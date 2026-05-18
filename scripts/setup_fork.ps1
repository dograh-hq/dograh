#!/usr/bin/env pwsh
# Contributor bootstrap (Windows). Run this once after cloning your fork.
# Configures git remotes (origin = your fork, upstream = dograh-hq/dograh),
# initializes the pipecat submodule, creates the Python venv, and copies
# the .env templates.

$ErrorActionPreference = 'Stop'

$UpstreamUrl    = 'https://github.com/dograh-hq/dograh.git'
$CanonicalHttps = $UpstreamUrl
$CanonicalSsh   = 'git@github.com:dograh-hq/dograh.git'

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$BaseDir   = Split-Path -Parent $ScriptDir
Set-Location $BaseDir

# Must be inside a git repo
try {
    git rev-parse --git-dir | Out-Null
} catch {
    Write-Host 'Error: not a git repository. Run this from inside your cloned fork.' -ForegroundColor Red
    exit 1
}

Write-Host '+==============================================================+' -ForegroundColor Blue
Write-Host '|              Dograh Contributor Bootstrap                    |' -ForegroundColor Blue
Write-Host '+==============================================================+' -ForegroundColor Blue
Write-Host ''

function Get-RemoteUrl([string]$Name) {
    try { return (git remote get-url $Name 2>$null) } catch { return $null }
}

###############################################################################
### 1) Configure git remotes
###############################################################################

Write-Host '[1/4] Configuring git remotes' -ForegroundColor Blue

$currentOrigin = Get-RemoteUrl 'origin'

$needsForkPrompt = $false
if (-not $currentOrigin) {
    $needsForkPrompt = $true
} elseif ($currentOrigin -eq $CanonicalHttps -or $currentOrigin -eq $CanonicalSsh) {
    Write-Host "origin currently points at the canonical repo ($currentOrigin)." -ForegroundColor Yellow
    Write-Host 'You should push to your own fork, not the canonical repo.'   -ForegroundColor Yellow
    $needsForkPrompt = $true
}

if ($needsForkPrompt) {
    Write-Host 'Enter your fork URL (e.g. https://github.com/<YOUR_HANDLE>/dograh.git):' -ForegroundColor Yellow
    $forkUrl = (Read-Host '>').Trim()
    if (-not $forkUrl) {
        Write-Host 'Fork URL is required.' -ForegroundColor Red
        exit 1
    }
    if ($currentOrigin) {
        git remote remove origin | Out-Null
    }
    git remote add origin $forkUrl
    Write-Host "OK origin set to $forkUrl" -ForegroundColor Green
} else {
    Write-Host "OK origin already set: $currentOrigin" -ForegroundColor Green
}

$existingUpstream = Get-RemoteUrl 'upstream'
if (-not $existingUpstream) {
    git remote add upstream $UpstreamUrl
    Write-Host "OK upstream set to $UpstreamUrl" -ForegroundColor Green
} elseif ($existingUpstream -ne $UpstreamUrl -and $existingUpstream -ne $CanonicalSsh) {
    Write-Host "upstream currently points at $existingUpstream (expected $UpstreamUrl)." -ForegroundColor Yellow
    $reset = (Read-Host 'Reset upstream to dograh-hq/dograh? [y/N]').Trim()
    if ($reset -match '^[Yy]') {
        git remote set-url upstream $UpstreamUrl
        Write-Host "OK upstream reset to $UpstreamUrl" -ForegroundColor Green
    } else {
        Write-Host 'Leaving upstream alone.' -ForegroundColor Yellow
    }
} else {
    Write-Host 'OK upstream already set' -ForegroundColor Green
}

Write-Host ''
git remote -v
Write-Host ''

###############################################################################
### 2) Initialize submodules
###############################################################################

Write-Host '[2/4] Initializing pipecat submodule' -ForegroundColor Blue
git submodule update --init --recursive
Write-Host 'OK submodules initialized' -ForegroundColor Green
Write-Host ''

###############################################################################
### 3) Python venv
###############################################################################

Write-Host '[3/4] Python virtual environment' -ForegroundColor Blue
$VenvPath = Join-Path $BaseDir 'venv'
$VenvActivate = Join-Path $VenvPath 'Scripts/Activate.ps1'

if (Test-Path $VenvActivate) {
    Write-Host "OK venv already exists at $VenvPath" -ForegroundColor Green
} else {
    $py = $null
    foreach ($candidate in @('python3.13', 'python', 'python3')) {
        if (Get-Command $candidate -ErrorAction SilentlyContinue) {
            $py = $candidate
            break
        }
    }
    if (-not $py) {
        Write-Host 'Error: no python interpreter found on PATH. Install Python 3.13.' -ForegroundColor Red
        exit 1
    }
    & $py -m venv $VenvPath
    $ver = (& $py --version)
    Write-Host "OK venv created at $VenvPath using $py ($ver)" -ForegroundColor Green
}
Write-Host ''

###############################################################################
### 4) .env files
###############################################################################

Write-Host '[4/4] Environment files' -ForegroundColor Blue
$pairs = @(
    @{ Src = 'api/.env.example'; Dst = 'api/.env' },
    @{ Src = 'ui/.env.example';  Dst = 'ui/.env'  }
)
foreach ($p in $pairs) {
    if (Test-Path $p.Dst) {
        Write-Host "OK $($p.Dst) already exists" -ForegroundColor Green
    } elseif (Test-Path $p.Src) {
        Copy-Item $p.Src $p.Dst
        Write-Host "OK created $($p.Dst) from $($p.Src)" -ForegroundColor Green
    } else {
        Write-Host "WARN $($p.Src) not found, skipping" -ForegroundColor Yellow
    }
}
Write-Host ''

###############################################################################
### Done
###############################################################################

Write-Host '+==============================================================+' -ForegroundColor Green
Write-Host '|                  Bootstrap complete                          |' -ForegroundColor Green
Write-Host '+==============================================================+' -ForegroundColor Green
Write-Host ''
Write-Host 'Next steps:' -ForegroundColor Yellow
Write-Host '  1. .\venv\Scripts\Activate.ps1'
Write-Host '  2. .\scripts\setup_requirements.ps1'
Write-Host '  3. cd ui; npm install; cd ..'
Write-Host '  4. docker compose -f docker-compose-local.yaml up -d'
Write-Host '  5. .\scripts\start_services_dev.ps1'
Write-Host ''
Write-Host 'To sync your fork with upstream later:' -ForegroundColor Yellow
Write-Host '  git fetch upstream'
Write-Host '  git checkout main; git merge upstream/main'
Write-Host '  git push origin main'
Write-Host ''
