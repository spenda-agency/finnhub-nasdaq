"""決算サマリー画像の生成"""
from pathlib import Path
from typing import Optional

import matplotlib
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

# 日本語フォント設定（プラットフォーム別の優先順）
# Windows: Yu Gothic / Meiryo / MS Gothic
# Linux (GitHub Actions Ubuntu): Noto Sans CJK JP / IPAGothic / VL Gothic
# Mac: Hiragino Sans
matplotlib.rcParams["font.family"] = [
    "Yu Gothic", "Meiryo", "MS Gothic",
    "Noto Sans CJK JP", "Noto Sans JP", "IPAGothic", "VL Gothic",
    "Hiragino Sans",
    "sans-serif",
]
matplotlib.rcParams["axes.unicode_minus"] = False  # マイナス記号の文字化け回避

from config import (
    COLOR_BAR,
    COLOR_HEADER,
    COLOR_JUDGE_OK,
    COLOR_LINE,
    OUTPUT_DIR,
)
from finnhub_client import EpsRecord, MarginRecord, RevenueRecord
from yfinance_client import MarketSnapshot

# Part 2 用カラーパレット
COLOR_BEAT = "#4CAF50"    # 緑（Beat）
COLOR_MISS = "#E57373"    # 赤（Miss）
COLOR_OPERATING = "#1F4E79"  # 青（営業利益率）
COLOR_NET = "#F4A460"        # オレンジ（純利益率）
COLOR_STRONG_BUY = "#2E7D32"
COLOR_BUY = "#66BB6A"
COLOR_HOLD = "#FFC107"
COLOR_SELL = "#EF5350"
COLOR_STRONG_SELL = "#C62828"


def _format_b(value: float) -> str:
    """10億ドル単位に整形。例: 113,800,000,000 -> '113.8B'"""
    return f"{value / 1e9:.1f}B"


def _format_eps(value: float) -> str:
    return f"{value:.2f}"


def _judge(actual: float, estimate: float) -> str:
    """実績がコンセンサスを上回ったら○、下回ったら×、同等なら-"""
    if actual > estimate:
        return "○"
    if actual < estimate:
        return "×"
    return "-"


def _draw_table(ax, title: str, rows: list[list[str]], judge_cells: set[tuple[int, int]]):
    """シンプルな3列+判定テーブルを描画"""
    ax.axis("off")
    n_rows = len(rows)
    n_cols = len(rows[0])
    col_widths = [0.38, 0.22, 0.22, 0.18]
    col_x = [0]
    for w in col_widths:
        col_x.append(col_x[-1] + w)

    row_h = 1.0 / n_rows

    for i, row in enumerate(rows):
        y = 1 - (i + 1) * row_h
        for j, cell in enumerate(row):
            # ヘッダ行と1列目は背景グレー
            bg = None
            if i == 0 or j == 0:
                bg = COLOR_HEADER
            if (i, j) in judge_cells:
                bg = COLOR_JUDGE_OK
            if bg:
                ax.add_patch(Rectangle(
                    (col_x[j], y), col_widths[j], row_h,
                    facecolor=bg, edgecolor="#CCCCCC", linewidth=0.5,
                ))
            else:
                ax.add_patch(Rectangle(
                    (col_x[j], y), col_widths[j], row_h,
                    facecolor="white", edgecolor="#CCCCCC", linewidth=0.5,
                ))
            weight = "bold" if (i == 0 or j == 0) else "normal"
            ax.text(
                col_x[j] + col_widths[j] / 2,
                y + row_h / 2,
                cell,
                ha="center", va="center",
                fontsize=10, fontweight=weight,
            )

    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.set_title(title, fontsize=11, fontweight="bold", loc="left", pad=6)


