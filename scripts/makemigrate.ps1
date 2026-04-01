#!/usr/bin/env pwsh
# Create a new Alembic migration with autogenerate (Windows)

Param(
    [string]$MigrationName,
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$BaseDir   = Split-Path -Parent $ScriptDir
Set-Location $BaseDir

# Ensure repository root is importable for Alembic env/module resolution.
if ($env:PYTHONPATH) {
    $env:PYTHONPATH = "$BaseDir;$($env:PYTHONPATH)"
} else {
    $env:PYTHONPATH = $BaseDir
}

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

# Prompt for migration name when not provided via parameter
if (-not $MigrationName) {
    $MigrationName = Read-Host "Enter the migration name (minimum 5 characters)"
}

if (-not $MigrationName -or $MigrationName.Length -lt 5) {
    Write-Host "Error: Migration name must be at least 5 characters long." -ForegroundColor Red
    exit 1
}

# Generate the Alembic revision
$cmd = "alembic -c api/alembic.ini revision --autogenerate -m `"$MigrationName`""

if ($DryRun) {
    Write-Host "Dry run: $cmd"
    exit 0
}

alembic -c api/alembic.ini revision --autogenerate -m $MigrationName
