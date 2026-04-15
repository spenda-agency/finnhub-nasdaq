# Windows Task Scheduler に「火-土 8時」のタスクを登録する一回限りのスクリプト
# 実行方法: 管理者権限のPowerShellで
#   cd C:\Users\spend\finnhub
#   .\register_task.ps1
#
# 削除する場合:
#   Unregister-ScheduledTask -TaskName "FinnhubMorningReport" -Confirm:$false

$TaskName = "FinnhubMorningReport"
$ScriptDir = "C:\Users\spend\finnhub"
$BatPath = Join-Path $ScriptDir "run_morning.bat"

if (-not (Test-Path $BatPath)) {
    Write-Error "run_morning.bat が見つかりません: $BatPath"
    exit 1
}

# 既存タスクがあれば一旦削除（再登録のため）
$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host "既存タスクを削除します: $TaskName"
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

# Action: バッチ実行
$action = New-ScheduledTaskAction `
    -Execute $BatPath `
    -WorkingDirectory $ScriptDir

# Trigger: 毎週 火・水・木・金・土 の 08:00
$trigger = New-ScheduledTaskTrigger `
    -Weekly `
    -DaysOfWeek Tuesday,Wednesday,Thursday,Friday,Saturday `
    -At "08:00"

# Settings: PCがスリープから起きている時に実行、開始時刻を逃したら可能なら起動
$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Hours 1) `
    -RestartCount 2 `
    -RestartInterval (New-TimeSpan -Minutes 5)

# Principal: 現在のユーザーで実行
$principal = New-ScheduledTaskPrincipal `
    -UserId "$env:USERDOMAIN\$env:USERNAME" `
    -LogonType Interactive `
    -RunLevel Limited

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Principal $principal `
    -Description "Daily earnings calendar + Part1/Part2 reports (Tue-Sat 8am)"

Write-Host "登録完了。次回起動: " -NoNewline
(Get-ScheduledTask -TaskName $TaskName).Triggers[0].StartBoundary
Write-Host "状態確認: Get-ScheduledTask -TaskName $TaskName"
Write-Host "手動テスト: Start-ScheduledTask -TaskName $TaskName"
