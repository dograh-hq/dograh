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
uv pip install -e './pipecat[cartesia,deepgram,openai,elevenlabs,groq,google,azure,sarvam,soundfile,silero,webrtc,speechmatics,openrouter,camb]'

if ($Dev) {
    Write-Host "Installing pipecat dev dependencies..."
    uv pip install --group pipecat/pyproject.toml:dev
}

Write-Host "Setup complete! Requirements are installed."
