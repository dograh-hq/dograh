#!/usr/bin/env pwsh
# Start Dograh services in development mode (Windows)
# Usage: .\scripts\start_services_dev.ps1 [-ArqWorkers 2] [-NoMigrations] [-IncludeTelephonyWorkers]
#
# Note: Telephony workers (ari_manager, campaign_orchestrator) are disabled by
# default on Windows because they use Unix signal handlers not supported by the
# Windows asyncio event loop.

Param(
    [int]$ArqWorkers = 1,
    [switch]$NoMigrations,
    [switch]$IncludeTelephonyWorkers
)

$ErrorActionPreference = 'Stop'

###############################################################################
### CONFIGURATION
###############################################################################

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$BaseDir   = Split-Path -Parent $ScriptDir
Set-Location $BaseDir

$EnvFile    = Join-Path $BaseDir 'api/.env'
$RunDir     = Join-Path $BaseDir 'run'
$LogsRoot   = Join-Path $BaseDir 'logs'
$LatestDir  = Join-Path $LogsRoot 'latest'
$VenvPath   = Join-Path $BaseDir 'venv'

Write-Host "Starting Dograh Services (DEV MODE) in BASE_DIR: $BaseDir"
Write-Host "Auto-reload enabled for api/ directory changes"

###############################################################################
### 1) Load environment variables
###############################################################################

if (Test-Path $EnvFile) {
    Get-Content $EnvFile | ForEach-Object {
        $line = $_.Trim()
        if ($line -and -not $line.StartsWith('#')) {
            $parts = $line -split '=', 2
            if ($parts.Count -eq 2) {
                [Environment]::SetEnvironmentVariable($parts[0].Trim(), $parts[1].Trim().Trim('"'), 'Process')
            }
        }
    }
}

if (-not $env:UVICORN_BASE_PORT) { $env:UVICORN_BASE_PORT = '8000' }

###############################################################################
### 2) Define services
###############################################################################

$serviceSpecs = @()

if ($IncludeTelephonyWorkers) {
    $serviceSpecs += @{ Name = 'ari_manager';           Cmd = "python -m api.services.telephony.ari_manager" }
    $serviceSpecs += @{ Name = 'campaign_orchestrator';  Cmd = "python -m api.services.campaign.campaign_orchestrator" }
}

$serviceSpecs += @{ Name = 'uvicorn'; Cmd = "uvicorn api.app:app --host 0.0.0.0 --port $($env:UVICORN_BASE_PORT) --reload --reload-dir api" }

for ($i = 1; $i -le $ArqWorkers; $i++) {
    $serviceSpecs += @{ Name = "arq$i"; Cmd = "python -m arq api.tasks.arq.WorkerSettings --custom-log-dict api.tasks.arq.LOG_CONFIG" }
}

###############################################################################
### 3) Activate virtual environment
###############################################################################

$VenvActivateScript = Join-Path $VenvPath 'Scripts/Activate.ps1'

if (Test-Path $VenvActivateScript) {
    . $VenvActivateScript
    Write-Host "Virtual environment activated: $VenvPath"
} else {
    Write-Host "Warning: Virtual environment not found at $VenvPath"
    Write-Host "Continuing without virtual environment activation..."
}

###############################################################################
### 4) Stop old services
###############################################################################

New-Item -ItemType Directory -Path $RunDir -Force | Out-Null

foreach ($spec in $serviceSpecs) {
    $pidFile = Join-Path $RunDir "$($spec.Name).pid"
    if (Test-Path $pidFile) {
        $oldPid = (Get-Content $pidFile -Raw).Trim()
        if ($oldPid) {
            $prev = $ErrorActionPreference; $ErrorActionPreference = 'SilentlyContinue'
            & taskkill /PID $oldPid /T /F 2>&1 | Out-Null
            $ErrorActionPreference = $prev
        }
        Remove-Item $pidFile -Force -ErrorAction SilentlyContinue
    }
}

###############################################################################
### 5) Run migrations
###############################################################################

if (-not $NoMigrations) {
    alembic -c (Join-Path $BaseDir 'api/alembic.ini') upgrade head
}

###############################################################################
### 6) Prepare logs
###############################################################################

New-Item -ItemType Directory -Path $LatestDir -Force | Out-Null
Get-ChildItem $LatestDir -Filter '*.log' -ErrorAction SilentlyContinue | Remove-Item -Force -ErrorAction SilentlyContinue

###############################################################################
### 7) Start services
###############################################################################

foreach ($spec in $serviceSpecs) {
    $name    = $spec.Name
    $logPath = Join-Path $LatestDir "$name.log"
    $pidFile = Join-Path $RunDir "$name.pid"

    Write-Host "-> Starting $name"

    $wrapped = "cd /d `"$BaseDir`" && $($spec.Cmd) >> `"$logPath`" 2>&1"
    $proc = Start-Process cmd.exe -ArgumentList '/c', $wrapped -PassThru -WindowStyle Hidden

    Set-Content -Path $pidFile -Value $proc.Id
    Write-Host "   PID $($proc.Id) -> $logPath"
}

###############################################################################
### 8) Summary
###############################################################################

Write-Host ""
Write-Host "------------------------------------------------------"
Write-Host "Mode: DEVELOPMENT (auto-reload enabled)"
Write-Host ""
foreach ($spec in $serviceSpecs) {
    $procId = (Get-Content (Join-Path $RunDir "$($spec.Name).pid") -Raw).Trim()
    Write-Host "  $($spec.Name) (PID $procId) -> logs/latest/$($spec.Name).log"
}
Write-Host ""
Write-Host "Health: curl.exe http://localhost:$($env:UVICORN_BASE_PORT)/api/v1/health"
Write-Host "Logs:   Get-Content logs/latest/*.log -Wait"
Write-Host "Stop:   .\scripts\stop_services.ps1"
Write-Host "------------------------------------------------------"
