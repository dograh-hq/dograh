#!/bin/bash

# Script to build Docker images with remote deployment fixes and push to Docker Hub for testing

set -e

echo "🐳 Building and Testing Docker Images with Remote Deployment Fixes"
echo "=================================================================="

# Configuration
DOCKER_HUB_USERNAME="${DOCKER_HUB_USERNAME:-yourusername}"  # Replace with your Docker Hub username
IMAGE_TAG="${IMAGE_TAG:-test-remote-fix}"
BUILD_CONTEXT="."

echo "📋 Configuration:"
echo "   Docker Hub Username: $DOCKER_HUB_USERNAME"
echo "   Image Tag: $IMAGE_TAG"
echo "   Build Context: $BUILD_CONTEXT"
echo ""

# Check if user is logged in to Docker Hub
echo "🔑 Checking Docker Hub login..."
if docker info | grep -q "Username.*$DOCKER_HUB_USERNAME"; then
    echo "✅ Already logged in to Docker Hub as $DOCKER_HUB_USERNAME"
else
    echo "🔐 Please log in to Docker Hub:"
    docker login
fi

echo ""

# Setup Docker Buildx for multi-arch builds (like GitHub Actions)
echo "🔧 Setting up Docker Buildx for multi-architecture builds..."
if ! docker buildx ls | grep -q "multiarch"; then
    echo "Creating new buildx instance..."
    docker buildx create --name multiarch --driver docker-container --use
    docker buildx inspect --bootstrap
else
    echo "Using existing buildx instance..."
    docker buildx use multiarch
fi
echo "✅ Docker Buildx ready for linux/amd64,linux/arm64"
echo ""

# Build API image
echo "🔨 Building API image..."
docker build -f api/Dockerfile -t $DOCKER_HUB_USERNAME/dograh-api:$IMAGE_TAG $BUILD_CONTEXT
if [ $? -eq 0 ]; then
    echo "✅ API image built successfully"
else
    echo "❌ API image build failed"
    exit 1
fi

echo ""

# Build UI image  
echo "🔨 Building UI image..."
docker build -f ui/Dockerfile -t $DOCKER_HUB_USERNAME/dograh-ui:$IMAGE_TAG $BUILD_CONTEXT
if [ $? -eq 0 ]; then
    echo "✅ UI image built successfully"
else
    echo "❌ UI image build failed"
    exit 1
fi

echo ""

# Push images to Docker Hub
echo "📤 Pushing images to Docker Hub..."

echo "Pushing API image..."
docker push $DOCKER_HUB_USERNAME/dograh-api:$IMAGE_TAG
if [ $? -eq 0 ]; then
    echo "✅ API image pushed successfully"
else
    echo "❌ API image push failed"
    exit 1
fi

echo "Pushing UI image..."
docker push $DOCKER_HUB_USERNAME/dograh-ui:$IMAGE_TAG
if [ $? -eq 0 ]; then
    echo "✅ UI image pushed successfully"
else
    echo "❌ UI image push failed"
    exit 1
fi

echo ""

# Create test docker-compose file
echo "📝 Creating test docker-compose file..."
cat > docker-compose.test.yaml << EOF
# Test docker-compose file for remote deployment
# Use this on your remote server to test the images

