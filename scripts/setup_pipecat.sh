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

# Install pipecat in editable mode with all extras
echo "Installing pipecat dependencies..."
pip install -e ./pipecat[cartesia,deepgram,openai,elevenlabs,groq,google,azure,soundfile,silero,webrtc,local-smart-turn-v3]

# Install other requirements
echo "Installing dograh API requirements..."
pip install -r api/requirements.txt

echo "Setup complete! Pipecat is now available as a git submodule."