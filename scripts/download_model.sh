#!/bin/bash
# Script to copy Silero VAD model during Docker build.
# This ensures the model is available at runtime.

set -e

# Source model path from the pipecat installation
SOURCE_MODEL_PATH="/tmp/pipecat/src/pipecat/audio/vad/data/silero_vad.onnx"

# Target model path in the installed package
USER_SITE=$(python -c "import site; print(site.getusersitepackages())")
VAD_DATA_DIR="$USER_SITE/pipecat/audio/vad/data"
TARGET_MODEL_PATH="$VAD_DATA_DIR/silero_vad.onnx"

echo "Setting up Silero VAD model..."

# Create the VAD data directory if it doesn't exist
mkdir -p "$VAD_DATA_DIR"

# Check if model already exists
if [ -f "$TARGET_MODEL_PATH" ]; then
    echo "✓ Silero VAD model already exists at: $TARGET_MODEL_PATH"
    exit 0
fi

# Check if source model exists
if [ ! -f "$SOURCE_MODEL_PATH" ]; then
    echo "✗ Source model not found at: $SOURCE_MODEL_PATH"
    exit 1
fi

echo "Copying Silero VAD model from pipecat source..."

# Copy the model file
cp "$SOURCE_MODEL_PATH" "$TARGET_MODEL_PATH"

# Verify the model file exists and get its size
if [ -f "$TARGET_MODEL_PATH" ]; then
    FILE_SIZE=$(stat -c%s "$TARGET_MODEL_PATH" 2>/dev/null || stat -f%z "$TARGET_MODEL_PATH" 2>/dev/null)
    echo "✓ Silero VAD model successfully copied to: $TARGET_MODEL_PATH ($FILE_SIZE bytes)"
else
    echo "✗ Failed to copy model file to: $TARGET_MODEL_PATH"
    exit 1
fi

echo "Silero VAD model setup complete!"