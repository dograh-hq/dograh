#!/bin/sh

# Simple Docker entrypoint for manual backend host override
# Only performs replacement if DOGRAH_BACKEND_HOST is actually set

set -e

echo "Starting Dograh UI..."

# Check if manual backend host is provided
if [ -n "$DOGRAH_BACKEND_HOST" ]; then
    echo "Using manual backend host: $DOGRAH_BACKEND_HOST"
    
    # Replace placeholder in runtime config with actual host
    sed -i "s/__DOGRAH_BACKEND_HOST_PLACEHOLDER__/$DOGRAH_BACKEND_HOST/g" /app/public/runtime-config.js
    
    echo "Backend host configured: $DOGRAH_BACKEND_HOST:8000"
else
    echo "Using automatic backend detection"
fi

# Start the Next.js application
exec "$@"