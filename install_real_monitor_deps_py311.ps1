$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = Join-Path $ProjectRoot ".venv-wechat-alert-py311\Scripts\python.exe"

if (-not (Test-Path $Python)) {
    Write-Error "Project venv not found: $Python"
}

Set-Location $ProjectRoot
& $Python -m pip install --default-timeout=180 -i https://pypi.tuna.tsinghua.edu.cn/simple -r requirements-real-monitor.txt
& $Python -m pip check
