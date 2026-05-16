"""Anthropic Claude API ラッパ（X投稿文 / コラム記事 / WPブログ記事生成）"""
import os
import re
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
- ハッシュタグ（#で始まる単語）は一切使用しない"""


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
- ハッシュタグ（# で始まる単語）は一切使わない"""
    return _call(X_SYSTEM, user, max_tokens=400, temperature=0.5)


# -----------------------------------------------------------------
# 集約版 X投稿文（全銘柄を1ポストにまとめる）
# -----------------------------------------------------------------
X_AGGREGATED_SYSTEM = """あなたは米国決算速報を1日1本のXポストにまとめるアナリストです。
複数銘柄の決算結果を、**140字以内**の日本語ツイート1本に集約します。

必須ルール:
- 140字以内（改行も字数にカウント。厳守）
- 対象の全ティッカーに `$` を付けて列挙（例: $GOOG $MSFT $NVDA）
- Beat/Miss / YoY / 株価反応の要点を最小限のキーワードで
- 絵文字は合計2〜4個まで
- 断定・推奨はしない（事実ベース）
- ハッシュタグ（# で始まる単語）は一切使わない
- 文字数オーバーは絶対NG。削る優先順位: 絵文字 → 詳細数値 → ティッカーYoYなど"""


def generate_aggregated_x_post(facts_list: list[dict], report_date: str) -> str:
    """複数銘柄の facts を1つの140字以内ツイートに集約する。"""
    lines = []
    for f in facts_list:
        lines.append(
            f"- ${f['ticker']} {f['company']}: "
            f"売上 {f['revenue_actual_str']} (YoY {f['yoy_str']}) / "
            f"EPS {f['eps_actual_str']} (予想 {f['eps_estimate_str']}, サプライズ {f['eps_surprise_str']}) / "
            f"株価反応 {f['reaction_str']} / 目標 {f['target_mean_str']} (上振れ {f['upside_str']})"
        )
    data_block = "\n".join(lines)

    user = f"""{report_date} 発表の米国株決算 {len(facts_list)}銘柄を、140字以内の日本語Xポスト1本にまとめてください。

対象銘柄のデータ:
{data_block}

出力ルール:
- 140字厳守（改行含む）
- 全ティッカーに $ を付けて含める
- 個別の細かい数値は省いてOK。Beat/Miss や YoY のざっくり傾向で可
- ハッシュタグ（# で始まる単語）は一切使わない
- 出力はツイート本文のみ（前置き・解説・引用符なし）"""

    text = _call(X_AGGREGATED_SYSTEM, user, max_tokens=400, temperature=0.4)
    # 安全装置: モデルがハッシュタグを混ぜた場合に備えて除去
    text = re.sub(r"#\S+", "", text)
    text = re.sub(r"[ 　]{2,}", " ", text).strip()
    # 140字オーバー時は末尾を切り詰める
    if len(text) > 140:
        text = text[:140].rstrip()
    return text


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


# -----------------------------------------------------------------
# WordPress ブログ記事生成（HTML出力 / Title + MetaDescription + Body）
# -----------------------------------------------------------------
WP_BLOG_SYSTEM = """あなたは最新ニュースをまとめるのが得意なビジネス系編集者兼ライターです。
入力された米国株の決算データを、自然で読みやすいブログ記事に整えてください。
記事はWordPressにそのまま入稿できるHTML形式で出力し、各銘柄ニュースを見出しと説明文で構成します。

要件:
- 想定読者: 米国株の最新ニュースを知って投資判断や雑談に活かしたい社会人
- トーン: ビジネス風、知的で落ち着いた語り口。専門的だが平易で、誇張や煽りを避ける
- 文字数: 内容に応じて1000字以上
- 構成: 冒頭で「最新ニュースをお伝えします」など短い導入を置き、その後に各銘柄ニュースの<h2>見出しと本文を続ける
- 日本語表現: 書き言葉に統一。句読点の位置、語尾のリズム、助詞の流れを整え、冗長・重複・口癖・フィラー（えー、あの、みたいな等）は削除

WordPress出力仕様:
- 本文は<h2>と<h3>を適切に用い、段落ごとに<br><br>で1行空ける
- 余計なラッパー（html, head, body, articleタグ等）は付けない
- 外部リンクや画像は挿入しない（入力データ内に確かな参照がある場合のみ許可）

品質基準:
- 事実は入力データに基づく。推測や断定的表現は避ける
- 長文内の主語・述語対応や時制を安定させる
- 同一語尾の連続を避け、文長にばらつきを作る（〜です。〜です。が続かないように言い換える）
- 重要用語は初出で簡潔に定義・説明
- 末尾に「次のステップ」や「チェックリスト」など読み手のアクションが分かる結びを置く

禁止事項:
- 入力データにない事実の創作、出典不明のデータの挿入
- 不自然なキーワードの羅列、過度な専門用語の連投
- 過剰な冗長表現、意味の重複する言い換えの多用
- 感嘆符・顔文字・口語的な間投詞の多用

出力フォーマット（必ずこの順序で、ラベルもそのまま使用）:
Title: （日付とインパクトのある銘柄名・キーワードを左寄せに含めた1行。簡潔でクリック意欲を喚起）
MetaDescription: （主キーワードを含む100〜140字の要約）
Body:
（WordPress本文に貼り付け可能なHTML本体。<h2>/<h3>を使い段落間は<br><br>で空ける。Bodyラベルの後ろに続けて記述する）"""


