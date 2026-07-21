[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [ValidateRange(1, 30)]
    [int]$GracefulShutdownSeconds = 3
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = Join-Path $ProjectRoot ".venv-wechat-alert-py311\Scripts\python.exe"
$ModulePattern = '(?i)(?:^|\s)-m\s+wechat_alert_assistant(?:\s|$)'

if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    throw "Project venv not found: $Python"
}

$PythonPath = [System.IO.Path]::GetFullPath($Python)
$OriginalWhatIfPreference = $WhatIfPreference
try {
    $WhatIfPreference = $false
    Import-Module CimCmdlets -ErrorAction Stop
    $AllProcesses = @(Get-CimInstance Win32_Process)
} finally {
    $WhatIfPreference = $OriginalWhatIfPreference
}
$RootProcesses = @(
    $AllProcesses | Where-Object {
        $_.ExecutablePath -and
        ([System.IO.Path]::GetFullPath($_.ExecutablePath) -ieq $PythonPath) -and
        $_.CommandLine -match $ModulePattern
    }
)

$TargetIds = [System.Collections.Generic.HashSet[int]]::new()
foreach ($Process in $RootProcesses) {
    [void]$TargetIds.Add([int]$Process.ProcessId)
}

# Include child interpreter and desktop-host processes created by the venv launcher.
do {
    $Added = $false
    foreach ($Process in $AllProcesses) {
        if ($TargetIds.Contains([int]$Process.ParentProcessId) -and $TargetIds.Add([int]$Process.ProcessId)) {
            $Added = $true
        }
    }
} while ($Added)

if ($TargetIds.Count -gt 0) {
    $IdList = @($TargetIds | Sort-Object)
    $Description = "WeChat Alert Assistant process tree: $($IdList -join ', ')"
    if ($PSCmdlet.ShouldProcess($Description, "Stop")) {
        foreach ($Id in $IdList) {
            $RunningProcess = Get-Process -Id $Id -ErrorAction SilentlyContinue
            if ($RunningProcess -and $RunningProcess.MainWindowHandle -ne 0) {
                [void]$RunningProcess.CloseMainWindow()
            }
        }

        $Deadline = (Get-Date).AddSeconds($GracefulShutdownSeconds)
        do {
            $RemainingIds = @(
                $IdList | Where-Object { Get-Process -Id $_ -ErrorAction SilentlyContinue }
            )
            if ($RemainingIds.Count -eq 0 -or (Get-Date) -ge $Deadline) {
                break
            }
            Start-Sleep -Milliseconds 200
        } while ($true)

        if ($RemainingIds.Count -gt 0) {
            Stop-Process -Id $RemainingIds -Force -ErrorAction SilentlyContinue
        }
        Write-Host "Stopped existing WeChat Alert Assistant processes."
    }
} else {
    Write-Host "No running WeChat Alert Assistant process found."
}

if ($PSCmdlet.ShouldProcess($PythonPath, "Start WeChat Alert Assistant")) {
    $env:PYTHONUTF8 = "1"
    $StartedProcess = Start-Process `
        -FilePath $PythonPath `
        -ArgumentList @("-m", "wechat_alert_assistant") `
        -WorkingDirectory $ProjectRoot `
        -WindowStyle Hidden `
        -PassThru

    Start-Sleep -Seconds 2
    $StartedProcess.Refresh()
    if ($StartedProcess.HasExited) {
        throw "WeChat Alert Assistant exited during startup. Check logs\app.log."
    }
    Write-Host "WeChat Alert Assistant restarted. PID=$($StartedProcess.Id)"
}
