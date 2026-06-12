#!/bin/sh
# Dograh UI Entrypoint Script
# This script enables runtime configuration of backend URLs for flexible deployments
# Supports: Docker Compose, Docker Swarm, Kubernetes, CapRover, and custom orchestration

set -e

# Configuration
UI_PORT="${PORT:-3010}"
BACKEND_URL="${BACKEND_URL:-http://api:8000}"
NEXT_PUBLIC_BACKEND_URL="${NEXT_PUBLIC_BACKEND_URL:-http://localhost:3010}"

# Logging
log() {
    echo "[$(date +'%Y-%m-%d %H:%M:%S')] $1"
}

log "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
log "🚀 Dograh UI Server - Production Ready"
log "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# Validate and display configuration
log "📋 Configuration:"
log "   UI Port: $UI_PORT"
log "   Backend URL (Server-side): $BACKEND_URL"
log "   Backend URL (Client-side): $NEXT_PUBLIC_BACKEND_URL"
log "   Node Environment: ${NODE_ENV:-production}"
log "   Telemetry: ${ENABLE_TELEMETRY:-true}"

# Health check for backend connectivity (informational only)
if [ "$CHECK_BACKEND" = "true" ] || [ "$CHECK_BACKEND" = "1" ]; then
    log "🔍 Verifying backend connectivity..."
    if command -v curl > /dev/null 2>&1; then
        if curl -sf "${BACKEND_URL}/api/v1/health" > /dev/null 2>&1; then
            log "✅ Backend is reachable at ${BACKEND_URL}"
        else
            log "⚠️  Warning: Backend may not be reachable at ${BACKEND_URL}"
            log "   This could be normal if backend is not yet started"
        fi
    fi
fi

log "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
log "✅ Starting Next.js server on port $UI_PORT..."
log "📍 Access UI at: http://localhost:$UI_PORT"
log "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# Export variables for Node.js process
export PORT=$UI_PORT
export BACKEND_URL=$BACKEND_URL
export NEXT_PUBLIC_BACKEND_URL=$NEXT_PUBLIC_BACKEND_URL
export NODE_ENV=${NODE_ENV:-production}

# Start the Next.js server
# Using the standalone server (specified in next.config.ts)
exec node server.js