services:
  postgres:
    image: postgres:17
    environment:
      POSTGRES_USER: postgres
      POSTGRES_PASSWORD: postgres
      POSTGRES_DB: postgres
    ports:
      - "5432:5432"
    volumes:
      - postgres_data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U postgres"]
      interval: 3s
      timeout: 3s
      retries: 10
    networks:
      - app-network

  redis:
    image: redis:7
    ports:
      - "6379:6379"
    command: >
      --requirepass redissecret
    volumes:
      - redis_data:/data
    healthcheck:
      test: ["CMD", "redis-cli", "-a", "redissecret", "ping"]
      interval: 3s
      timeout: 10s
      retries: 10
    networks:
      - app-network

  minio:
    image: minio/minio
    container_name: minio
    command: server /data --console-address ":9001"
    environment:
      MINIO_ROOT_USER: minioadmin
      MINIO_ROOT_PASSWORD: minioadmin
      MINIO_API_CORS_ALLOW_ORIGIN: "*"
    ports:
      - "127.0.0.1:9000:9000"
      - "127.0.0.1:9001:9001"
    volumes:
      - minio-data:/data
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:9000/minio/health/live"]
      interval: 10s
      timeout: 5s
      retries: 5
    networks:
      - app-network

  api:
    image: $DOCKER_HUB_USERNAME/dograh-api:$IMAGE_TAG
    volumes:
      - shared-tmp:/tmp
    environment:
      ENVIRONMENT: "local"
      LOG_LEVEL: "INFO"
      DATABASE_URL: "postgresql+asyncpg://postgres:postgres@postgres:5432/postgres"
      REDIS_URL: "redis://:redissecret@redis:6379"
      ENABLE_AWS_S3: "false"
      MINIO_ENDPOINT: "minio:9000"
      MINIO_ACCESS_KEY: "minioadmin"
      MINIO_SECRET_KEY: "minioadmin"
      MINIO_BUCKET: "voice-audio"
      MINIO_SECURE: "false"
      ENABLE_TRACING: "false"
      ENABLE_TELEMETRY: "false"
      SENTRY_DSN: "https://3acdb63d5f1f70430953353b82de61e0@o4509486225096704.ingest.us.sentry.io/4510152922693632"
    ports:
      - "8000:8000"
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy
      minio:
        condition: service_healthy
      cloudflared:
        condition: service_started
    healthcheck:
      test: ["CMD-SHELL", 'python -c "import urllib.request; urllib.request.urlopen(\"http://localhost:8000/api/v1/health\").read()"']
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 60s
    networks:
      - app-network

  ui:
    image: $DOCKER_HUB_USERNAME/dograh-ui:$IMAGE_TAG
    environment:
      BACKEND_URL: "http://api:8000"
      NODE_ENV: "oss"
      # Optional manual override for testing
      # DOGRAH_BACKEND_HOST: "YOUR_SERVER_IP"
      ENABLE_TELEMETRY: "false"
      POSTHOG_KEY: "phc_ItizB1dP6yv7ZYobbcqrpxTdbomDA8hJFSEmAMdYvIr"
      POSTHOG_HOST: "https://us.posthog.com"
      SENTRY_DSN: "https://d9387fed5f80e90781f1dbd9b2c0994c@o4509486225096704.ingest.us.sentry.io/4510124708200448"
    ports:
      - "3010:3010"
    depends_on:
      api:
        condition: service_healthy
    healthcheck:
      test: ["CMD-SHELL", "wget --no-verbose --tries=1 --spider http://localhost:3010 || exit 1"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 30s
    networks:
      - app-network

  cloudflared:
    image: cloudflare/cloudflared:latest
    container_name: cloudflared-tunnel
    command: tunnel --no-autoupdate --url http://api:8000 --metrics 0.0.0.0:2000
    ports:
      - "2000:2000"
    networks:
      - app-network

volumes:
  postgres_data:
  redis_data:
  minio-data:
    driver: local
  shared-tmp:
    driver: local

networks:
  app-network:
    driver: bridge
EOF

echo "✅ Test docker-compose file created: docker-compose.test.yaml"

echo ""
echo "🎉 Build and Push Complete!"
echo ""
echo "📋 Images Created:"
echo "   API: $DOCKER_HUB_USERNAME/dograh-api:$IMAGE_TAG"
echo "   UI:  $DOCKER_HUB_USERNAME/dograh-ui:$IMAGE_TAG"
echo ""
echo "🚀 Testing Instructions:"
echo "========================"
echo ""
echo "1. Copy 'docker-compose.test.yaml' to your remote server"
echo ""
echo "2. On your remote server, run:"
echo "   # Automatic detection (recommended)"
echo "   docker-compose -f docker-compose.test.yaml up"
echo ""
echo "   # Manual override (if auto-detection fails)"
echo "   DOGRAH_BACKEND_HOST=\$(hostname -I | cut -d' ' -f1) docker-compose -f docker-compose.test.yaml up"
echo ""
echo "3. Access from your local machine:"
echo "   http://YOUR_REMOTE_SERVER_IP:3010"
echo ""
echo "4. Check browser console for backend detection logs:"
echo "   Look for '[Dograh] Backend detection' messages"
echo ""
echo "✅ The remote deployment fix should work automatically!"

# Show image sizes
echo ""
echo "📊 Image Information:"
echo "===================="
docker images | grep "$DOCKER_HUB_USERNAME/dograh-.*:$IMAGE_TAG" || echo "Images not found locally (may have been cleaned up)"