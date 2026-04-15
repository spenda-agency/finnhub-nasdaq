"""決算サマリー生成 & Slack投稿 のエントリポイント

使い方:
    python main.py                       # config.TICKERS 全社、Part 1 + Part 2 両方
    python main.py GOOG                  # 1社のみ
    python main.py GOOG --no-slack       # Slack投稿スキップ（ローカル保存のみ）
    python main.py GOOG --part 1         # Part 1 のみ
    python main.py GOOG --part 2         # Part 2 のみ
"""
import argparse
import sys
from pathlib import Path

from config import HISTORICAL_QUARTERS, TICKERS
from chart_builder import build_chart, build_chart_part2
from finnhub_client import (
    fetch_eps_surprise,
    fetch_margin_history,
    fetch_quarterly_revenue,
)


def process_ticker(ticker: str, company_jp: str, post_slack: bool, parts: list[int]) -> list[Path]:
    print(f"[{ticker}] {company_jp} — データ取得中...")
    eps = fetch_eps_surprise(ticker, limit=8)
    revenue = fetch_quarterly_revenue(ticker, quarters=HISTORICAL_QUARTERS)
    print(f"  EPS: {len(eps)}件 / 売上: {len(revenue)}件")

    latest = revenue[-1]
    fy_label = latest.fiscal_label
    chart_paths = []

    if 1 in parts:
        print(f"[{ticker}] Part 1 生成中...")
        path1 = build_chart(ticker, company_jp, eps, revenue)
        print(f"  保存: {path1}")
        chart_paths.append((path1, f"*{company_jp}（{ticker}）{fy_label}決算 Part 1*"))

    if 2 in parts:
        print(f"[{ticker}] Part 2 データ取得中（yfinance + 利益率）...")
        from yfinance_client import fetch_market_snapshot
        snapshot = fetch_market_snapshot(ticker)
        margins = fetch_margin_history(ticker, quarters=8)
        print(f"  現在株価: ${snapshot.current_price} / アナリスト: {snapshot.target.num_analysts}名 / 利益率: {len(margins)}件")

        print(f"[{ticker}] Part 2 生成中...")
        path2 = build_chart_part2(ticker, company_jp, eps, margins, snapshot, fy_label)
        print(f"  保存: {path2}")
        chart_paths.append((path2, f"*{company_jp}（{ticker}）{fy_label}決算 Part 2（詳細分析）*"))

    if post_slack:
        from slack_poster import post_chart_to_slack
        for path, caption in chart_paths:
            post_chart_to_slack(path, caption)

    return [p for p, _ in chart_paths]


def main() -> int:
    parser = argparse.ArgumentParser(description="決算サマリー生成 & Slack投稿")
    parser.add_argument("tickers", nargs="*", help="ティッカー (省略時は config.TICKERS 全社)")
    parser.add_argument("--no-slack", action="store_true", help="Slack投稿をスキップ")
    parser.add_argument("--part", type=int, choices=[1, 2], help="Partを1つだけ生成 (指定なしなら両方)")
    args = parser.parse_args()

    targets = args.tickers or list(TICKERS.keys())
    post_slack = not args.no_slack
    parts = [args.part] if args.part else [1, 2]

    errors = []
    for ticker in targets:
        company_jp = TICKERS.get(ticker, ticker)
        try:
            process_ticker(ticker, company_jp, post_slack, parts)
        except Exception as e:
            print(f"  NG {ticker} エラー: {e}", file=sys.stderr)
            errors.append((ticker, str(e)))

    print()
    print(f"完了: {len(targets) - len(errors)}/{len(targets)} 成功")
    if errors:
        for ticker, msg in errors:
            print(f"  - {ticker}: {msg}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
