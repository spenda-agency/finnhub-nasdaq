"""Finnhub APIからのデータ取得

プロセス内メモ化 + 429リトライを組み込み、同じティッカーへの重複呼び出し
（Part1/Part2 → publish_combined_article の2巡）で無料枠（60req/min）を
食いつぶさないようにしている。
"""
import logging
import os
import time
from dataclasses import dataclass
from functools import lru_cache
from typing import Optional

import finnhub
from dotenv import load_dotenv

load_dotenv()
log = logging.getLogger("finnhub_client")

# 429 時のリトライ設定
_MAX_RETRIES = 2
_RETRY_SLEEP_SEC = 60  # 無料枠は 60 req/min のため 60秒スリープで回復


@dataclass
class EpsRecord:
    period: str       # 例 "2025-12-31"
    actual: float     # 実績EPS
    estimate: float   # コンセンサスEPS


@dataclass
class RevenueRecord:
    period: str       # 例 "2025-12-31"
    fiscal_label: str # 例 "FY25Q4"
    revenue: float    # 売上（ドル単位）


@dataclass
class CalendarEntry:
    date: str                          # YYYY-MM-DD
    symbol: str
    hour: str                          # bmo / amc / dmh
    quarter: int
    year: int
    eps_estimate: Optional[float]
    eps_actual: Optional[float]
    revenue_estimate: Optional[float]
    revenue_actual: Optional[float]

    @property
    def fiscal_label(self) -> str:
        return f"FY{str(self.year)[-2:]}Q{self.quarter}"


@dataclass
class MarginRecord:
    period: str
    fiscal_label: str
    revenue: float
    operating_income: Optional[float]
    net_income: Optional[float]

    @property
    def operating_margin(self) -> Optional[float]:
        if self.operating_income is None or not self.revenue:
            return None
        return self.operating_income / self.revenue * 100

    @property
    def net_margin(self) -> Optional[float]:
        if self.net_income is None or not self.revenue:
            return None
        return self.net_income / self.revenue * 100


def _client() -> finnhub.Client:
    api_key = os.getenv("FINNHUB_API_KEY")
    if not api_key:
        raise RuntimeError("FINNHUB_API_KEY が .env に設定されていません")
    return finnhub.Client(api_key=api_key)


def _call_with_retry(func, *args, **kwargs):
    """429 (rate limit) を受けたら _RETRY_SLEEP_SEC 秒待って最大 _MAX_RETRIES 回リトライ。"""
    attempt = 0
    while True:
        try:
            return func(*args, **kwargs)
        except finnhub.FinnhubAPIException as e:
            status = getattr(e, "status_code", None)
            if status == 429 and attempt < _MAX_RETRIES:
                attempt += 1
                log.warning(
                    f"Finnhub 429 (rate limit)。{_RETRY_SLEEP_SEC}秒待機してリトライ ({attempt}/{_MAX_RETRIES})"
                )
                time.sleep(_RETRY_SLEEP_SEC)
                continue
            raise


# ---------- 下位API呼び出し（メモ化付き） ----------
# 同じレスポンスを複数関数で共有するため、ティッカー単位で一度だけ取得する

@lru_cache(maxsize=256)
def _financials_reported_cached(ticker: str) -> dict:
    """financials_reported(quarterly) を1ティッカー1回だけ呼び出す。"""
    client = _client()
    log.debug(f"[{ticker}] financials_reported 取得")
    return _call_with_retry(client.financials_reported, symbol=ticker, freq="quarterly")


@lru_cache(maxsize=256)
def _company_profile_cached(ticker: str) -> dict:
    """company_profile2 を1ティッカー1回だけ呼び出す。"""
    client = _client()
    log.debug(f"[{ticker}] company_profile2 取得")
    try:
        return _call_with_retry(client.company_profile2, symbol=ticker) or {}
    except Exception as e:
        log.warning(f"[{ticker}] company_profile2 取得失敗: {e}")
        return {}


