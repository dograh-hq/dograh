#!/usr/bin/env pwsh
# Stop Dograh services started by start_services_dev.ps1 (Windows)

$ErrorActionPreference = 'Stop'

###############################################################################
### CONFIGURATION
###############################################################################

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$BaseDir   = Split-Path -Parent $ScriptDir
$RunDir    = Join-Path $BaseDir 'run'

Set-Location $BaseDir
Write-Host "Stopping Dograh Services in BASE_DIR: $BaseDir"

###############################################################################
### HELPER
###############################################################################

function Stop-ProcessTree([int]$ProcessId) {
    # taskkill /T kills the entire process tree. Temporarily relax error
    # preference so that a "process not found" message on stderr does not
    # terminate the script.
    $prev = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'SilentlyContinue'
        & taskkill /PID $ProcessId /T /F 2>&1 | Out-Null
    } finally {
        $ErrorActionPreference = $prev
    }
}

###############################################################################
### STOP SERVICES
###############################################################################

if (-not (Test-Path $RunDir)) {
    Write-Host "No run directory found at $RunDir"
    Write-Host "No services appear to be running."
    exit 0
}

$pidFiles = Get-ChildItem $RunDir -Filter '*.pid' -ErrorAction SilentlyContinue
if (-not $pidFiles) {
    Write-Host "No PID files found in $RunDir"
    Write-Host "No services appear to be running."
    exit 0
}

$stoppedCount = 0
$failedCount  = 0

foreach ($pidFile in $pidFiles) {
    $name   = $pidFile.BaseName
    $oldPid = (Get-Content $pidFile.FullName -Raw).Trim()

    $proc = Get-Process -Id ([int]$oldPid) -ErrorAction SilentlyContinue
    if ($proc) {
        Write-Host "Stopping $name (PID $oldPid)..."
        Stop-ProcessTree ([int]$oldPid)
        Start-Sleep -Seconds 2

        $still = Get-Process -Id ([int]$oldPid) -ErrorAction SilentlyContinue
        if ($still) {
            Write-Host "  Warning: $name did not exit cleanly"
            $failedCount++
        } else {
            Write-Host "  Stopped $name"
            $stoppedCount++
        }
    } else {
        # The tracked cmd.exe may have exited but child processes may still run.
        # Best-effort cleanup via taskkill tree kill.
        Stop-ProcessTree ([int]$oldPid)
        Write-Host "Service $name (PID $oldPid) is not running"
    }

    Remove-Item $pidFile.FullName -Force -ErrorAction SilentlyContinue
}

###############################################################################
### SUMMARY
###############################################################################

Write-Host ""
Write-Host "------------------------------------------------------"
Write-Host "Stopped $stoppedCount service(s)"
if ($failedCount -gt 0) {
    Write-Host "Failed to stop $failedCount service(s)"
}
Write-Host "------------------------------------------------------"
