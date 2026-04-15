@echo off
REM Windows Task Scheduler から呼ばれるエントリポイント
REM 文字コードをUTF-8にしてPython実行
chcp 65001 > nul
cd /d C:\Users\spend\finnhub
python morning_report.py