@lru_cache(maxsize=256)
def _company_earnings_cached(ticker: str, limit: int) -> tuple:
    """company_earnings を (ticker, limit) 単位でメモ化。lru_cache 用に tuple 化。"""
    client = _client()
    log.debug(f"[{ticker}] company_earnings 取得 (limit={limit})")
    data = _call_with_retry(client.company_earnings, ticker, limit=limit) or []
    # lru_cache 対象は hashable 必要。dict のまま tuple にまとめる
    return tuple(data)


def clear_caches() -> None:
    """テスト・再実行用: メモ化キャッシュを全クリア。"""
    _financials_reported_cached.cache_clear()
    _company_profile_cached.cache_clear()
    _company_earnings_cached.cache_clear()


# ---------- public API ----------
def fetch_eps_surprise(ticker: str, limit: int = 8) -> list[EpsRecord]:
    """最新の四半期EPS実績 vs コンセンサスを取得。新しい順で返す。"""
    data = _company_earnings_cached(ticker, limit)
    records = []
    for row in data:
        if row.get("actual") is None or row.get("estimate") is None:
            continue
        records.append(EpsRecord(
            period=row["period"],
            actual=float(row["actual"]),
            estimate=float(row["estimate"]),
        ))
    return records


def _find_revenue_in_report(report: dict) -> Optional[float]:
    """reported financialsのIC(income statement)から売上を探す。
    会社ごとにラベルが異なるため複数候補を試す。"""
    ic = report.get("report", {}).get("ic", [])
    if not ic:
        return None

    # 優先度順の候補ラベル
    candidates = [
        "Revenues",
        "Total revenues",
        "Revenue",
        "Net sales",
        "Total net sales",
        "Total revenue",
        "Sales",
    ]
    # labelで厳密一致を優先
    by_label = {item.get("label", "").strip(): item for item in ic if "value" in item}
    for key in candidates:
        if key in by_label and by_label[key].get("value") is not None:
            return float(by_label[key]["value"])

    # コンセプト名でのフォールバック
    concepts = {item.get("concept", ""): item for item in ic if "value" in item}
    for c in [
        "us-gaap_Revenues",
        "us-gaap_RevenueFromContractWithCustomerExcludingAssessedTax",
        "us-gaap_SalesRevenueNet",
    ]:
        if c in concepts and concepts[c].get("value") is not None:
            return float(concepts[c]["value"])

    return None


def _find_ic_value(report: dict, label_candidates: list[str], concept_candidates: list[str]) -> Optional[float]:
    """損益計算書(IC)から指定ラベル/コンセプトに合致する値を探す共通関数。"""
    ic = report.get("report", {}).get("ic", [])
    if not ic:
        return None

    by_label = {item.get("label", "").strip(): item for item in ic if "value" in item}
    for key in label_candidates:
        if key in by_label and by_label[key].get("value") is not None:
            return float(by_label[key]["value"])

    concepts = {item.get("concept", ""): item for item in ic if "value" in item}
    for c in concept_candidates:
        if c in concepts and concepts[c].get("value") is not None:
            return float(concepts[c]["value"])

    return None


def _find_operating_income(report: dict) -> Optional[float]:
    return _find_ic_value(
        report,
        label_candidates=[
            "Operating income (loss)",
            "Income from operations",
            "Operating income",
            "Operating profit",
        ],
        concept_candidates=["us-gaap_OperatingIncomeLoss"],
    )


def _find_net_income(report: dict) -> Optional[float]:
    return _find_ic_value(
        report,
        label_candidates=[
            "Net income (loss)",
            "Net income",
            "Net income attributable to common stockholders",
            "Net earnings",
        ],
        concept_candidates=["us-gaap_NetIncomeLoss", "us-gaap_ProfitLoss"],
    )


