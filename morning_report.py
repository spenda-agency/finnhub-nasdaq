"""毎朝のレポート

セクション:
1. 今後3日の主要決算予定 (Revenue Consensus > $10B)
2. 直近24時間に発表された決算 (Revenue > $10B) → Part 1 / Part 2 を生成

火-土の朝8時に Windows Task Scheduler から起動される想定。
手動実行: python morning_report.py
"""
from __future__ import annotations

import io
import logging
import sys
from datetime import date, timedelta
from pathlib import Path

# Windows コンソールでも UTF-8 出力できるように強制
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

from finnhub_client import (
    CalendarEntry,
    fetch_company_name,
    fetch_earnings_calendar,
    fetch_eps_surprise,
    fetch_market_cap,
)
from slack_poster import post_text_to_slack
from main import process_ticker
from yfinance_client import fetch_previous_quarter_revenue

REVENUE_THRESHOLD = 10e9  # $10B
LOG_DIR = Path(__file__).parent / "logs"
LOG_DIR.mkdir(exist_ok=True)


# ---------- ロガー設定 ----------
def _setup_logger() -> logging.Logger:
    today = date.today().isoformat()
    log_file = LOG_DIR / f"morning_{today}.log"
    handlers = [
        logging.FileHandler(log_file, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ]
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s | %(message)s",
        handlers=handlers,
    )
    return logging.getLogger("morning_report")


log = _setup_logger()


# ---------- フォーマッタ ----------
def _fmt_b(v: float | None) -> str:
    if v is None:
        return "N/A"
    if v >= 1e12:
        return f"${v / 1e12:.2f}T"
    if v >= 1e9:
        return f"${v / 1e9:.1f}B"
    if v >= 1e6:
        return f"${v / 1e6:.0f}M"
    return f"${v:.0f}"


def _fmt_eps(v: float | None) -> str:
    return f"${v:.2f}" if v is not None else "N/A"


# ---------- セクション1: 今後の予定 ----------
def build_upcoming_section() -> str:
    today = date.today()
    end = today + timedelta(days=3)
    log.info(f"今後の決算予定取得: {today} → {end}")
    entries = fetch_earnings_calendar(today.isoformat(), end.isoformat())

    upcoming = [
        e for e in entries
        if e.revenue_estimate and e.revenue_estimate > REVENUE_THRESHOLD
        and e.revenue_actual is None
    ]
    upcoming.sort(key=lambda e: (e.date, e.symbol))
    log.info(f"フィルタ後 (Rev予想>$10B & 未発表): {len(upcoming)}件")

    header = f":calendar: *今後3日の主要決算予定* (Revenue Consensus > $10B)"
    if not upcoming:
        return f"{header}\n_該当銘柄なし_"

    # 各銘柄の前期実績・時価総額を取得
    # - 前期EPS: Finnhub /stock/earnings (8四半期分。最新が前期に相当)
    # - 前期Revenue: yfinance quarterly_income_stmt (Finnhub calendar無料枠は30日のみのため)
    # - 時価総額: Finnhub /stock/profile2
    lines = [f"{header} — {len(upcoming)}件\n```"]
    for e in upcoming:
        try:
            eps_hist = fetch_eps_surprise(e.symbol, limit=1)
            prev_eps = eps_hist[0].actual if eps_hist else None
        except Exception:
            prev_eps = None
        try:
            prev_rev = fetch_previous_quarter_revenue(e.symbol)
        except Exception:
            prev_rev = None
        try:
            mc = fetch_market_cap(e.symbol)
        except Exception:
            mc = None

        line = (
            f"🇺🇸 {e.symbol:<6} "
            f"| EPS予想 {_fmt_eps(e.eps_estimate):>7} (前期 {_fmt_eps(prev_eps):>7}) "
            f"| Rev予想 {_fmt_b(e.revenue_estimate):>7} (前期 {_fmt_b(prev_rev):>7}) "
            f"| MCap {_fmt_b(mc):>7} "
            f"| {e.fiscal_label} {e.date[5:]} {e.hour or '?':<3}"
        )
        lines.append(line)
    lines.append("```")
    return "\n".join(lines)


# ---------- セクション2: 直近24時間の決算 → Part1/2 ----------
def get_recent_reported_tickers() -> list[CalendarEntry]:
    """前営業日 amc 〜 当日 bmo に発表され、Revenue Actual > $10B の銘柄を返す。"""
    today = date.today()
    yesterday = today - timedelta(days=1)
    # 週末をスキップする最小ロジック: 月曜なら金曜まで遡る
    weekday = today.weekday()  # 0=Mon
    if weekday == 0:  # Monday → cover Fri amc + Mon bmo
        start = today - timedelta(days=3)
    else:
        start = yesterday

    log.info(f"直近24時間カバー範囲: {start} → {today}")
    entries = fetch_earnings_calendar(start.isoformat(), today.isoformat())

    # 発表済み(actual有) かつ Revenue > 10B
    reported = [
        e for e in entries
        if e.revenue_actual is not None
        and e.revenue_actual > REVENUE_THRESHOLD
        and start.isoformat() <= e.date <= today.isoformat()
    ]
    # 重複ティッカーを最新分のみに
    by_symbol: dict[str, CalendarEntry] = {}
    for e in sorted(reported, key=lambda x: x.date):
        by_symbol[e.symbol] = e
    result = list(by_symbol.values())
    result.sort(key=lambda x: (x.date, x.symbol))
    log.info(f"フィルタ後 (Rev実績>$10B): {len(result)}件 → {[e.symbol for e in result]}")
    return result


def run_part1_part2_for(ticker: str, parent_thread_ts: str = "") -> None:
    """個別銘柄に Part1+Part2 を生成して Slack投稿"""
    name = fetch_company_name(ticker) or ticker
    log.info(f"[{ticker}] {name} - Part1/Part2 生成開始")
    try:
        process_ticker(ticker, name, post_slack=True, parts=[1, 2])
    except Exception as e:
        log.error(f"[{ticker}] エラー: {e}")
        if parent_thread_ts:
            post_text_to_slack(f":warning: {ticker} の生成失敗: `{e}`", thread_ts=parent_thread_ts)


# ---------- メイン ----------
def main() -> int:
    log.info("=" * 60)
    log.info(f"morning_report 開始 ({date.today()})")
    log.info("=" * 60)

    # Section 1
    try:
        upcoming_msg = build_upcoming_section()
        post_text_to_slack(upcoming_msg)
    except Exception as e:
        log.exception(f"Section 1 エラー: {e}")
        post_text_to_slack(f":warning: Section1(今後の予定)取得失敗: `{e}`")

    # Section 2
    try:
        recent = get_recent_reported_tickers()
    except Exception as e:
        log.exception(f"Section 2 取得エラー: {e}")
        post_text_to_slack(f":warning: Section2(直近決算)取得失敗: `{e}`")
        return 1

    if not recent:
        post_text_to_slack(":bar_chart: *直近24時間の決算 (Revenue > $10B)*\n_該当銘柄なし_")
        log.info("該当なし。終了。")
        return 0

    summary = (
        f":bar_chart: *直近24時間の決算 (Revenue > $10B) — {len(recent)}件*\n"
        f"以下の銘柄について Part 1 / Part 2 を順次投稿します:\n"
        f"`{', '.join(e.symbol for e in recent)}`"
    )
    parent_ts = post_text_to_slack(summary)

    for entry in recent:
        run_part1_part2_for(entry.symbol, parent_thread_ts=parent_ts)

    log.info("morning_report 完了")
    return 0


if __name__ == "__main__":
    sys.exit(main())
