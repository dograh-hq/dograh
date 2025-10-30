# Multi-stage Dockerfile
# Stage 1: Builder - Install Python dependencies
FROM python:3.12-slim AS builder

WORKDIR /app

# Install git in builder stage (needed for pip install from git)
RUN apt-get update && apt-get install -y \
    git \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Copy and install requirements
COPY api/requirements.txt .

# Install dependencies to user directory for easy copying
RUN pip install --user --no-cache-dir -r requirements.txt && \
    # Clean up pip cache after installation
    rm -rf /root/.cache/pip

# Copy and install pipecat from local submodule
COPY pipecat /tmp/pipecat
RUN pip install --user --no-cache-dir '/tmp/pipecat[cartesia,deepgram,openai,elevenlabs,groq,google,azure,soundfile,silero,webrtc]' && \
    # Clean up pip cache and temporary pipecat directory
    rm -rf /root/.cache/pip /tmp/pipecat

    
# Remove unnecessary Python cache files from installed packages
RUN find /root/.local -type f -name '*.pyc' -delete && \
    find /root/.local -type d -name '__pycache__' -delete && \
    find /root/.local -type f -name '*.pyo' -delete

# Stage 2: Runtime - Minimal image with only runtime dependencies
FROM python:3.12-slim AS runner

WORKDIR /app

# Only install ffmpeg (runtime dependency)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Copy Python packages from builder stage
COPY --from=builder /root/.local /root/.local

# Make sure scripts in .local are available
ENV PATH=/root/.local/bin:$PATH

# Set Python to not generate .pyc files in runtime
ENV PYTHONDONTWRITEBYTECODE=1
# Unbuffered output for better container logging
ENV PYTHONUNBUFFERED=1

# Copy application code
COPY ./api ./api
COPY ./scripts/start_services.sh ./scripts/start_services.sh

ENV PYTHONPATH=/app

# Expose the port FastAPI will run on
EXPOSE 8000

# Run the FastAPI app with uvicorn
CMD ["bash", "-c", "./scripts/start_services.sh && tail -f ./logs/latest/*.log"]