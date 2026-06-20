#!/usr/bin/env pwsh
# Setup script for using pipecat as a git submodule (Windows).
#
# Usage:
#   ./scripts/setup_requirements.ps1          # default: install runtime deps
#   ./scripts/setup_requirements.ps1 -Dev     # also install pipecat dev deps;
#                                        # skips git submodule update (CI
#                                        # already checks out submodules).

[CmdletBinding()]
param(
    [switch]$Dev
)

$ErrorActionPreference = 'Stop'

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$BaseDir   = Split-Path -Parent $ScriptDir
Set-Location $BaseDir

# Fail early if the active Python is not 3.12 or 3.13. uv pip installs into
# whichever interpreter resolves here (the active venv, or PATH python), so a
# mismatch surfaces as confusing wheel/build errors much later.
$PythonBin = if ($env:PYTHON) { $env:PYTHON } else { 'python' }
if (-not (Get-Command $PythonBin -ErrorAction SilentlyContinue)) {
    Write-Error "'$PythonBin' not found on PATH. Activate the project venv (or set `$env:PYTHON) and retry."
    exit 1
}

$PyMajMin = & $PythonBin -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")'
if ($PyMajMin -ne '3.12' -and $PyMajMin -ne '3.13') {
    $PyPath = (Get-Command $PythonBin).Source
    Write-Error "Python 3.12 or 3.13 required, found $PyMajMin at $PyPath. Activate a venv built with python3.12 or python3.13 and retry."
    exit 1
}

Write-Host "Setting up pipecat as a git submodule..."

if (-not $Dev) {
    Write-Host "Initializing git submodules..."
    git submodule update --init --recursive
}

# Use uv (https://github.com/astral-sh/uv) for ~5-10x faster installs.
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host "Installing uv..."
    Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression
    $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
}

# Install dograh API requirements first so pipecat's extras win on any
# shared transitive dependencies (matches api/Dockerfile and CI workflow).
Write-Host "Installing dograh API requirements..."
uv pip install -r api/requirements.txt

if ($Dev) {
    Write-Host "Installing dograh API dev requirements..."
    uv pip install -r api/requirements.dev.txt
}

# Install pipecat in editable mode with all extras
Write-Host "Installing pipecat dependencies..."
uv pip install -e './pipecat[cartesia,deepgram,openai,elevenlabs,groq,google,azure,sarvam,soundfile,silero,webrtc,speechmatics,openrouter,camb,mcp,inworld,smallest]'

if ($Dev) {
    Write-Host "Installing pipecat dev dependencies..."
    uv pip install --group pipecat/pyproject.toml:dev
}

Write-Host "Setup complete! Requirements are installed."
