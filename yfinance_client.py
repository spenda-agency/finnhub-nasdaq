"""yfinance 経由の市場データ取得（株価・アナリスト推奨・目標株価・決算反応）

プロセス内メモ化あり: 同一ティッカーへの `fetch_market_snapshot` / `fetch_previous_quarter_revenue`
は初回のみ yfinance を叩き、2回目以降は結果を使い回す。
"""
from dataclasses import dataclass, field
from datetime import timedelta
from functools import lru_cache
from typing import Optional

import yfinance as yf


@dataclass
class PriceTarget:
    mean: Optional[float] = None
    high: Optional[float] = None
    low: Optional[float] = None
    median: Optional[float] = None
    num_analysts: Optional[int] = None


@dataclass
class AnalystRecommendation:
    strong_buy: int = 0
    buy: int = 0
    hold: int = 0
    sell: int = 0
    strong_sell: int = 0

    @property
    def total(self) -> int:
        return self.strong_buy + self.buy + self.hold + self.sell + self.strong_sell


@dataclass
class MarketSnapshot:
    ticker: str
    current_price: Optional[float] = None
    previous_close: Optional[float] = None
    last_earnings_date: Optional[str] = None   # YYYY-MM-DD
    earnings_reaction_pct: Optional[float] = None  # 決算発表前後の株価変化率
    target: PriceTarget = field(default_factory=PriceTarget)
    recommendation: AnalystRecommendation = field(default_factory=AnalystRecommendation)

    @property
    def upside_pct(self) -> Optional[float]:
        if self.current_price and self.target.mean:
            return (self.target.mean - self.current_price) / self.current_price * 100
        return None


def clear_caches() -> None:
    """テスト・再実行用: yfinance 側メモ化キャッシュを全クリア。"""
    fetch_previous_quarter_revenue.cache_clear()
    fetch_market_snapshot.cache_clear()


@lru_cache(maxsize=256)
def fetch_previous_quarter_revenue(ticker: str) -> Optional[float]:
    """yfinance から前期(直近発表済み四半期)の売上を取得（ドル）"""
    try:
        t = yf.Ticker(ticker)
        qis = t.quarterly_income_stmt
        if qis is None or qis.empty:
            return None
        # 行ラベル候補: 'Total Revenue', 'Revenue', 'Operating Revenue'
        for label in ["Total Revenue", "Revenue", "Operating Revenue", "Net Revenue"]:
            if label in qis.index:
                series = qis.loc[label]
                if not series.empty and series.iloc[0] is not None:
                    return float(series.iloc[0])
        return None
    except Exception as e:
        print(f"  warn: yfinance prev revenue取得失敗 ({ticker}): {e}")
        return None


@lru_cache(maxsize=256)
def fetch_market_snapshot(ticker: str) -> MarketSnapshot:
    """yfinance から市場データ一式を取得。失敗した項目は None のまま残す。"""
    snap = MarketSnapshot(ticker=ticker)
    t = yf.Ticker(ticker)

    # --- .info から: 現在株価・目標株価・アナリスト人数 ---
    try:
        info = t.info
        snap.current_price = info.get("currentPrice") or info.get("regularMarketPrice")
        snap.previous_close = info.get("previousClose")
        snap.target.mean = info.get("targetMeanPrice")
        snap.target.high = info.get("targetHighPrice")
        snap.target.low = info.get("targetLowPrice")
        snap.target.median = info.get("targetMedianPrice")
        snap.target.num_analysts = info.get("numberOfAnalystOpinions")
    except Exception as e:
        print(f"  warn: info取得失敗 {e}")

    # --- アナリスト推奨 ---
    try:
        rec_df = t.recommendations
        if rec_df is not None and not rec_df.empty:
            # 直近期間 (period='0m' が当月)
            if "period" in rec_df.columns:
                latest = rec_df[rec_df["period"] == "0m"]
                if latest.empty:
                    latest = rec_df.iloc[[0]]
            else:
                latest = rec_df.iloc[[0]]
            row = latest.iloc[0]
            snap.recommendation.strong_buy = int(row.get("strongBuy", 0) or 0)
            snap.recommendation.buy = int(row.get("buy", 0) or 0)
            snap.recommendation.hold = int(row.get("hold", 0) or 0)
            snap.recommendation.sell = int(row.get("sell", 0) or 0)
            snap.recommendation.strong_sell = int(row.get("strongSell", 0) or 0)
    except Exception as e:
        print(f"  warn: recommendations取得失敗 {e}")

    # --- 最新決算発表日 & 株価反応 ---
    try:
        ed = t.earnings_dates
        if ed is not None and not ed.empty:
            # "Reported EPS" が入っている＝発表済み
            past = ed[ed["Reported EPS"].notna()] if "Reported EPS" in ed.columns else ed
            if not past.empty:
                last_ts = past.index[0]
                last_date = last_ts.date() if hasattr(last_ts, "date") else last_ts
                snap.last_earnings_date = last_date.isoformat()

                # 発表日前後の株価推移を取得
                hist = t.history(
                    start=last_date - timedelta(days=7),
                    end=last_date + timedelta(days=7),
                )
                if not hist.empty:
                    before = hist[hist.index.date < last_date]
                    after = hist[hist.index.date > last_date]
                    if not before.empty and not after.empty:
                        price_before = float(before["Close"].iloc[-1])
                        price_after = float(after["Close"].iloc[0])
                        snap.earnings_reaction_pct = (price_after - price_before) / price_before * 100
    except Exception as e:
        print(f"  warn: earnings_dates取得失敗 {e}")

    return snap
