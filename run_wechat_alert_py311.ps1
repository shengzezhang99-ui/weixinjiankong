$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = Join-Path $ProjectRoot ".venv-wechat-alert-py311\Scripts\python.exe"

if (-not (Test-Path $Python)) {
    Write-Error "Project venv not found: $Python"
}

Set-Location $ProjectRoot
$env:PYTHONUTF8 = "1"
& $Python -m wechat_alert_assistant
