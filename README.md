# 決算サマリー生成MVP

Finnhub無料APIからNASDAQ銘柄の決算データを取得し、決算サマリー画像を生成してSlackに投稿する。

## セットアップ

### 1. Finnhub APIキー取得

1. <https://finnhub.io/register> で無料登録
2. ダッシュボードのAPI Keyをコピー

### 2. Slack Bot Token取得

1. <https://api.slack.com/apps> で "Create New App" → "From scratch"
2. OAuth & Permissions → Bot Token Scopes に以下を追加:
   - `chat:write`（必須）
   - `files:write`（必須）
   - `channels:read`（任意。チャンネル名→ID解決が必要な場合のみ）
3. "Install to Workspace" → `xoxb-` で始まるトークンをコピー
4. `#99z_test_data` チャンネルにBotを招待:
   ```
   /invite @your-bot-name
   ```
5. **チャンネルIDを確認**: チャンネルを右クリック → 「チャンネル詳細を表示」 → 下部に `C05QPTXN4HE` のような文字列。これを `.env` の `SLACK_CHANNEL` に設定する（`#99z_test_data` という名前指定でもコード側で自動解決するが、IDの方が確実）

### 3. 環境変数設定

```powershell
cd C:\Users\spend\finnhub
copy .env.example .env
notepad .env
```

`.env` に以下を記入:
```
FINNHUB_API_KEY=ck...（Finnhubで取得した値）
SLACK_BOT_TOKEN=xoxb-...（Slackで取得した値）
SLACK_CHANNEL=#99z_test_data
```

### 4. Python環境構築

```powershell
cd C:\Users\spend\finnhub
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

## 実行

```powershell
# 全社（GOOG/AAPL/MSFT）をPart 1 + Part 2 両方生成してSlack投稿
python main.py

# 1社のみ
python main.py GOOG

# Slack投稿せず、ローカル保存のみ（動作確認用）
python main.py GOOG --no-slack

# Part 1 だけ / Part 2 だけ
python main.py GOOG --part 1
python main.py GOOG --part 2
```

生成されたPNGは `output/` に保存されます（`TICKER_FYxxQx.png` / `TICKER_FYxxQx_part2.png`）。

## Part 1 / Part 2 の内容

### Part 1（サマリー）
- EPS・売上の実績 vs コンセンサス
- 過去12四半期の売上推移と YoY 成長率

### Part 2（詳細分析）
- **KPIカード**: 現在株価 / 決算後の株価反応 / 目標株価平均 / 上昇余地
- **EPS Beat履歴**: 過去8四半期の実績vs予想、○×判定とサプライズ%
- **利益率トレンド**: 営業利益率・純利益率の推移
- **アナリスト推奨**: Strong Buy〜Strong Sell の内訳
- **目標株価レンジ**: 高値・平均・安値と現在株価の関係

Part 2 のデータソース:
- Finnhub: EPS実績・利益率（`/stock/financials-reported` から計算）
- yfinance: 株価・目標株価・アナリスト推奨・決算反応（APIキー不要）

## 朝のレポート（毎朝8時 自動実行）

`morning_report.py` が以下を自動投稿します：

### セクション1: 今後3日の主要決算予定
Finnhub `/calendar/earnings` から、`Revenue Consensus > $10B` かつ未発表の銘柄を1行ずつ。
```
🇺🇸 NVDA   | EPS予想 $0.85 | Rev予想 $32.5B (前期 $30.0B) | MCap $3.20T | FY26Q1 02-26 amc
🇺🇸 CRM    | EPS予想 $2.10 | Rev予想  $9.7B (前期 $9.3B)  | MCap $260B  | FY25Q4 02-26 amc
```

### セクション2: 直近24時間の決算 → Part 1 / Part 2 + 集約X下書き + 集約WP記事
前営業日 amc 〜 当日 bmo に発表された `Revenue Actual > $10B` の銘柄について：
1. **Part 1 / Part 2** 画像を Slack スレッドに投稿
2. **まとめコラム記事 (全銘柄1本 / 2,000字以上)** を Claude API で生成 → Slack スレッドに長文投稿
3. **X投稿 集約版 (全銘柄1本 / 140字以内)** を Claude API で生成
   - `drafts/YYYY-MM-DD.txt` に保存してリポジトリに自動コミット（手動Xコピペ用）
   - Slack スレッドにも本文投稿
4. **WordPress下書き (1日1記事 / 全銘柄集約)** を WP に保存
   - 本文 = コラム記事 + 集約X下書き + 銘柄別チャート画像
   - 銘柄ごとに別記事は作らない（1日＝1下書き）

### 手動実行
```powershell
cd C:\Users\spend\finnhub
python morning_report.py
```
ログは `logs/morning_YYYY-MM-DD.log` に保存。

### 自動実行（GitHub Actions / 推奨）
リポジトリ: <https://github.com/spenda-agency/finnhub-nasdaq>

毎週 **火-土 8:00 JST**（cron `0 23 * * 1-5` UTC）に自動実行されます。
ワークフロー定義: [.github/workflows/morning-report.yml](.github/workflows/morning-report.yml)

#### 初回セットアップ
1. リポジトリを GitHub に push（下記「初回 push 手順」参照）
2. GitHub の Settings → Secrets and variables → Actions で以下を登録:

   | Secret 名 | 値 | 必須 |
   |---|---|---|
   | `FINNHUB_API_KEY` | Finnhub のAPIキー | ✅ |
   | `SLACK_BOT_TOKEN` | `xoxb-...` | ✅ |
   | `SLACK_CHANNEL` | `C05QPTXN4HE` | ✅ |
   | `ANTHROPIC_API_KEY` | `sk-ant-...` | ✅ (コラム/X生成用) |
   | `WP_SITE_URL` | `https://fxstock-dataincome.com` | ✅ (X下書き保存用) |
   | `WP_USERNAME` | WP のログイン名 | ✅ |
   | `WP_APP_PASSWORD` | アプリケーションパスワード（スペース込み24文字） | ✅ |

   **WordPress アプリケーションパスワードの取得手順:**
   1. WordPress 管理画面にログイン (`https://fxstock-dataincome.com/wp-admin`)
   2. 左メニュー「ユーザー」→「プロフィール」
   3. 下部の「アプリケーションパスワード」セクション
   4. 新しい名前（例: `finnhub-nasdaq`）を入れて「新しいアプリケーションパスワードを追加」
   5. 表示された24文字（`xxxx xxxx xxxx xxxx xxxx xxxx` スペース込み）をコピー → Secret に設定
   6. 閉じると二度と見られないので必ず保存