def build_chart(
    ticker: str,
    company_jp: str,
    eps_records: list[EpsRecord],
    revenue_records: list[RevenueRecord],
) -> Path:
    """決算サマリー画像を生成してPNGパスを返す。"""
    if not eps_records:
        raise ValueError(f"{ticker}: EPSデータが空です")
    if not revenue_records:
        raise ValueError(f"{ticker}: 売上ヒストリカルが空です")

    latest_eps = eps_records[0]
    latest_rev = revenue_records[-1]
    fy_label = latest_rev.fiscal_label

    # 成長率YoYを計算（4Q前との比較）
    yoy_values = []
    for i, r in enumerate(revenue_records):
        if i >= 4:
            prev = revenue_records[i - 4].revenue
            yoy = (r.revenue / prev - 1) * 100 if prev else 0
        else:
            yoy = None
        yoy_values.append(yoy)
    latest_yoy = yoy_values[-1]

    # Figure構築
    fig = plt.figure(figsize=(8.8, 10), facecolor="white")
    gs = fig.add_gridspec(4, 1, height_ratios=[0.6, 1.6, 1.6, 3.5], hspace=0.55)

    # 0: タイトル
    ax_title = fig.add_subplot(gs[0])
    ax_title.axis("off")
    title = f"{company_jp}（{ticker}）{fy_label}決算（Part 1）"
    ax_title.text(
        0.5, 0.5, title,
        ha="center", va="center",
        fontsize=16, fontweight="bold",
        transform=ax_title.transAxes,
    )
    # 下線（アンダーライン風）
    ax_title.plot([0.05, 0.95], [0.15, 0.15], color="black", linewidth=1.2, transform=ax_title.transAxes)

    # 1: EPS表
    ax_eps = fig.add_subplot(gs[1])
    eps_rows = [
        ["EPS［ドル］", "結果", "コンセンサス", "判定"],
        ["Q4実績", _format_eps(latest_eps.actual), _format_eps(latest_eps.estimate), _judge(latest_eps.actual, latest_eps.estimate)],
        ["Q1ガイダンス", "-", "-", "-"],
        ["通年ガイダンス", "-", "-", "-"],
    ]
    judge_set = set()
    if latest_eps.actual > latest_eps.estimate:
        judge_set.add((1, 3))
    _draw_table(ax_eps, "", eps_rows, judge_set)

    # 2: 売上表（コンセンサスは無料枠では取得不可）
    ax_rev = fig.add_subplot(gs[2])
    rev_rows = [
        ["売上高［ドル］", "結果", "コンセンサス", "判定"],
        ["Q4実績", _format_b(latest_rev.revenue), "N/A (無料枠)", "-"],
        ["Q1ガイダンス", "-", "-", "-"],
        ["通年ガイダンス", "-", "-", "-"],
    ]
    _draw_table(ax_rev, "", rev_rows, set())

    # YoY吹き出し
    if latest_yoy is not None:
        ax_rev.annotate(
            f"+{latest_yoy:.0f}%, YoY" if latest_yoy >= 0 else f"{latest_yoy:.0f}%, YoY",
            xy=(0.48, 0.75), xycoords="axes fraction",
            xytext=(0.45, 1.05), textcoords="axes fraction",
            ha="center", fontsize=10,
            bbox=dict(boxstyle="round,pad=0.4", fc="white", ec="gray", lw=0.8),
            arrowprops=dict(arrowstyle="-", color="gray", lw=0.6),
        )

    # 3: 売上推移チャート
    ax_chart = fig.add_subplot(gs[3])
    labels = [r.fiscal_label for r in revenue_records]
    revenues_b = [r.revenue / 1e9 for r in revenue_records]
    x = range(len(labels))

    bars = ax_chart.bar(x, revenues_b, color=COLOR_BAR, width=0.7, label="売上高")
    for bar, val in zip(bars, revenues_b):
        ax_chart.text(
            bar.get_x() + bar.get_width() / 2,
            val * 0.5,
            f"{val:.1f}",
            ha="center", va="center", fontsize=8,
        )

    ax_chart.set_xticks(list(x))
    ax_chart.set_xticklabels(labels, rotation=45, ha="right", fontsize=9)
    ax_chart.set_ylabel("売上高［10億ドル］", fontsize=9)
    ax_chart.set_title("売上高", fontsize=11, fontweight="bold")
    ax_chart.grid(axis="y", alpha=0.3)

    # YoY成長率 line（右軸）
    ax_yoy = ax_chart.twinx()
    valid_x = [i for i, v in enumerate(yoy_values) if v is not None]
    valid_y = [v for v in yoy_values if v is not None]
    ax_yoy.plot(valid_x, valid_y, color=COLOR_LINE, marker="o", linewidth=2, label="成長率(YoY)")
    for xi, yi in zip(valid_x, valid_y):
        ax_yoy.text(xi, yi + 1.5, f"{yi:.0f}%", ha="center", fontsize=8, color=COLOR_LINE)
    ax_yoy.set_ylabel("成長率", fontsize=9)
    ax_yoy.set_ylim(0, max(valid_y) * 1.4 if valid_y else 25)

    # 凡例（上）
    lines1, labels1 = ax_chart.get_legend_handles_labels()
    lines2, labels2 = ax_yoy.get_legend_handles_labels()
    ax_chart.legend(lines1 + lines2, labels1 + labels2, loc="upper left", fontsize=9, frameon=False)

    # 出力
    out_path = OUTPUT_DIR / f"{ticker}_{fy_label}.png"
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return out_path


