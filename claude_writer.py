"""Anthropic Claude API ラッパ（X投稿文 / コラム記事生成）"""
import os
from typing import Optional

import anthropic
from dotenv import load_dotenv

load_dotenv()

# 低コスト・十分な品質で米国株コラム向き
MODEL = "claude-haiku-4-5-20251001"


def _client() -> anthropic.Anthropic:
    key = os.getenv("ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY が未設定です。"
            "ローカル実行時は .env、GitHub Actions実行時は Secrets を確認してください。"
        )
    return anthropic.Anthropic(api_key=key)


def _call(system: str, user: str, max_tokens: int = 2000, temperature: float = 0.7) -> str:
    client = _client()
    resp = client.messages.create(
        model=MODEL,
        max_tokens=max_tokens,
        temperature=temperature,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    # assistant message の text を連結して返す
    text_parts = [b.text for b in resp.content if getattr(b, "type", "") == "text"]
    return "".join(text_parts).strip()


# -----------------------------------------------------------------
# X投稿文生成
# -----------------------------------------------------------------
X_SYSTEM = """あなたは米国株の決算速報を140字以内の日本語ツイートにまとめるアナリストです。
守るべきルール:
- 140字以内（必須。改行も字数にカウント）
- 絵文字は2〜4個、$TICKERを1回含める
- キーとなる数値（売上/EPS/YoY/目標株価）を1行ずつ簡潔に
- ポジティブ/ネガティブは事実ベースで表現。断定・推奨はしない
- 最後に #米国株 #決算 を必ず含める"""


def generate_x_post(facts: dict) -> str:
    """facts: 企業データ辞書から 140字以内のX投稿文を生成"""
    user = f"""以下の決算データを140字以内のツイートにしてください:

銘柄: {facts['company']} ($ {facts['ticker']})
期: {facts['fy_label']}
売上実績: {facts['revenue_actual_str']}（予想 {facts['revenue_estimate_str']}、YoY {facts['yoy_str']}）
EPS実績: {facts['eps_actual_str']}（予想 {facts['eps_estimate_str']}）
決算後株価反応: {facts['reaction_str']}
目標株価平均: {facts['target_mean_str']}（上昇余地 {facts['upside_str']}）

注意:
- 140字厳守
- 改行を使って読みやすく
- 結論めいた主観は避け、事実のみ
- 最後の行に必ず #米国株 #決算 を含める"""
    return _call(X_SYSTEM, user, max_tokens=400, temperature=0.5)


# -----------------------------------------------------------------
# コラム記事生成
# -----------------------------------------------------------------
COLUMN_SYSTEM = """あなたは米国株の決算速報コラムを書くアナリストです。
個人投資家が毎朝読むのを楽しみにする、データに基づいた冷静で読みやすい文章を書きます。

文体ルール:
- 日本語、丁寧語（です・ます）
- 1,800〜2,200字
- 断定・推奨は避け、「〜と読み取れます」「〜の可能性があります」など客観的な表現
- 数値は必ず出典データ範囲内の事実のみ使用。外部知識の創作禁止
- 市場の見方・アナリスト予想は「そう言われている」形で引用

構成（各見出し必須）:
## 決算サマリー（200字前後）
## 決算ハイライト（400字前後、数値中心）
## 市場の受け止め（400字前後、株価反応・アナリスト推奨を解説）
## 成長性と収益性（400字前後、YoY・利益率トレンド）
## 投資家目線のポイント（300字前後、注目すべき論点）
## 次のカタライスト（150字前後、次回決算予定など。不明な場合は触れない）"""


def generate_column(facts: dict) -> str:
    """facts辞書から 2,000字前後のコラムMarkdownを生成"""
    user = f"""以下の決算データに基づき、2,000字前後のコラム記事をMarkdownで書いてください。

## 銘柄情報
- 企業: {facts['company']}
- ティッカー: ${facts['ticker']}
- 期: {facts['fy_label']}
- 発表日: {facts['report_date']}

## 決算実績
- 売上: {facts['revenue_actual_str']}（予想 {facts['revenue_estimate_str']}、サプライズ {facts['revenue_surprise_str']}）
- EPS: {facts['eps_actual_str']}（予想 {facts['eps_estimate_str']}、サプライズ {facts['eps_surprise_str']}）
- 売上YoY: {facts['yoy_str']}

## 収益性（直近四半期）
- 営業利益率: {facts['op_margin_str']}
- 純利益率: {facts['net_margin_str']}

## 市場の反応
- 現在株価: {facts['current_price_str']}
- 決算後株価反応: {facts['reaction_str']}
- 目標株価: 平均 {facts['target_mean_str']} / 高値 {facts['target_high_str']} / 安値 {facts['target_low_str']}
- アナリスト推奨: Strong Buy {facts['strong_buy']} / Buy {facts['buy']} / Hold {facts['hold']} / Sell {facts['sell']} / Strong Sell {facts['strong_sell']}（計 {facts['total_analysts']}名）

## 過去8四半期の売上推移
{facts['revenue_history_str']}

上記データのみを使って、外部知識の創作なしでコラムを書いてください。
タイトル行（H1）から始めて、各セクション見出し（H2）に沿って記述してください。"""
    return _call(COLUMN_SYSTEM, user, max_tokens=3000, temperature=0.6)


# -----------------------------------------------------------------
# まとめコラム記事生成（全銘柄を1記事にまとめる）
# -----------------------------------------------------------------
COMBINED_COLUMN_SYSTEM = """あなたは米国株の決算速報コラムを書くアナリストです。
個人投資家が毎朝読むのを楽しみにする、データに基づいた冷静で読みやすい文章を書きます。

文体ルール:
- 日本語、丁寧語（です・ます）
- 断定・推奨は避け、「〜と読み取れます」「〜の可能性があります」など客観的な表現
- 数値は必ず出典データ範囲内の事実のみ使用。外部知識の創作禁止
- 市場の見方・アナリスト予想は「そう言われている」形で引用

構成ルール:
- 冒頭に全体の概要（200〜300字）
- 各銘柄ごとにH2見出しで区切り、以下のサブセクション（H3）を含める:
  ### 決算ハイライト（数値中心）
  ### 市場の受け止め（株価反応・アナリスト推奨）
  ### 成長性と収益性（YoY・利益率トレンド）
  ### 投資家目線のポイント（注目すべき論点）
- 最後に全体のまとめ（200字前後）"""


def generate_combined_column(facts_list: list[dict], upcoming_entries: list[dict] | None = None) -> str:
    """複数銘柄の facts 辞書リストから、1つのまとめコラム記事を生成"""

    # 各銘柄のデータブロックを組み立て
    ticker_blocks = []
    for facts in facts_list:
        block = f"""### {facts['company']} (${facts['ticker']}) — {facts['fy_label']}
- 発表日: {facts['report_date']}
- 売上: {facts['revenue_actual_str']}（予想 {facts['revenue_estimate_str']}、サプライズ {facts['revenue_surprise_str']}）
- EPS: {facts['eps_actual_str']}（予想 {facts['eps_estimate_str']}、サプライズ {facts['eps_surprise_str']}）
- 売上YoY: {facts['yoy_str']}
- 営業利益率: {facts['op_margin_str']} / 純利益率: {facts['net_margin_str']}
- 現在株価: {facts['current_price_str']}（決算後反応: {facts['reaction_str']}）
- 目標株価: 平均 {facts['target_mean_str']} / 高値 {facts['target_high_str']} / 安値 {facts['target_low_str']}
- アナリスト推奨: Strong Buy {facts['strong_buy']} / Buy {facts['buy']} / Hold {facts['hold']} / Sell {facts['sell']} / Strong Sell {facts['strong_sell']}（計 {facts['total_analysts']}名）
- 過去8四半期の売上推移:
{facts['revenue_history_str']}"""
        ticker_blocks.append(block)

    tickers_data = "\n\n".join(ticker_blocks)

    # 決算直前の銘柄セクション
    upcoming_section = ""
    if upcoming_entries:
        upcoming_lines = []
        for u in upcoming_entries:
            upcoming_lines.append(
                f"- {u['company']} (${u['ticker']}): {u['fy_label']} / "
                f"EPS予想 {u.get('eps_estimate_str', 'N/A')} (前期 {u.get('prev_eps_str', 'N/A')}) / "
                f"Rev予想 {u.get('revenue_estimate_str', 'N/A')} (前期 {u.get('prev_revenue_str', 'N/A')}) / "
                f"時価総額 {u.get('market_cap_str', 'N/A')} / "
                f"発表予定 {u.get('date', '')} {u.get('hour', '')}"
            )
        upcoming_section = f"""

## 今後の決算予定データ（決算直前の詳細セクション用）
{chr(10).join(upcoming_lines)}"""

    user = f"""以下の決算データに基づき、まとめコラム記事をMarkdownで書いてください。
タイトル行（H1）は不要です（別途設定します）。

## 決算発表済み銘柄データ（決算直後結果セクション用）
{tickers_data}
{upcoming_section}

ルール:
- 上記データのみを使って、外部知識の創作なしで書いてください
- 冒頭の概要で全銘柄の決算結果を簡潔に要約してください
- 決算発表済みの各銘柄ごとにH2見出し（## 銘柄名）で区切り、H3サブセクションで詳細を書いてください
- 今後の決算予定データがある場合は「## 決算直前の注目銘柄」というH2セクションを最後の方に追加し、各銘柄の注目ポイントをまとめてください
- 最後に「## まとめ」セクションで全体の総括を書いてください
- 各銘柄セクションは800〜1,200字、全体で最低2,000字以上"""

    # 銘柄数に応じてトークン上限を調整
    max_tokens = max(4000, len(facts_list) * 2000 + 1500)
    if upcoming_entries:
        max_tokens += len(upcoming_entries) * 800
    max_tokens = min(max_tokens, 8000)  # Claude Haiku の安全上限

    return _call(COMBINED_COLUMN_SYSTEM, user, max_tokens=max_tokens, temperature=0.6)