3. Actions タブ → "Morning Earnings Report" → "Run workflow" で手動テスト

#### 手動トリガー
GitHub の Actions タブ → ワークフロー → "Run workflow" ボタン

#### ログ確認
- 成功時: Actions タブで各ステップの出力を確認
- 失敗時: `logs/` フォルダがアーティファクトとしてアップロード（7日保持）

#### 初回 push 手順
```powershell
cd C:\Users\spend\finnhub
git add .
git commit -m "Initial commit: Finnhub earnings morning report"
git push -u origin main
```

### 自動実行（Windows Task Scheduler / 代替）
PCが常時起動している場合の代替手段。`register_task.ps1` を管理者PowerShellで実行。詳細は同ファイル内コメント参照。

### 注意事項
- **PCが起動・サインイン中である必要**があります。スリープ中の発火は不可
- 火-土 8:00 を逃した場合、設定で「次に起動可能な時に実行」を有効化済み
- Finnhub `/calendar/earnings` は **米国市場のみ・直近30日のみ** カバー（無料枠の制約）
- 「前期Revenue」は yfinance（Yahoo Finance）から取得（Finnhub無料枠の制約回避）
- API レート: 60 req/min。発表数が多い日（決算ラッシュ）は数分かかる場合あり

### 動作確認のフロー
```powershell
# 1. セクション1だけテスト (Slack投稿なし、コンソールに出力)
PYTHONIOENCODING=utf-8 python -X utf8 -c "from morning_report import build_upcoming_section; print(build_upcoming_section())"

# 2. 全体実行 (Slackに投稿、当日該当銘柄のPart1/Part2が大量投稿される可能性あり)
python morning_report.py

# 3. ログ確認
type logs\morning_2026-04-15.log
```

## データの制約（Finnhub無料プラン）

| 項目 | 取得可否 |
|---|---|
| EPS実績 vs コンセンサス | ✅ |
| 四半期売上実績 | ✅ |
| 売上コンセンサス | ❌（表には "N/A (無料枠)" と表示） |
| 次四半期/通年ガイダンス | ❌（表では空欄） |
| 対象市場 | 米国株のみ |
| レート制限 | 60 req/min |

有料プラン（$49.99/月〜）に切り替えれば売上コンセンサス・ガイダンスも取得可能。

## 銘柄の追加

`config.py` の `TICKERS` 辞書に追加:
```python
TICKERS = {
    "GOOG": "アルファベット",
    "AAPL": "アップル",
    "MSFT": "マイクロソフト",
    "NVDA": "エヌビディア",  # 追加例
}
```

## トラブルシューティング

- **日本語が豆腐（□）になる**: Windowsに Yu Gothic / Meiryo / MS Gothic のいずれかが入っているか確認。入ってなければ `chart_builder.py` 冒頭の `font.family` を別フォントに変更
- **Slack投稿失敗 (`not_in_channel`)**: Botをチャンネルに `/invite` で招待
- **Finnhub 429エラー**: レート制限超過。60秒待って再実行
- **売上が取れない銘柄がある**: 決算報告のラベル違い。`finnhub_client._find_revenue_in_report` の候補ラベルを調整
- **Anthropic `credit balance is too low`**: <https://console.anthropic.com/settings/billing> でクレジットを追加。コラム記事・X投稿文の生成が全てスキップされるため最優先で対処
- **WP下書き保存で 401 `rest_cannot_create`**: 「このユーザーとして投稿を編集する権限がありません」というメッセージが出る場合、以下の順に確認:
  1. `WP_USERNAME` に対応する **WPユーザーの権限グループを「投稿者(Author)」以上**に変更（購読者・寄稿者には `upload_files` / `edit_posts` がない）
  2. **アプリケーションパスワードは当該ユーザーで発行したもの**か再確認（別ユーザーのApp Passwordだと本エラー）
  3. **Wordfence / iThemes Security / All-In-One WP Security** などが REST API を制限していないか。「REST API を有効化」または本スクリプトのIPを許可リストに追加
  4. サーバー(Apache)が `Authorization` ヘッダを落としている場合は `.htaccess` に以下を追加:
     ```apache
     SetEnvIf Authorization "(.*)" HTTP_AUTHORIZATION=$1
     ```
  - `publish_report.py` は下書き保存の前に `verify_credentials()` を呼び、上記エラーが起きた場合は Slack に通知した上で下書きをスキップします（ジョブ自体は緑のまま続行）
- **`drafts/YYYY-MM-DD.txt` がリポジトリにコミットされない**: Actions → workflow permissions が `Read and write permissions` になっているか確認（Settings → Actions → General → Workflow permissions）
