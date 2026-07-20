#!/usr/bin/env pwsh
# Start the full Dograh local development environment

$ErrorActionPreference = 'Stop'

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$BaseDir   = Split-Path -Parent $ScriptDir
Set-Location $BaseDir

Write-Host "====================================================="
Write-Host "Starting Full Dograh Development Environment..."
Write-Host "====================================================="

Write-Host "`n1. Starting background databases (Postgres, Redis, MinIO)..."
docker compose --env-file api/.env up -d postgres redis minio

Write-Host "Waiting 10 seconds for PostgreSQL to initialize..."
Start-Sleep -Seconds 10

Write-Host "`n2. Starting Next.js UI in a new window..."
Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd ui; npm run dev"

Write-Host "`n3. Starting Python Backend in this window..."
.\scripts\start_services_dev.ps1
