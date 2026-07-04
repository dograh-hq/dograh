# Local MPS Gateway — Docker image
# Standalone document processing + workflow generation, replacing Dograh MPS cloud.
#
# Build:  docker build -f api/Dockerfile.mps -t dograh-local-mps .
# Run:    docker run -p 9000:9000 dograh-local-mps

FROM python:3.12-slim

WORKDIR /app

# System deps for document parsing
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Python deps
RUN pip install --no-cache-dir \
    fastapi==0.115.6 \
    uvicorn==0.34.0 \
    httpx==0.28.1 \
    pypdf==5.1.0 \
    python-docx==1.1.2 \
    numpy==2.2.1 \
    pydantic==2.10.4 \
    python-multipart==0.0.19

# turbovec for 8x compressed vector search (4-bit quantized)
RUN pip install --no-cache-dir turbovec==0.7.0

# Optional: Gemini embeddings support
# RUN pip install --no-cache-dir google-generativeai==0.8.4

# Copy the local MPS modules
COPY api/services/local_mps_gateway.py /app/
COPY api/services/local_mps_workflow_generator.py /app/
COPY api/services/local_mps_doc_processor.py /app/

ENV LOCAL_MPS_PORT=9000
ENV LOCAL_MPS_DATA_DIR=/data/docstore
ENV LOCAL_MPS_EMBEDDING_PROVIDER=openai
ENV LOCAL_MPS_EMBEDDING_MODEL=text-embedding-3-small

RUN mkdir -p /data/docstore

EXPOSE 9000

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD curl -f http://localhost:9000/health || exit 1

CMD ["uvicorn", "local_mps_gateway:app", "--host", "0.0.0.0", "--port", "9000"]