def _build_wp_data_block(facts_list: list[dict], upcoming_entries: list[dict] | None) -> str:
    """WP記事生成プロンプト用のデータブロックを組み立てる。"""
    blocks = []
    for f in facts_list:
        blocks.append(
            f"- {f['company']} (${f['ticker']}) {f['fy_label']}（発表日 {f['report_date']}）\n"
            f"  - 売上 {f['revenue_actual_str']} (予想 {f['revenue_estimate_str']}, "
            f"サプライズ {f['revenue_surprise_str']}, YoY {f['yoy_str']})\n"
            f"  - EPS {f['eps_actual_str']} (予想 {f['eps_estimate_str']}, "
            f"サプライズ {f['eps_surprise_str']})\n"
            f"  - 営業利益率 {f['op_margin_str']} / 純利益率 {f['net_margin_str']}\n"
            f"  - 現在株価 {f['current_price_str']} / 決算後株価反応 {f['reaction_str']}\n"
            f"  - 目標株価 平均 {f['target_mean_str']} / 高値 {f['target_high_str']} / "
            f"安値 {f['target_low_str']} (上昇余地 {f['upside_str']})\n"
            f"  - アナリスト推奨: Strong Buy {f['strong_buy']} / Buy {f['buy']} / "
            f"Hold {f['hold']} / Sell {f['sell']} / Strong Sell {f['strong_sell']} "
            f"(計 {f['total_analysts']}名)\n"
            f"  - 過去8四半期売上推移:\n{f['revenue_history_str']}"
        )
    decided_block = "\n\n".join(blocks)

    upcoming_block = ""
    if upcoming_entries:
        u_lines = []
        for u in upcoming_entries:
            u_lines.append(
                f"- {u['company']} (${u['ticker']}) {u['fy_label']} / "
                f"発表予定 {u.get('date', '')} {u.get('hour', '')} / "
                f"EPS予想 {u.get('eps_estimate_str', 'N/A')} (前期 {u.get('prev_eps_str', 'N/A')}) / "
                f"Rev予想 {u.get('revenue_estimate_str', 'N/A')} (前期 {u.get('prev_revenue_str', 'N/A')}) / "
                f"時価総額 {u.get('market_cap_str', 'N/A')}"
            )
        upcoming_block = "\n\n## 決算直前の注目銘柄（参考データ）\n" + "\n".join(u_lines)

    return f"## 決算発表済み銘柄データ\n{decided_block}{upcoming_block}"


def generate_wp_blog_article(
    facts_list: list[dict],
    report_date: str,
    upcoming_entries: list[dict] | None = None,
) -> dict:
    """WP下書き用のブログ記事をHTMLで生成する。

    戻り値: {"title": str, "meta_description": str, "body_html": str, "raw": str}
    出力解析に失敗した場合は title/meta_description が空になり得るので、
    呼び出し側でフォールバックを用意すること。
    """
    data_block = _build_wp_data_block(facts_list, upcoming_entries)
    user = f"""日付: {report_date}
銘柄数: {len(facts_list)}件

入力データ:
{data_block}

上記データに基づき、最初に指定したフォーマット
（Title / MetaDescription / Body）に厳密に従ってWordPress記事を出力してください。
Body はそのまま貼り付けて記事にできるHTMLとしてください。"""

    # 銘柄数に応じてトークン上限を調整
    max_tokens = max(4000, len(facts_list) * 1800 + 1500)
    if upcoming_entries:
        max_tokens += len(upcoming_entries) * 600
    max_tokens = min(max_tokens, 8000)

    raw = _call(WP_BLOG_SYSTEM, user, max_tokens=max_tokens, temperature=0.5)
    return _parse_wp_blog_output(raw)


def _parse_wp_blog_output(raw: str) -> dict:
    """`Title:` / `MetaDescription:` / `Body:` 区切りで出力をパースする。

    見つからない場合は当該フィールドを空文字とし、body_html は raw 全体を返す。
    """
    result = {"title": "", "meta_description": "", "body_html": "", "raw": raw}
    text = raw.strip()

    # 各ラベルの位置を検索（行頭、コロン直後）。日本語コロン「：」も許容。
    title_match = re.search(r"^Title\s*[:：]\s*(.+?)$", text, re.MULTILINE)
    meta_match = re.search(r"^MetaDescription\s*[:：]\s*(.+?)$", text, re.MULTILINE)
    body_match = re.search(r"^Body\s*[:：]\s*", text, re.MULTILINE)

    if title_match:
        result["title"] = title_match.group(1).strip()
    if meta_match:
        result["meta_description"] = meta_match.group(1).strip()

    if body_match:
        # Body: の直後以降をHTML本体とみなす
        body_html = text[body_match.end():].strip()
        # Body内に Title / MetaDescription が再出現していたら除去（保険）
        body_html = re.sub(r"^(Title|MetaDescription)\s*[:：].*$\n?", "",
                           body_html, flags=re.MULTILINE).strip()
        result["body_html"] = body_html
    else:
        # ラベルが見つからない場合: Title/MetaDescriptionを除去した残りをBodyとする
        body_html = text
        if title_match:
            body_html = body_html.replace(title_match.group(0), "", 1)
        if meta_match:
            body_html = body_html.replace(meta_match.group(0), "", 1)
        result["body_html"] = body_html.strip()

    return result
