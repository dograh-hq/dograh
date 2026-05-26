# syntax=docker/dockerfile:1.7
# Voice-engine overlay image.
#
# Build context = repo root (so we can copy the upstream Dograh tree later
# without restructuring). For now this image only runs our overlay/ code —
# Stream S5 follow-up wires the actual Dograh SDK in.
#
# Build: docker build -f overlay/Dockerfile -t voice-engine .

FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app

# --- deps ---
FROM base AS deps
COPY overlay/requirements.txt ./overlay/requirements.txt
RUN pip install -r overlay/requirements.txt

# --- run ---
FROM base AS run
COPY --from=deps /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=deps /usr/local/bin /usr/local/bin
COPY overlay ./overlay
# Make `overlay` importable as a top-level package.
ENV PYTHONPATH=/app
EXPOSE 7000
# FastMCP defaults to stdio; HTTP transport is configured via env in
# server.py once the Dograh SDK lands.
CMD ["python", "-m", "overlay.mcp_server.server"]
