"""X(Twitter) 投稿用 16:9 サマリーカード画像生成 (1200x675)"""
from pathlib import Path
from typing import Optional

import matplotlib
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

# 日本語フォント設定（chart_builder と同じ）
matplotlib.rcParams["font.family"] = [
    "Yu Gothic", "Meiryo", "MS Gothic",
    "Noto Sans CJK JP", "Noto Sans JP", "IPAGothic", "VL Gothic",
    "Hiragino Sans",
    "sans-serif",
]
matplotlib.rcParams["axes.unicode_minus"] = False

from config import OUTPUT_DIR
from finnhub_client import EpsRecord, RevenueRecord
from yfinance_client import MarketSnapshot

COLOR_BG = "#FFFFFF"
COLOR_ACCENT = "#1F4E79"
COLOR_BAR = "#F4A460"
COLOR_POSITIVE = "#2E7D32"
COLOR_NEGATIVE = "#C62828"
COLOR_NEUTRAL = "#555555"


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


def _signed(v: Optional[float]) -> tuple[str, str]:
    if v is None:
        return "N/A", COLOR_NEUTRAL
    sign = "+" if v >= 0 else ""
    color = COLOR_POSITIVE if v >= 0 else COLOR_NEGATIVE
    return f"{sign}{v:.1f}%", color


def _draw_stat(ax, label: str, value: str, sub: str, value_color: str = "#111"):
    ax.axis("off")
    ax.text(0.5, 0.78, label, ha="center", va="center", fontsize=13, color="#555", transform=ax.transAxes)
    ax.text(0.5, 0.45, value, ha="center", va="center", fontsize=26, fontweight="bold", color=value_color, transform=ax.transAxes)
    ax.text(0.5, 0.15, sub, ha="center", va="center", fontsize=11, color="#888", transform=ax.transAxes)


def build_x_card(
    ticker: str,
    company_jp: str,
    fy_label: str,
    eps_latest: EpsRecord,
    revenue_records: list[RevenueRecord],
    snapshot: MarketSnapshot,
) -> Path:
    """X投稿用 1200x675 (16:9) のサマリーカードを生成"""
    latest_rev = revenue_records[-1]
    # YoY
    yoy = None
    if len(revenue_records) >= 5:
        prev_y = revenue_records[-5].revenue
        if prev_y:
            yoy = (latest_rev.revenue / prev_y - 1) * 100

    # Figure: 1200x675 @ 150dpi = 8.0 x 4.5 inch
    fig = plt.figure(figsize=(8.0, 4.5), facecolor=COLOR_BG, dpi=150)

    gs = fig.add_gridspec(
        3, 4,
        height_ratios=[0.4, 1.3, 1.3],
        width_ratios=[1, 1, 1, 1],
        hspace=0.4, wspace=0.25,
        left=0.04, right=0.96, top=0.94, bottom=0.06,
    )

    # --- Row 0: タイトル帯 ---
    ax_title = fig.add_subplot(gs[0, :])
    ax_title.axis("off")
    ax_title.text(
        0.01, 0.5,
        f"[US] {company_jp} (${ticker})",
        ha="left", va="center",
        fontsize=18, fontweight="bold", color=COLOR_ACCENT,
        transform=ax_title.transAxes,
    )
    ax_title.text(
        0.99, 0.5,
        f"{fy_label} 決算",
        ha="right", va="center",
        fontsize=16, color="#333",
        transform=ax_title.transAxes,
    )

    # --- Row 1: 統計4枚 ---
    yoy_str, yoy_color = _signed(yoy)
    rev_beat = "✅" if latest_rev.revenue and eps_latest.actual > eps_latest.estimate else ""  # placeholder
    # 売上 Beat 判定（最新の売上 vs 予想は別データ必要なので省略、YoYで代用）

    _draw_stat(
        fig.add_subplot(gs[1, 0]),
        "売上高",
        _fmt_b(latest_rev.revenue),
        f"YoY {yoy_str}" if yoy else "",
        value_color="#111",
    )

    eps_sign, eps_color = _signed(((eps_latest.actual - eps_latest.estimate) / abs(eps_latest.estimate) * 100) if eps_latest.estimate else None)
    beat_mark = " ○" if eps_latest.actual > eps_latest.estimate else (" ×" if eps_latest.actual < eps_latest.estimate else "")
    _draw_stat(
        fig.add_subplot(gs[1, 1]),
        "EPS 実績",
        f"{_fmt_eps(eps_latest.actual)}{beat_mark}",
        f"予想 {_fmt_eps(eps_latest.estimate)} ({eps_sign})",
        value_color=eps_color if beat_mark else "#111",
    )

    react_str, react_color = _signed(snapshot.earnings_reaction_pct)
    _draw_stat(
        fig.add_subplot(gs[1, 2]),
        "決算後株価反応",
        react_str,
        snapshot.last_earnings_date or "",
        value_color=react_color,
    )

    upside_str, upside_color = _signed(snapshot.upside_pct)
    tgt_sub = f"n={snapshot.target.num_analysts}名" if snapshot.target.num_analysts else ""
    tgt_mean_str = _fmt_eps(snapshot.target.mean).replace("$", "")
    _draw_stat(
        fig.add_subplot(gs[1, 3]),
        "目標株価(平均)",
        f"${tgt_mean_str}" if snapshot.target.mean else "N/A",
        f"上昇余地 {upside_str} / {tgt_sub}",
        value_color=upside_color if snapshot.target.mean else "#111",
    )

    # --- Row 2: 売上推移ミニチャート（横長） ---
    ax_chart = fig.add_subplot(gs[2, :])
    labels = [r.fiscal_label for r in revenue_records[-8:]]
    values = [r.revenue / 1e9 for r in revenue_records[-8:]]
    x = list(range(len(labels)))
    ax_chart.bar(x, values, color=COLOR_BAR, width=0.65)
    for xi, v in zip(x, values):
        ax_chart.text(xi, v * 0.5, f"{v:.1f}", ha="center", fontsize=8)
    ax_chart.set_xticks(x)
    ax_chart.set_xticklabels(labels, fontsize=9, rotation=0)
    ax_chart.set_title("過去8四半期 売上推移 (10億ドル)", fontsize=11, fontweight="bold", loc="left")
    ax_chart.spines["top"].set_visible(False)
    ax_chart.spines["right"].set_visible(False)
    ax_chart.set_yticks([])

    # 出力
    out_path = OUTPUT_DIR / f"{ticker}_{fy_label}_xcard.png"
    fig.savefig(out_path, dpi=150, bbox_inches=None, pad_inches=0)
    plt.close(fig)
    return out_path
