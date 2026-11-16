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

# Install other requirements first so pipecat submodule wins any version conflicts
echo "Installing dograh API requirements..."
pip install -r api/requirements.txt

# See setup_requirements.sh. The newest submodule tag is v1.1.0, so without
# this override the merged 1.16.0 tree is versioned as 1.1.1.dev.
export SETUPTOOLS_SCM_PRETEND_VERSION_FOR_PIPECAT_AI=1.16.0
echo "Installing pipecat dependencies..."
pip install -e ./pipecat[cartesia,deepgram,openai,elevenlabs,groq,google,azure,sarvam,soundfile,silero,webrtc,speechmatics,openrouter,camb,mcp,inworld,smallest,aws-nova-sonic]

echo "Installing Gnani STT adapter..."
pip install -r api/requirements.gnani-stt.txt

echo "Setup complete! Pipecat is now available as a git submodule."
