"""決算発表された銘柄の X投稿下書き(WordPress + drafts/) + コラム記事(Slack) を生成・配信

WP・X下書き・コラム記事は全て1日1本に集約する（銘柄ごとには作らない）。
morning_report.py から publish_combined_article() が呼ばれる。
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from claude_writer import generate_aggregated_x_post, generate_combined_column
from finnhub_client import (
    EpsRecord,
    MarginRecord,
    RevenueRecord,
    fetch_eps_surprise,
    fetch_margin_history,
    fetch_quarterly_revenue,
)
from slack_poster import post_text_to_slack
from wordpress_client import create_draft_post, upload_media, verify_credentials
from x_card_builder import build_x_card
from yfinance_client import MarketSnapshot, fetch_market_snapshot

log = logging.getLogger("publish_report")


# ---------- データを"事実辞書"に整形 ----------
def _fmt_b(v: Optional[float]) -> str:
    if v is None:
        return "N/A"
    if abs(v) >= 1e12:
        return f"${v / 1e12:.2f}T"
    if abs(v) >= 1e9:
        return f"${v / 1e9:.1f}B"
    if abs(v) >= 1e6:
        return f"${v / 1e6:.0f}M"
    return f"${v:.0f}"


def _fmt_eps(v: Optional[float]) -> str:
    return f"${v:.2f}" if v is not None else "N/A"


def _signed_pct(v: Optional[float]) -> str:
    if v is None:
        return "N/A"
    sign = "+" if v >= 0 else ""
    return f"{sign}{v:.1f}%"


def _build_facts(
    ticker: str,
    company_jp: str,
    fy_label: str,
    report_date: str,
    eps_records: list[EpsRecord],
    revenue_records: list[RevenueRecord],
    margin_records: list[MarginRecord],
    snapshot: MarketSnapshot,
) -> dict:
    latest_eps = eps_records[0]
    latest_rev = revenue_records[-1]
    latest_margin = margin_records[-1] if margin_records else None

    # YoY
    yoy = None
    if len(revenue_records) >= 5:
        prev_y = revenue_records[-5].revenue
        if prev_y:
            yoy = (latest_rev.revenue / prev_y - 1) * 100

    # サプライズ（コンセンサス - actual は売上はデータなし。EPSのみ）
    eps_surprise = None
    if latest_eps.estimate:
        eps_surprise = (latest_eps.actual - latest_eps.estimate) / abs(latest_eps.estimate) * 100

    # 過去8Q履歴テキスト
    hist_lines = [f"- {r.fiscal_label}: {_fmt_b(r.revenue)}" for r in revenue_records[-8:]]

    rec = snapshot.recommendation
    return {
        "ticker": ticker,
        "company": company_jp,
        "fy_label": fy_label,
        "report_date": report_date,
        "revenue_actual_str": _fmt_b(latest_rev.revenue),
        "revenue_estimate_str": "N/A (無料枠)",
        "revenue_surprise_str": "N/A",
        "eps_actual_str": _fmt_eps(latest_eps.actual),
        "eps_estimate_str": _fmt_eps(latest_eps.estimate),
        "eps_surprise_str": _signed_pct(eps_surprise),
        "yoy_str": _signed_pct(yoy),
        "op_margin_str": _signed_pct(latest_margin.operating_margin) if latest_margin else "N/A",
        "net_margin_str": _signed_pct(latest_margin.net_margin) if latest_margin else "N/A",
        "current_price_str": f"${snapshot.current_price:.2f}" if snapshot.current_price else "N/A",
        "reaction_str": _signed_pct(snapshot.earnings_reaction_pct),
        "target_mean_str": f"${snapshot.target.mean:.2f}" if snapshot.target.mean else "N/A",
        "target_high_str": f"${snapshot.target.high:.2f}" if snapshot.target.high else "N/A",
        "target_low_str": f"${snapshot.target.low:.2f}" if snapshot.target.low else "N/A",
        "upside_str": _signed_pct(snapshot.upside_pct),
        "strong_buy": rec.strong_buy,
        "buy": rec.buy,
        "hold": rec.hold,
        "sell": rec.sell,
        "strong_sell": rec.strong_sell,
        "total_analysts": rec.total,
        "revenue_history_str": "\n".join(hist_lines),
    }


# ---------- 全銘柄まとめ記事の生成・配信 ----------
def publish_combined_article(
    ticker_data_list: list[dict],
    report_date: str,
    parent_thread_ts: str = "",
    upcoming_entries: list[dict] | None = None,
) -> None:
    """全銘柄の決算結果を1つの記事にまとめて WP下書き + Slack 配信

    ticker_data_list: 各銘柄の dict (ticker, company_jp, fy_label, report_date を含む)
    """
    log.info(f"まとめ記事生成開始: {len(ticker_data_list)}銘柄")

    # 各銘柄のデータを取得して facts リストとチャート画像のみ収集
    # （個別のX投稿文は生成しない：集約版1本に統一）
    all_facts: list[dict] = []
    all_xcard_paths: list[tuple] = []  # (path, ticker, company_jp)

    for td in ticker_data_list:
        ticker = td["ticker"]
        company_jp = td["company_jp"]
        fy_label = td["fy_label"]
        entry_date = td["report_date"]

        log.info(f"[{ticker}] データ取得中...")
        try:
            eps = fetch_eps_surprise(ticker, limit=8)
            revenue = fetch_quarterly_revenue(ticker, quarters=12)
            margins = fetch_margin_history(ticker, quarters=8)
            snapshot = fetch_market_snapshot(ticker)
        except Exception as e:
            log.error(f"[{ticker}] データ取得失敗: {e}")
            continue

        if not eps or not revenue:
            log.warning(f"[{ticker}] データ不足のためスキップ")
            continue

        facts = _build_facts(ticker, company_jp, fy_label, entry_date, eps, revenue, margins, snapshot)
        all_facts.append(facts)

        # X card 画像 (WP本文内のチャート一覧に使用)
        try:
            xcard_path = build_x_card(ticker, company_jp, fy_label, eps[0], revenue, snapshot)
            all_xcard_paths.append((xcard_path, ticker, company_jp))
            log.info(f"  X card: {xcard_path}")
        except Exception as e:
            log.error(f"[{ticker}] X card 生成失敗: {e}")

    if not all_facts:
        log.warning("有効な銘柄データなし。まとめ記事生成をスキップ")
        return

    # 集約版 X投稿文（全銘柄を1ポストに集約・140字以内）を生成して drafts/ に保存
    aggregated_x: Optional[str] = None
    try:
        log.info("集約X投稿文 (140字) 生成中 (Claude)...")
        aggregated_x = generate_aggregated_x_post(all_facts, report_date)
        log.info(f"  集約X投稿文: {len(aggregated_x)}字")
        drafts_dir = Path(__file__).parent / "drafts"
        drafts_dir.mkdir(exist_ok=True)
        draft_path = drafts_dir / f"{report_date}.txt"
        draft_path.write_text(aggregated_x + "\n", encoding="utf-8")
        log.info(f"  X下書き保存: {draft_path}")
        if parent_thread_ts:
            tickers_str = ", ".join(f['ticker'] for f in all_facts)
            post_text_to_slack(
                f":pencil2: *X下書き* ({len(aggregated_x)}字 / {tickers_str})\n"
                f"`drafts/{report_date}.txt` に保存（リポジトリにコミットされます）\n"
                f"```\n{aggregated_x}\n```",
                thread_ts=parent_thread_ts,
            )
    except Exception as e:
        log.exception(f"集約X投稿文生成失敗: {e}")
        if parent_thread_ts:
            post_text_to_slack(f":warning: 集約X投稿文の生成失敗: `{e}`", thread_ts=parent_thread_ts)

    # WordPress: 1日分の内容を集約した1記事 (コラム + 集約X下書き + チャート一覧)
    tickers_str = ", ".join(f['ticker'] for f in all_facts)
    wp_title = f"{report_date} Nasdaq決算速報まとめ（{tickers_str}）"
    try:
        # まとめコラム記事生成 (Claude) — 全銘柄を1本のコラムに集約
        log.info("まとめコラム記事生成中 (Claude)...")
        column_md = generate_combined_column(all_facts, upcoming_entries=upcoming_entries)
        log.info(f"  まとめコラム: {len(column_md)}字")
    except Exception as e:
        log.exception(f"まとめコラム生成失敗: {e}")
        column_md = None

    # WordPress 下書き保存
    try:
        log.info("WP 認証・権限の事前チェック中...")
        try:
            verify_credentials()
        except Exception as cred_err:
            # 認証・権限エラーはログに詳細を残しつつ Slack にも要点だけ通知
            log.exception(f"WP 認証/権限エラー: {cred_err}")
            if parent_thread_ts:
                post_text_to_slack(
                    f":warning: WP下書きスキップ（認証/権限エラー）\n```{cred_err}```",
                    thread_ts=parent_thread_ts,
                )
            raise
        log.info("WordPressにまとめ記事（集約版）を下書き保存中...")
        content_blocks = []

        # 1. コラム本文（全銘柄を集約した1本）
        if column_md:
            # Markdown → HTML 簡易変換 (WP は Gutenberg なのでそのまま paragraph ブロック)
            content_blocks.append(
                f'<!-- wp:paragraph -->\n'
                f'<p>{column_md.replace(chr(10), "<br>")}</p>\n'
                f'<!-- /wp:paragraph -->\n'
            )

        # 2. 本日のX投稿（140字集約版） — aggregated_x は上で生成済みの場合のみ
        if aggregated_x:
            content_blocks.append(
                f'<!-- wp:heading -->\n'
                f'<h2>本日のX投稿（140字集約版）</h2>\n'
                f'<!-- /wp:heading -->\n'
                f'<!-- wp:paragraph -->\n'
                f'<p>{aggregated_x.replace(chr(10), "<br>")}</p>\n'
                f'<!-- /wp:paragraph -->\n'
            )

        # 3. 各銘柄のチャート一覧（画像のみ、個別の見出しや個別X投稿文は付けない）
        if all_xcard_paths:
            content_blocks.append(
                f'<!-- wp:heading -->\n'
                f'<h2>銘柄別チャート</h2>\n'
                f'<!-- /wp:heading -->\n'
            )
            for xcard_path, ticker, company_jp in all_xcard_paths:
                media_id = upload_media(xcard_path, title=f"{company_jp} ({ticker})")
                content_blocks.append(
                    f'<!-- wp:image {{"id":{media_id}}} -->\n'
                    f'<figure class="wp-block-image">'
                    f'<img src="" alt="{company_jp} ({ticker})" class="wp-image-{media_id}"/>'
                    f'<figcaption>{company_jp} (${ticker})</figcaption>'
                    f'</figure>\n'
                    f'<!-- /wp:image -->\n'
                )

        featured_media = None
        if all_xcard_paths:
            # 最初の X card をアイキャッチに
            featured_media = upload_media(all_xcard_paths[0][0], title=wp_title)

        post = create_draft_post(
            title=wp_title,
            content="\n".join(content_blocks),
            featured_media_id=featured_media,
            excerpt=f"Nasdaq主要銘柄の決算速報を1本にまとめたレポート（{tickers_str}）",
        )
        log.info(f"  まとめ下書き作成: {post.get('link', post.get('id'))}")
        if parent_thread_ts:
            post_text_to_slack(
                f":newspaper: まとめ記事WP下書き保存完了（1日分集約 / {len(all_facts)}銘柄） → {post.get('link', '')}",
                thread_ts=parent_thread_ts,
            )
    except Exception as e:
        log.exception(f"WPまとめ下書き保存失敗: {e}")
        if parent_thread_ts:
            post_text_to_slack(f":warning: まとめ記事WP下書き保存失敗: `{e}`", thread_ts=parent_thread_ts)

    # Slack にまとめコラム投稿
    if column_md:
        try:
            tickers_str = ", ".join(f['ticker'] for f in all_facts)
            header = f":newspaper: *{report_date} Nasdaq決算直後結果と決算直前の詳細* ({tickers_str})"
            ts = post_text_to_slack(header, thread_ts=parent_thread_ts)
            post_text_to_slack(column_md, thread_ts=ts or parent_thread_ts)
        except Exception as e:
            log.exception(f"まとめコラムSlack投稿失敗: {e}")

    log.info("まとめ記事 publish 完了")
