"""決算発表された銘柄の X投稿下書き(WordPress) + コラム記事(Slack) を生成・配信

publish_for_ticker() が単一銘柄の処理をまとめる。morning_report.py から呼ばれる。
"""
from __future__ import annotations

import logging
from typing import Optional

from claude_writer import generate_column, generate_x_post
from finnhub_client import (
    EpsRecord,
    MarginRecord,
    RevenueRecord,
    fetch_eps_surprise,
    fetch_margin_history,
    fetch_quarterly_revenue,
)
from slack_poster import post_text_to_slack
from wordpress_client import create_draft_post, upload_media
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


# ---------- メイン処理 ----------
def publish_for_ticker(
    ticker: str,
    company_jp: str,
    fy_label: str,
    report_date: str,
    parent_thread_ts: str = "",
) -> None:
    """銘柄1つに対して: X card画像 + X投稿文 + コラム記事 → WP+Slack配信"""
    log.info(f"[{ticker}] publish_for_ticker 開始")

    # データ取得
    eps = fetch_eps_surprise(ticker, limit=8)
    revenue = fetch_quarterly_revenue(ticker, quarters=12)
    margins = fetch_margin_history(ticker, quarters=8)
    snapshot = fetch_market_snapshot(ticker)

    if not eps or not revenue:
        log.warning(f"[{ticker}] データ不足のためスキップ")
        return

    facts = _build_facts(ticker, company_jp, fy_label, report_date, eps, revenue, margins, snapshot)

    # X card 画像
    log.info(f"[{ticker}] X card 画像生成中...")
    xcard_path = build_x_card(ticker, company_jp, fy_label, eps[0], revenue, snapshot)
    log.info(f"  {xcard_path}")

    # X投稿文生成
    log.info(f"[{ticker}] X投稿文生成中 (Claude)...")
    try:
        x_text = generate_x_post(facts)
        log.info(f"  {len(x_text)}字: {x_text[:50]}...")
    except Exception as e:
        log.error(f"[{ticker}] X投稿文生成失敗: {e}")
        x_text = None

    # コラム記事生成
    log.info(f"[{ticker}] コラム記事生成中 (Claude)...")
    try:
        column_md = generate_column(facts)
        log.info(f"  {len(column_md)}字")
    except Exception as e:
        log.error(f"[{ticker}] コラム生成失敗: {e}")
        column_md = None

    # WordPress 下書き保存（X投稿テキスト + 画像）
    if x_text:
        try:
            log.info(f"[{ticker}] WordPressにメディアをアップロード...")
            media_id = upload_media(xcard_path, title=f"{company_jp} ({ticker}) {fy_label}")
            log.info(f"  media_id: {media_id}")
            # 本文は X投稿文 + 画像ショートコード（下書きから編集可）
            content = (
                f'<!-- wp:paragraph -->\n'
                f'<p>{x_text.replace(chr(10), "<br>")}</p>\n'
                f'<!-- /wp:paragraph -->\n'
                f'<!-- wp:image {{"id":{media_id}}} -->\n'
                f'<figure class="wp-block-image"><img src="" alt="" class="wp-image-{media_id}"/></figure>\n'
                f'<!-- /wp:image -->\n'
            )
            wp_title = f"【X下書き】{company_jp} ({ticker}) {fy_label}決算"
            post = create_draft_post(
                title=wp_title,
                content=content,
                featured_media_id=media_id,
                excerpt=x_text[:100],
            )
            log.info(f"  下書き作成: {post.get('link', post.get('id'))}")
            if parent_thread_ts:
                post_text_to_slack(
                    f":memo: {ticker}: WP下書き保存完了 → {post.get('link', '')} ({len(x_text)}字)",
                    thread_ts=parent_thread_ts,
                )
        except Exception as e:
            log.exception(f"[{ticker}] WP下書き保存失敗: {e}")
            if parent_thread_ts:
                post_text_to_slack(f":warning: {ticker}: WP下書き保存失敗 `{e}`", thread_ts=parent_thread_ts)

    # コラム記事を Slack に投稿
    if column_md:
        try:
            # Slack の1メッセージ上限 40,000字以内 (通常 2000字前後なので余裕)
            header = f":newspaper: *{company_jp} ({ticker}) {fy_label} コラム記事*"
            ts = post_text_to_slack(header, thread_ts=parent_thread_ts)
            # 本文はスレッド内の続投稿
            post_text_to_slack(column_md, thread_ts=ts or parent_thread_ts)
        except Exception as e:
            log.exception(f"[{ticker}] コラムSlack投稿失敗: {e}")

    log.info(f"[{ticker}] publish 完了")
