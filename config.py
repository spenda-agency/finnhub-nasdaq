"""プロジェクト設定"""
from pathlib import Path

# 対象銘柄と日本語表示名
TICKERS = {
    "GOOG": "アルファベット",
    "AAPL": "アップル",
    "MSFT": "マイクロソフト",
}

# グラフ設定
HISTORICAL_QUARTERS = 12  # 下段グラフの四半期数

# 出力先
OUTPUT_DIR = Path(__file__).parent / "output"
OUTPUT_DIR.mkdir(exist_ok=True)

# カラーパレット（添付画像に合わせる）
COLOR_BAR = "#F4A460"      # オレンジ（売上バー）
COLOR_LINE = "#1F4E79"     # 青（成長率ライン）
COLOR_HEADER = "#E8E8E8"   # グレー（テーブルヘッダ）
COLOR_JUDGE_OK = "#FCE4D6" # 薄オレンジ（判定セル背景）