def fetch_quarterly_revenue(ticker: str, quarters: int = 12) -> list[RevenueRecord]:
    """四半期売上のヒストリカルを新しい順→古い順に並べ替えて返す。"""
    data = _financials_reported_cached(ticker)
    reports = data.get("data", [])

    records = []
    for rep in reports:
        revenue = _find_revenue_in_report(rep)
        if revenue is None:
            continue
        year = rep.get("year")
        quarter = rep.get("quarter")
        period = rep.get("endDate", "")
        if not year or not quarter:
            continue
        # FYラベル (例: FY25Q4) — 暦年ベース。後で微調整可能
        fy_label = f"FY{str(year)[-2:]}Q{quarter}"
        records.append(RevenueRecord(
            period=period,
            fiscal_label=fy_label,
            revenue=revenue,
        ))

    # 古い順にソートし、末尾から quarters 件を採用
    records.sort(key=lambda r: r.period)
    return records[-quarters:]


def fetch_earnings_calendar(from_date: str, to_date: str) -> list[CalendarEntry]:
    """Finnhub earnings calendar を取得して CalendarEntry のリストで返す。
    日付は ISO形式 'YYYY-MM-DD'。"""
    client = _client()
    data = _call_with_retry(
        client.earnings_calendar,
        _from=from_date, to=to_date, symbol="", international=False,
    )
    raw = data.get("earningsCalendar", []) or []
    out = []
    for e in raw:
        if not e.get("symbol"):
            continue
        out.append(CalendarEntry(
            date=e.get("date", ""),
            symbol=e["symbol"],
            hour=e.get("hour", ""),
            quarter=e.get("quarter", 0) or 0,
            year=e.get("year", 0) or 0,
            eps_estimate=e.get("epsEstimate"),
            eps_actual=e.get("epsActual"),
            revenue_estimate=e.get("revenueEstimate"),
            revenue_actual=e.get("revenueActual"),
        ))
    return out


def fetch_previous_quarter_actuals(ticker: str, lookback_days: int = 120) -> Optional[CalendarEntry]:
    """前期(発表済み)のCalendarEntryを返す。Calendarデータは確実に四半期ベース。"""
    from datetime import date, timedelta
    client = _client()
    today = date.today()
    start = today - timedelta(days=lookback_days)
    data = _call_with_retry(
        client.earnings_calendar,
        _from=start.isoformat(), to=today.isoformat(),
        symbol=ticker, international=False,
    )
    raw = data.get("earningsCalendar", []) or []
    reported = [
        r for r in raw
        if r.get("revenueActual") is not None or r.get("epsActual") is not None
    ]
    if not reported:
        return None
    reported.sort(key=lambda r: r.get("date", ""))
    latest = reported[-1]
    return CalendarEntry(
        date=latest.get("date", ""),
        symbol=latest.get("symbol", ticker),
        hour=latest.get("hour", ""),
        quarter=latest.get("quarter", 0) or 0,
        year=latest.get("year", 0) or 0,
        eps_estimate=latest.get("epsEstimate"),
        eps_actual=latest.get("epsActual"),
        revenue_estimate=latest.get("revenueEstimate"),
        revenue_actual=latest.get("revenueActual"),
    )


def fetch_market_cap(ticker: str) -> Optional[float]:
    """時価総額（ドル単位）。Finnhub の marketCapitalization は百万ドル単位。"""
    profile = _company_profile_cached(ticker)
    mc = profile.get("marketCapitalization")
    return float(mc) * 1e6 if mc else None


def fetch_company_name(ticker: str) -> Optional[str]:
    """会社名（英語）を取得"""
    profile = _company_profile_cached(ticker)
    return profile.get("name") or None


def fetch_margin_history(ticker: str, quarters: int = 8) -> list[MarginRecord]:
    """四半期ごとの売上・営業利益・純利益を取得（利益率計算用）。"""
    data = _financials_reported_cached(ticker)
    reports = data.get("data", [])

    records = []
    for rep in reports:
        revenue = _find_revenue_in_report(rep)
        if revenue is None:
            continue
        year = rep.get("year")
        quarter = rep.get("quarter")
        period = rep.get("endDate", "")
        if not year or not quarter:
            continue
        records.append(MarginRecord(
            period=period,
            fiscal_label=f"FY{str(year)[-2:]}Q{quarter}",
            revenue=revenue,
            operating_income=_find_operating_income(rep),
            net_income=_find_net_income(rep),
        ))

    records.sort(key=lambda r: r.period)
    return records[-quarters:]
