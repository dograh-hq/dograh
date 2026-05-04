#!/bin/bash

# Setup script for using pipecat as a git submodule

# Get the project root directory (parent of scripts)
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
DOGRAH_DIR="$(dirname "$SCRIPT_DIR")"

cd "$DOGRAH_DIR"

echo "Setting up pipecat as a git submodule..."

# Initialize and update submodules
echo "Initializing git submodules..."
git submodule update --init --recursive

# Install dograh API requirements first so pipecat's extras win on any
# shared transitive dependencies (matches api/Dockerfile and CI workflow).
echo "Installing dograh API requirements..."
pip install -r api/requirements.txt

# Install pipecat in editable mode with all extras
echo "Installing pipecat dependencies..."
pip install -e ./pipecat[cartesia,deepgram,openai,elevenlabs,groq,google,azure,sarvam,soundfile,silero,webrtc,speechmatics,openrouter,camb]

echo "Setup complete! Pipecat is now available as a git submodule."