# syntax=docker/dockerfile:1
# Multi-stage Dockerfile
# Stage 1: Builder - Install Python dependencies into a venv via uv
# (mirrors .devcontainer/Dockerfile's venv-builder stage).
FROM python:3.13-slim AS builder

WORKDIR /app

# Install git in builder stage (needed for any pip install from git URLs)
RUN apt-get update && apt-get install -y \
    git \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# uv (https://github.com/astral-sh/uv) for ~5-10x faster installs than pip.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

# Build the venv at the path it will live at in the final image, so shebangs
# and console-scripts inside the venv reference the correct runtime location
# after COPY --from.
ENV VIRTUAL_ENV=/opt/venv \
    PATH=/opt/venv/bin:$PATH
RUN python -m venv "$VIRTUAL_ENV"

# Layer 1: API deps. Cache invalidates only when requirements.txt changes.
RUN --mount=type=bind,source=api/requirements.txt,target=/tmp/req.txt \
    --mount=type=cache,target=/root/.cache/uv \
    uv pip install -r /tmp/req.txt

# Layer 2: pipecat deps. Cache invalidates when pipecat source changes.
# After installing pipecat, two hardening tweaks:
#   1. Swap opencv-python (pulled by pipecat[webrtc]) for opencv-python-headless.
#      The non-headless build links against X11/Qt (libxcb*); without those
#      shared libs in the image, `import cv2` fails at runtime.
#   2. Pre-download NLTK's punkt_tab tokenizer so pipecat's text processing
#      doesn't hit the network on first agent run. NLTK auto-finds it under
#      sys.prefix/nltk_data, so it travels with the venv on COPY.
RUN --mount=type=bind,source=pipecat,target=/tmp/pipecat,rw \
    --mount=type=cache,target=/root/.cache/uv \
    uv pip install '/tmp/pipecat[cartesia,deepgram,openai,elevenlabs,groq,google,azure,sarvam,soundfile,silero,webrtc,speechmatics,openrouter,camb,mcp]' \
 && uv pip uninstall opencv-python \
 && uv pip install opencv-python-headless \
 && python -c "import nltk; nltk.download('punkt_tab', download_dir='/opt/venv/nltk_data', quiet=True)"

# Strip cache files, test/example dirs, and type stubs from the venv
RUN find /opt/venv -type f -name '*.pyc' -delete && \
    find /opt/venv -type d -name '__pycache__' -prune -exec rm -rf {} + && \
    find /opt/venv -type f -name '*.pyo' -delete && \
    find /opt/venv -type d \( -name tests -o -name test -o -name examples \) -prune -exec rm -rf {} + && \
    find /opt/venv -name '*.pyi' -delete

# Stage 2: Node deps for ts_validator (built with full node:22-slim, only
# node_modules is copied into the runner).
FROM node:22-slim AS ts-deps
WORKDIR /ts_validator
COPY api/mcp_server/ts_validator/package*.json ./
RUN npm ci --omit=dev && npm cache clean --force

# Stage 3: Static ffmpeg binary (avoids apt ffmpeg pulling mesa/libllvm for
# hardware acceleration we don't use server-side).
FROM debian:trixie-slim AS ffmpeg-static
ARG TARGETARCH
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl ca-certificates xz-utils \
    && curl -fsSL -o /tmp/ffmpeg.tar.xz "https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-${TARGETARCH}-static.tar.xz" \
    && mkdir -p /tmp/ffmpeg \
    && tar -xJf /tmp/ffmpeg.tar.xz -C /tmp/ffmpeg --strip-components=1 \
    && mv /tmp/ffmpeg/ffmpeg /tmp/ffmpeg/ffprobe /usr/local/bin/ \
    && chmod +x /usr/local/bin/ffmpeg /usr/local/bin/ffprobe

# Stage 4: Runtime - Minimal image with only runtime dependencies
FROM python:3.13-slim AS runner

WORKDIR /app

RUN groupadd --system dograh \
 && useradd --system --gid dograh --no-log-init --home-dir /app --shell /usr/sbin/nologin dograh \
 && chown dograh:dograh /app

# Static ffmpeg + ffprobe (used by audio_converter, audio_file_cache, etc.)
COPY --from=ffmpeg-static /usr/local/bin/ffmpeg /usr/local/bin/ffmpeg
COPY --from=ffmpeg-static /usr/local/bin/ffprobe /usr/local/bin/ffprobe

# Node.js 22 binary only (ts_validator subprocess needs node >=22.6 for
# native TypeScript stripping; see api/mcp_server/ts_bridge.py). python:3.13-slim
# already provides libstdc++6, libgcc-s1, and ca-certificates that node needs.
COPY --from=node:22-slim /usr/local/bin/node /usr/local/bin/node

# Copy the populated venv from the builder stage. NLTK data lives at
# /opt/venv/nltk_data and is auto-discovered via sys.prefix.
COPY --from=builder /opt/venv /opt/venv

# Activate the venv for subsequent RUN/CMD layers.
ENV VIRTUAL_ENV=/opt/venv \
    PATH=/opt/venv/bin:$PATH

# Set Python to not generate .pyc files in runtime
ENV PYTHONDONTWRITEBYTECODE=1
# Unbuffered output for better container logging
ENV PYTHONUNBUFFERED=1

# Copy application code (chown at copy-time avoids a duplicate /app layer
# from a later `RUN chown -R`, which would double the on-disk size of /app).
COPY --chown=dograh:dograh ./api ./api
COPY --chown=dograh:dograh ./scripts/start_services_docker.sh ./scripts/start_services_docker.sh

# ts_validator Node deps (built in ts-deps stage with full node:22-slim image).
# The validator runs as a short-lived subprocess from api/mcp_server/ts_bridge.py.
COPY --from=ts-deps --chown=dograh:dograh /ts_validator/node_modules ./api/mcp_server/ts_validator/node_modules

# Product documentation — read at runtime by the MCP docs tools
# (search_dograh_docs / fetch_dograh_doc) so agents can learn Dograh.
COPY --chown=dograh:dograh ./docs ./docs

ENV PYTHONPATH=/app

# Disable file logging in Docker - logs go to stdout for docker logs
ENV LOG_TO_FILE=false

USER dograh

# Expose the port FastAPI will run on
EXPOSE 8000

# Run the FastAPI app with uvicorn
CMD ["./scripts/start_services_docker.sh"]
