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
    uv pip install '/tmp/pipecat[cartesia,deepgram,openai,elevenlabs,groq,google,azure,sarvam,soundfile,silero,webrtc,speechmatics,openrouter,camb,mcp,inworld,smallest]' \
 && uv pip uninstall opencv-python \
 && uv pip install opencv-python-headless \
 && python -c "import nltk; nltk.download('punkt_tab', download_dir='/opt/venv/nltk_data', quiet=True)"

# Layer 3: Patch mcp SSE charset
# Fix: without charset=utf-8 in Content-Type, HTTP clients fallback to ISO-8859-1
# and corrupt non-ASCII characters (Turkish: ş, ğ, ü, ö, ı, ç).
RUN sed -i 's/CONTENT_TYPE_SSE = "text\/event-stream"/CONTENT_TYPE_SSE = "text\/event-stream; charset=utf-8"/' \
    /opt/venv/lib/python3.13/site-packages/mcp/server/streamable_http.py

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

# Stage 3: Static ffmpeg binary
FROM debian:trixie-slim AS ffmpeg-static
ARG TARGETARCH
ARG BTBN_TAG=autobuild-2026-05-31-13-22
ARG BTBN_REV=N-124714-g49a77d37be
RUN set -eu ; \
    apt-get update && apt-get install -y --no-install-recommends \
        curl ca-certificates xz-utils ; \
    rm -rf /var/lib/apt/lists/* ; \
    case "${TARGETARCH}" in \
      amd64) btbn_arch=linux64 ; \
             sha256=ee052121296e6479325e09c6097d48e72a4af472d18c2b94388b5405dcde6cce ;; \
      arm64) btbn_arch=linuxarm64 ; \
             sha256=e97545305043794cdf7b698d713e29291464e0c35bb8e0f3ff1f62e4c56eedd6 ;; \
      *) echo "unsupported TARGETARCH: ${TARGETARCH}" >&2 ; exit 1 ;; \
    esac ; \
    url="https://github.com/BtbN/FFmpeg-Builds/releases/download/${BTBN_TAG}/ffmpeg-${BTBN_REV}-${btbn_arch}-gpl.tar.xz" ; \
    mkdir -p /tmp/ffmpeg ; cd /tmp/ffmpeg ; \
    echo "Downloading ffmpeg (${BTBN_TAG}) from ${url}" ; \
    curl -fsSL --connect-timeout 20 --speed-limit 1024 --speed-time 30 \
         --max-time 600 --retry 3 --retry-delay 5 --retry-all-errors \
         -o ffmpeg.tar.xz "${url}" ; \
    echo "${sha256}  ffmpeg.tar.xz" | sha256sum -c - ; \
    tar -xJf ffmpeg.tar.xz ; \
    ffmpeg_bin="$(find /tmp/ffmpeg -type f -name ffmpeg | head -n1)" ; \
    ffprobe_bin="$(find /tmp/ffmpeg -type f -name ffprobe | head -n1)" ; \
    [ -n "${ffmpeg_bin}" ] && [ -n "${ffprobe_bin}" ] ; \
    mv "${ffmpeg_bin}" "${ffprobe_bin}" /usr/local/bin/ ; \
    chmod +x /usr/local/bin/ffmpeg /usr/local/bin/ffprobe ; \
    rm -rf /tmp/ffmpeg

# Stage 4: Runtime - Minimal image with only runtime dependencies
FROM python:3.13-slim AS runner

WORKDIR /app

RUN groupadd --system dograh \
 && useradd --system --gid dograh --no-log-init --home-dir /app --shell /usr/sbin/nologin dograh \
 && chown dograh:dograh /app

COPY --from=ffmpeg-static /usr/local/bin/ffmpeg /usr/local/bin/ffmpeg
COPY --from=ffmpeg-static /usr/local/bin/ffprobe /usr/local/bin/ffprobe

COPY --from=node:22-slim /usr/local/bin/node /usr/local/bin/node

COPY --from=builder /opt/venv /opt/venv

ENV VIRTUAL_ENV=/opt/venv \
    PATH=/opt/venv/bin:$PATH

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

COPY --chown=dograh:dograh ./api ./api

COPY --chown=dograh:dograh \
     ./scripts/start_services_docker.sh \
     ./scripts/run_migrate.sh \
     ./scripts/run_web.sh \
     ./scripts/run_arq_worker.sh \
     ./scripts/run_ari_manager.sh \
     ./scripts/run_campaign_orchestrator.sh \
     ./scripts/drain_web.sh \
     ./scripts/

COPY --from=ts-deps --chown=dograh:dograh /ts_validator/node_modules ./api/mcp_server/ts_validator/node_modules

COPY --chown=dograh:dograh ./docs ./docs

ENV PYTHONPATH=/app

ENV LOG_TO_FILE=false

USER dograh

EXPOSE 8000

CMD ["./scripts/start_services_docker.sh"]
