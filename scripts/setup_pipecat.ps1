#!/usr/bin/env pwsh
# Setup script for using pipecat as a git submodule (Windows)

$ErrorActionPreference = 'Stop'

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$BaseDir   = Split-Path -Parent $ScriptDir
Set-Location $BaseDir

Write-Host "Setting up pipecat as a git submodule..."

# Initialize and update submodules
Write-Host "Initializing git submodules..."
git submodule update --init --recursive

# Install dograh API requirements first so pipecat's extras win on any
# shared transitive dependencies (matches api/Dockerfile and CI workflow).
Write-Host "Installing dograh API requirements..."
pip install -r api/requirements.txt

# Install pipecat in editable mode with all extras
Write-Host "Installing pipecat dependencies..."
pip install -e './pipecat[cartesia,deepgram,openai,elevenlabs,groq,google,azure,sarvam,soundfile,silero,webrtc,speechmatics,openrouter,camb]'

Write-Host "Setup complete! Pipecat is now available as a git submodule."
