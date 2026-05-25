# =============================================================================
# Stage 1: venv-builder
# Minimal image whose only job is to populate the venv. Uses the same Python
# source as the runtime stage (deadsnakes) so the symlinks inside the venv
# (e.g. venv/bin/python -> /usr/bin/python3.13) stay valid after COPY --from.
# Everything in this stage except the venv itself is discarded.
# =============================================================================
FROM ubuntu:24.04 AS venv-builder

RUN apt-get update \
    && export DEBIAN_FRONTEND=noninteractive \
    && apt-get install -y --no-install-recommends \
        build-essential \
        curl \
        ca-certificates \
        git \
        libpq-dev \
        pkg-config \
        software-properties-common \
    && add-apt-repository -y ppa:deadsnakes/ppa \
    && apt-get install -y --no-install-recommends \
        python3.13 \
        python3.13-venv \
        python3.13-dev \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

# Build the venv at the path it will live at in the final image, so shebangs
# and console-scripts inside the venv reference the correct runtime location
# once the seed step rsyncs them into the named volume.
ENV VIRTUAL_ENV=/workspaces/dograh/venv \
    PATH=/workspaces/dograh/venv/bin:$PATH
RUN mkdir -p /workspaces/dograh && python3.13 -m venv "$VIRTUAL_ENV"

# Layer 1: API deps. Cache invalidates only when these two files change.
RUN --mount=type=bind,source=api/requirements.txt,target=/tmp/req.txt \
    --mount=type=bind,source=api/requirements.dev.txt,target=/tmp/req.dev.txt \
    --mount=type=cache,target=/root/.cache/uv \
    uv pip install -r /tmp/req.txt -r /tmp/req.dev.txt

# Layer 2: pipecat deps. Cache invalidates when pipecat source changes.
# After installing pipecat, two hardening tweaks (mirrored from api/Dockerfile):
#   1. Swap opencv-python (pulled by pipecat[webrtc]) for opencv-python-headless.
#      The non-headless build links against X11/Qt (libxcb*); without those
#      shared libs in the image, `import cv2` fails at runtime.
#   2. Pre-download NLTK's punkt_tab tokenizer so pipecat's text processing
#      doesn't hit the network on first agent run. NLTK auto-finds it under
#      sys.prefix/nltk_data, so it travels with the venv on COPY/rsync.
RUN --mount=type=bind,source=pipecat,target=/tmp/pipecat,rw \
    --mount=type=cache,target=/root/.cache/uv \
    uv pip install '/tmp/pipecat[cartesia,deepgram,openai,elevenlabs,groq,google,azure,sarvam,soundfile,silero,webrtc,speechmatics,openrouter,camb,mcp]' \
 && uv pip install --group /tmp/pipecat/pyproject.toml:dev \
 && uv pip uninstall opencv-python \
 && uv pip install opencv-python-headless \
 && python -c "import nltk; nltk.download('punkt_tab', download_dir='/workspaces/dograh/venv/nltk_data', quiet=True)"


# =============================================================================
# Stage 2: runtime devcontainer image
# Inherits the devcontainer base (vscode user, sudo, etc.) and brings only the
# populated venv across from the builder stage.
# =============================================================================
FROM mcr.microsoft.com/devcontainers/base:ubuntu-24.04

RUN apt-get update \
    && export DEBIAN_FRONTEND=noninteractive \
    && apt-get install -y --no-install-recommends \
        build-essential \
        curl \
        ffmpeg \
        git \
        jq \
        libpq-dev \
        pkg-config \
        postgresql-client \
        procps \
        redis-tools \
        rsync \
        software-properties-common \
    && add-apt-repository -y ppa:deadsnakes/ppa \
    && apt-get install -y --no-install-recommends \
        python3.13 \
        python3.13-venv \
        python3.13-dev \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# uv is still needed at runtime so post-create.sh can do the editable
# pipecat install (and any ad-hoc `uv pip install` users might run).
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

# Bring the populated venv across. At runtime, the named volume in
# docker-compose.yml shadows /workspaces/dograh/venv; post-create.sh
# rsyncs from /opt/venv-template into the (initially empty) volume,
# comparing build-stamps so an image rebuild that changed deps re-seeds.
COPY --from=venv-builder --chown=vscode:vscode /workspaces/dograh/venv /opt/venv-template
RUN date -u +%s > /opt/venv-template/.build-stamp \
 && chown vscode:vscode /opt/venv-template/.build-stamp

ENV VIRTUAL_ENV=/workspaces/dograh/venv \
    PATH=/workspaces/dograh/venv/bin:$PATH