# ============================================================
# Part 2: 分析向け詳細チャート
# ============================================================

def _draw_kpi_card(ax, label: str, value: str, sub: str = "", color: str = "#333"):
    """1枚のKPIカードを描画"""
    ax.axis("off")
    ax.add_patch(Rectangle((0, 0), 1, 1, facecolor="#F7F7F7", edgecolor="#CCCCCC", linewidth=0.8))
    ax.text(0.5, 0.78, label, ha="center", va="center", fontsize=9, color="#666")
    ax.text(0.5, 0.42, value, ha="center", va="center", fontsize=16, fontweight="bold", color=color)
    if sub:
        ax.text(0.5, 0.15, sub, ha="center", va="center", fontsize=8, color="#888")


def _fmt_signed_pct(v: Optional[float]) -> tuple[str, str]:
    """±付きの%文字列と色を返す"""
    if v is None:
        return "N/A", "#888"
    sign = "+" if v >= 0 else ""
    color = "#2E7D32" if v >= 0 else "#C62828"
    return f"{sign}{v:.1f}%", color


def build_chart_part2(
    ticker: str,
    company_jp: str,
    eps_records: list[EpsRecord],
    margin_records: list[MarginRecord],
    snapshot: MarketSnapshot,
    fy_label: str,
) -> Path:
    """Part 2（分析向け）チャートを生成"""
    fig = plt.figure(figsize=(8.8, 11), facecolor="white")
    gs = fig.add_gridspec(
        5, 1,
        height_ratios=[0.5, 1.2, 2.2, 2.2, 2.4],
        hspace=0.55,
    )

    # --- Row 0: タイトル ---
    ax_title = fig.add_subplot(gs[0])
    ax_title.axis("off")
    ax_title.text(
        0.5, 0.5,
        f"{company_jp}（{ticker}）{fy_label}決算（Part 2）",
        ha="center", va="center",
        fontsize=16, fontweight="bold",
        transform=ax_title.transAxes,
    )
    ax_title.plot([0.05, 0.95], [0.15, 0.15], color="black", linewidth=1.2, transform=ax_title.transAxes)

    # --- Row 1: KPIカード 4枚 ---
    gs_kpi = gs[1].subgridspec(1, 4, wspace=0.15)

    ax_k1 = fig.add_subplot(gs_kpi[0, 0])
    price_txt = f"${snapshot.current_price:.2f}" if snapshot.current_price else "N/A"
    _draw_kpi_card(ax_k1, "現在株価", price_txt, sub=snapshot.ticker)

    ax_k2 = fig.add_subplot(gs_kpi[0, 1])
    react_txt, react_color = _fmt_signed_pct(snapshot.earnings_reaction_pct)
    react_sub = f"発表日: {snapshot.last_earnings_date}" if snapshot.last_earnings_date else ""
    _draw_kpi_card(ax_k2, "決算後の株価反応", react_txt, sub=react_sub, color=react_color)

    ax_k3 = fig.add_subplot(gs_kpi[0, 2])
    tgt_txt = f"${snapshot.target.mean:.2f}" if snapshot.target.mean else "N/A"
    tgt_sub = f"n={snapshot.target.num_analysts}" if snapshot.target.num_analysts else ""
    _draw_kpi_card(ax_k3, "目標株価（平均）", tgt_txt, sub=tgt_sub)

    ax_k4 = fig.add_subplot(gs_kpi[0, 3])
    upside_txt, upside_color = _fmt_signed_pct(snapshot.upside_pct)
    _draw_kpi_card(ax_k4, "上昇余地", upside_txt, sub="vs 現在株価", color=upside_color)

    # --- Row 2: EPS Beat履歴（過去8Q） ---
    ax_eps = fig.add_subplot(gs[2])
    recent_eps = eps_records[:8][::-1]  # 古い順
    labels = [r.period[:7] for r in recent_eps]
    actuals = [r.actual for r in recent_eps]
    estimates = [r.estimate for r in recent_eps]
    x = list(range(len(labels)))
    width = 0.38

    ax_eps.bar([i - width / 2 for i in x], estimates, width, label="予想", color="#B0BEC5")
    bar_actual = ax_eps.bar(
        [i + width / 2 for i in x],
        actuals,
        width,
        label="実績",
        color=[COLOR_BEAT if a >= e else COLOR_MISS for a, e in zip(actuals, estimates)],
    )
    for i, (a, e) in enumerate(zip(actuals, estimates)):
        mark = "○" if a >= e else "×"
        surprise_pct = (a - e) / abs(e) * 100 if e else 0
        top = max(a, e)
        ax_eps.text(i, top + max(actuals) * 0.05, f"{mark} {surprise_pct:+.1f}%",
                    ha="center", fontsize=8,
                    color=COLOR_BEAT if a >= e else COLOR_MISS)
    ax_eps.set_xticks(x)
    ax_eps.set_xticklabels(labels, rotation=30, ha="right", fontsize=9)
    ax_eps.set_ylabel("EPS（ドル）", fontsize=9)
    ax_eps.set_title("EPS Beat履歴（過去8四半期）", fontsize=11, fontweight="bold", loc="left")
    ax_eps.legend(loc="upper left", fontsize=9, frameon=False)
    ax_eps.grid(axis="y", alpha=0.3)
    ax_eps.set_ylim(0, max(actuals + estimates) * 1.25)

    # --- Row 3: 利益率トレンド ---
    ax_mg = fig.add_subplot(gs[3])
    mg_labels = [m.fiscal_label for m in margin_records]
    op_margins = [m.operating_margin for m in margin_records]
    net_margins = [m.net_margin for m in margin_records]
    mx = list(range(len(mg_labels)))

    if any(v is not None for v in op_margins):
        valid = [(i, v) for i, v in enumerate(op_margins) if v is not None]
        if valid:
            vx, vy = zip(*valid)
            ax_mg.plot(vx, vy, marker="o", color=COLOR_OPERATING, linewidth=2, label="営業利益率")
            for i, v in valid:
                ax_mg.text(i, v + 1, f"{v:.1f}%", ha="center", fontsize=8, color=COLOR_OPERATING)

    if any(v is not None for v in net_margins):
        valid = [(i, v) for i, v in enumerate(net_margins) if v is not None]
        if valid:
            vx, vy = zip(*valid)
            ax_mg.plot(vx, vy, marker="s", color=COLOR_NET, linewidth=2, label="純利益率")
            for i, v in valid:
                ax_mg.text(i, v - 2, f"{v:.1f}%", ha="center", fontsize=8, color=COLOR_NET)

    ax_mg.set_xticks(mx)
    ax_mg.set_xticklabels(mg_labels, rotation=45, ha="right", fontsize=9)
    ax_mg.set_ylabel("利益率（%）", fontsize=9)
    ax_mg.set_title("利益率トレンド", fontsize=11, fontweight="bold", loc="left")
    ax_mg.legend(loc="lower right", fontsize=9, frameon=False)
    ax_mg.grid(axis="y", alpha=0.3)
    ax_mg.axhline(0, color="#888", linewidth=0.5)

    # --- Row 4: アナリスト推奨 + 目標株価レンジ ---
    gs_bot = gs[4].subgridspec(2, 1, height_ratios=[1, 1], hspace=0.5)

    # 4a: 推奨度（横向き積み上げバー）
    ax_rec = fig.add_subplot(gs_bot[0])
    rec = snapshot.recommendation
    if rec.total > 0:
        segments = [
            ("Strong Buy", rec.strong_buy, COLOR_STRONG_BUY),
            ("Buy", rec.buy, COLOR_BUY),
            ("Hold", rec.hold, COLOR_HOLD),
            ("Sell", rec.sell, COLOR_SELL),
            ("Strong Sell", rec.strong_sell, COLOR_STRONG_SELL),
        ]
        left = 0
        for name, count, color in segments:
            if count > 0:
                ax_rec.barh(0, count, left=left, color=color, edgecolor="white", linewidth=1)
                ax_rec.text(
                    left + count / 2, 0,
                    f"{name}\n{count}",
                    ha="center", va="center", fontsize=8,
                    color="white", fontweight="bold",
                )
                left += count
        ax_rec.set_xlim(0, rec.total)
        ax_rec.set_ylim(-0.6, 0.6)
        ax_rec.set_yticks([])
        ax_rec.set_xticks([])
        for spine in ax_rec.spines.values():
            spine.set_visible(False)
        ax_rec.set_title(
            f"アナリスト推奨（計 {rec.total} 名）",
            fontsize=11, fontweight="bold", loc="left",
        )
    else:
        ax_rec.axis("off")
        ax_rec.text(0.5, 0.5, "アナリスト推奨データなし", ha="center", va="center", fontsize=10, color="#888")
        ax_rec.set_title("アナリスト推奨", fontsize=11, fontweight="bold", loc="left")

    # 4b: 目標株価レンジ
    ax_tgt = fig.add_subplot(gs_bot[1])
    if snapshot.target.low and snapshot.target.high and snapshot.current_price:
        low = snapshot.target.low
        high = snapshot.target.high
        mean = snapshot.target.mean or (low + high) / 2
        current = snapshot.current_price

        # レンジバー
        y = 0
        ax_tgt.plot([low, high], [y, y], color="#CCCCCC", linewidth=8, solid_capstyle="round")
        # 平均マーカー
        ax_tgt.plot(mean, y, marker="v", color=COLOR_OPERATING, markersize=14, label=f"平均: ${mean:.2f}")
        # 現在株価マーカー
        ax_tgt.plot(current, y, marker="o", color=COLOR_BAR, markersize=14, label=f"現在: ${current:.2f}")

        # 数値ラベル
        ax_tgt.text(low, y - 0.35, f"安値\n${low:.2f}", ha="center", fontsize=8)
        ax_tgt.text(high, y - 0.35, f"高値\n${high:.2f}", ha="center", fontsize=8)

        x_min = min(low, current) * 0.95
        x_max = max(high, current) * 1.05
        ax_tgt.set_xlim(x_min, x_max)
        ax_tgt.set_ylim(-0.8, 0.5)
        ax_tgt.set_yticks([])
        for spine in ["left", "right", "top"]:
            ax_tgt.spines[spine].set_visible(False)
        ax_tgt.set_xlabel("株価（ドル）", fontsize=9)
        ax_tgt.legend(loc="upper center", fontsize=9, frameon=False, ncol=2, bbox_to_anchor=(0.5, 1.15))
        ax_tgt.set_title("目標株価レンジ", fontsize=11, fontweight="bold", loc="left", pad=20)
    else:
        ax_tgt.axis("off")
        ax_tgt.text(0.5, 0.5, "目標株価データなし", ha="center", va="center", fontsize=10, color="#888")
        ax_tgt.set_title("目標株価レンジ", fontsize=11, fontweight="bold", loc="left")

    # 出力
    out_path = OUTPUT_DIR / f"{ticker}_{fy_label}_part2.png"
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return out_path
