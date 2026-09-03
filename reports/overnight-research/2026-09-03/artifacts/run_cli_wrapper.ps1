$ErrorActionPreference = "Stop"

$repoRoot = "D:\Desktop\TradingAgents-CN"
$pythonExe = "C:\Users\KSO\AppData\Local\Programs\Python\Python310\python.exe"
$stdoutPath = "D:\Desktop\TradingAgents-CN\reports\overnight-research\2026-09-03\artifacts\run_cli.stdout.log"
$stderrPath = "D:\Desktop\TradingAgents-CN\reports\overnight-research\2026-09-03\artifacts\run_cli.stderr.log"
$statePath = "D:\Desktop\TradingAgents-CN\reports\overnight-research\2026-09-03\artifacts\run_cli.state.json"
$summaryPath = "D:\Desktop\TradingAgents-CN\reports\overnight-research\2026-09-03\morning-summary.md"

Set-Location -LiteralPath $repoRoot

if (Test-Path -LiteralPath $stdoutPath) {
    Remove-Item -LiteralPath $stdoutPath -Force
}
if (Test-Path -LiteralPath $stderrPath) {
    Remove-Item -LiteralPath $stderrPath -Force
}

$exitCode = 1
try {
    $process = Start-Process -FilePath $pythonExe -ArgumentList @("research\\crypto_backtest\\run_overnight_multi_asset_research_v2.py") -WorkingDirectory $repoRoot -WindowStyle Hidden -RedirectStandardOutput $stdoutPath -RedirectStandardError $stderrPath -PassThru
    $process.WaitForExit()
    $exitCode = $process.ExitCode
} catch {
    $_ | Out-String | Set-Content -LiteralPath $stderrPath -Encoding UTF8
    $exitCode = 1
}

$state = if (Test-Path -LiteralPath $statePath) {
    Get-Content -Raw -LiteralPath $statePath | ConvertFrom-Json
} else {
    [pscustomobject]@{}
}

$summaryItem = Get-Item -LiteralPath $summaryPath -ErrorAction SilentlyContinue
$state | Add-Member -NotePropertyName finishedAt -NotePropertyValue ((Get-Date).ToString("o")) -Force
$state | Add-Member -NotePropertyName exitCode -NotePropertyValue $exitCode -Force
$state | Add-Member -NotePropertyName status -NotePropertyValue ($(if ($exitCode -eq 0) { "completed" } else { "failed" })) -Force
$state | Add-Member -NotePropertyName summaryLastWriteTime -NotePropertyValue ($(if ($summaryItem) { $summaryItem.LastWriteTime.ToString("o") } else { $null })) -Force
$state | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $statePath -Encoding UTF8

exit $exitCode
