#!/usr/bin/env pwsh
# Run Alembic database migrations (Windows)

$ErrorActionPreference = 'Stop'

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$BaseDir   = Split-Path -Parent $ScriptDir
Set-Location $BaseDir

$EnvFile = Join-Path $BaseDir 'api/.env'

# Load environment variables
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
} else {
    Write-Host "Error: Environment file $EnvFile not found." -ForegroundColor Red
    exit 1
}

# Run migrations
alembic -c api/alembic.ini upgrade head
