param(
    [string]$ISCCPath = "",
    [string]$ScriptPath = ".\installer\wechat_alert_assistant.iss"
)

$ErrorActionPreference = "Stop"

function Resolve-InnoCompiler {
    param([string]$PreferredPath)

    if ($PreferredPath -and (Test-Path -LiteralPath $PreferredPath)) {
        return (Resolve-Path -LiteralPath $PreferredPath).Path
    }

    $command = Get-Command ISCC.exe -ErrorAction SilentlyContinue
    if ($command) {
        return $command.Source
    }

    $candidates = @(
        "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
        "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
        "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
    )

    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate) {
            return (Resolve-Path -LiteralPath $candidate).Path
        }
    }

    throw "ISCC.exe not found. Install Inno Setup 6 or pass -ISCCPath."
}

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not $root) {
    $root = (Get-Location).Path
}

$scriptFullPath = if ([System.IO.Path]::IsPathRooted($ScriptPath)) {
    $ScriptPath
} else {
    Join-Path $root $ScriptPath
}
$scriptFullPath = (Resolve-Path -LiteralPath $scriptFullPath).Path

$distDir = Join-Path $root "dist\微信强提醒助手"
$mainExe = Join-Path $distDir "微信强提醒助手.exe"
if (-not (Test-Path -LiteralPath $mainExe)) {
    throw "Packaged app not found: $mainExe. Run PyInstaller first."
}

$iscc = Resolve-InnoCompiler -PreferredPath $ISCCPath

Push-Location -LiteralPath (Split-Path -Parent $scriptFullPath)
try {
    & $iscc $scriptFullPath
    if ($LASTEXITCODE -ne 0) {
        throw "Inno Setup failed with exit code $LASTEXITCODE."
    }
} finally {
    Pop-Location
}

$installerPath = Join-Path (Split-Path -Parent $scriptFullPath) "微信强提醒助手_Setup_1.0.0.exe"
if (-not (Test-Path -LiteralPath $installerPath)) {
    throw "Installer was not created: $installerPath"
}

$item = Get-Item -LiteralPath $installerPath
Write-Host "Installer created:"
Write-Host $item.FullName
Write-Host ("Size: {0:N2} MB" -f ($item.Length / 1MB))
