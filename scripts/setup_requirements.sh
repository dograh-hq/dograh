#!/bin/bash

# Setup script for using pipecat as a git submodule.
#
# Usage:
#   ./scripts/setup_requirements.sh           # default: install runtime deps
#   ./scripts/setup_requirements.sh --dev     # also install pipecat dev deps;
#                                        # skips git submodule update (CI
#                                        # already checks out submodules).

set -euo pipefail

DEV_MODE=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --dev)
            DEV_MODE=1
            shift
            ;;
        *)
            echo "Unknown argument: $1" >&2
            echo "Usage: $0 [--dev]" >&2
            exit 1
            ;;
    esac
done

# Get the project root directory (parent of scripts)
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
DOGRAH_DIR="$(dirname "$SCRIPT_DIR")"

cd "$DOGRAH_DIR"

echo "Setting up pipecat as a git submodule..."

if [ "$DEV_MODE" -eq 0 ]; then
    echo "Initializing git submodules..."
    git submodule update --init --recursive
fi

# Use uv (https://github.com/astral-sh/uv) for ~5-10x faster installs.
# The devcontainer Dockerfile pre-installs uv; this fallback handles CI runners
# and contributor laptops that don't have it yet.
if ! command -v uv >/dev/null 2>&1; then
    echo "Installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
fi

# Install dograh API requirements first so pipecat's extras win on any
# shared transitive dependencies (matches api/Dockerfile and CI workflow).
echo "Installing dograh API requirements..."
uv pip install -r api/requirements.txt

if [ "$DEV_MODE" -eq 1 ]; then
    echo "Installing dograh API dev requirements..."
    uv pip install -r api/requirements.dev.txt
fi

# Install pipecat in editable mode with all extras
echo "Installing pipecat dependencies..."
uv pip install -e ./pipecat[cartesia,deepgram,openai,elevenlabs,groq,google,azure,sarvam,soundfile,silero,webrtc,speechmatics,openrouter,camb,mcp]

if [ "$DEV_MODE" -eq 1 ]; then
    echo "Installing pipecat dev dependencies..."
    uv pip install --group pipecat/pyproject.toml:dev
fi

echo "Setup complete! Requirements are installed."
