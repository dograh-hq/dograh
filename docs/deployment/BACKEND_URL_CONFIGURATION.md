# Dograh UI Deployment Guide: Backend URL Configuration

## Overview

The Dograh UI (Next.js) requires proper configuration of backend URLs for both server-side and client-side communication. This guide explains how to configure it for different deployment scenarios.

## Architecture: Two Backend URLs

Dograh UI uses **two separate backend URL configurations**:

### 1. `BACKEND_URL` (Server-side)
- **Purpose**: Used by Next.js server for API rewrites and Server-Side Rendering (SSR)
- **When it's used**: When requests go through the Next.js server
- **Scope**: Server-side only (not exposed to browsers)
- **Example values**:
  - Local Docker Compose: `http://api:8000`
  - CapRover: `http://srv-captain--dograh-api:8000`
  - Remote server: `https://api.internal.company.com:8000`

### 2. `NEXT_PUBLIC_BACKEND_URL` (Client-side)
- **Purpose**: Used by browser-side JavaScript for API calls
- **When it's used**: When React components/JavaScript make direct API requests
- **Scope**: Embedded in browser JavaScript, visible in client code
- **Example values**:
  - Local: `http://localhost:3010` or `http://localhost:8000`
  - Production: `https://dograh.company.com` (same domain to avoid CORS)

---

## Deployment Scenarios

### Scenario 1: Docker Compose (Local Development)

**Default behavior**: Everything is auto-configured.

```bash
# Just run it!
docker compose up
```

**What happens**:
- UI builds with `BACKEND_URL=http://api:8000` (internal Docker network)
- UI builds with `NEXT_PUBLIC_BACKEND_URL=http://localhost:3010`
- Browser requests go through localhost
- Server requests use Docker service name

---

### Scenario 2: Docker Compose with Custom Backend URL

**Use case**: Backend running on different host or port

```bash
# Option A: Using environment variables
export BACKEND_URL=http://dograh-api.internal:9000
export NEXT_PUBLIC_BACKEND_URL=https://dograh.company.com
docker compose up

# Option B: Using .env file
cat > .env << EOF
BACKEND_URL=http://dograh-api.internal:9000
NEXT_PUBLIC_BACKEND_URL=https://dograh.company.com
EOF
docker compose up
```

**How it works**:
- docker-compose.yaml passes variables as build args to Dockerfile
- Dockerfile uses these values at build time
- Variables are also available at runtime for configuration

---

### Scenario 3: CapRover Deployment (Pre-built Images)

**Issue**: CapRover uses pre-built registry images, can't modify build args.

**Solution**: Set environment variables that are read by the entrypoint script.

#### Step 1: Add Environment Variables in CapRover UI

Go to your dograh-ui Captain Definition:

```json
{
  "schemaVersion": 2,
  "dockerfilePath": "./Dockerfile",
  "sourceType": "image",
  "imageName": "dograhai/dograh-ui:latest",
  "env": {
    "BACKEND_URL": "http://srv-captain--dograh-api:8000",
    "NEXT_PUBLIC_BACKEND_URL": "https://dograh-api.voices.shifo.org",
    "NODE_ENV": "oss",
    "ENABLE_TELEMETRY": "false"
  },
  "ports": ["3010/http"],
  "volumes": []
}
```

#### Step 2: Verify Configuration

SSH into the running container:

```bash
docker exec dograh-ui env | grep BACKEND
# Should output:
# BACKEND_URL=http://srv-captain--dograh-api:8000
# NEXT_PUBLIC_BACKEND_URL=https://dograh-api.voices.shifo.org
```

#### Step 3: Check Server Logs

View container logs to confirm correct configuration:

```bash
docker logs dograh-ui

# Expected output:
# [2026-06-02 12:34:56] Configuration:
# [2026-06-02 12:34:56]    Backend URL (Server-side): http://srv-captain--dograh-api:8000
# [2026-06-02 12:34:56]    Backend URL (Client-side): https://dograh-api.voices.shifo.org
```

---

### Scenario 4: Kubernetes Deployment

**Use case**: Deploying with Helm or raw K8s manifests

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: dograh-ui-config
data:
  BACKEND_URL: "http://dograh-api:8000"
  NEXT_PUBLIC_BACKEND_URL: "https://dograh.company.com"
  NODE_ENV: "oss"
  ENABLE_TELEMETRY: "true"

---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: dograh-ui
spec:
  replicas: 2
  template:
    spec:
      containers:
      - name: ui
        image: dograhai/dograh-ui:latest
        envFrom:
        - configMapRef:
            name: dograh-ui-config
        ports:
        - containerPort: 3010
        livenessProbe:
          httpGet:
            path: /
            port: 3010
          initialDelaySeconds: 30
          periodSeconds: 10
```

---

### Scenario 5: Docker Build with Custom Backend

**Use case**: Building your own image with specific backend URL

```bash
# Build with custom backend URL
docker build \
  --build-arg BACKEND_URL=https://api.internal.company.com \
  --build-arg NEXT_PUBLIC_BACKEND_URL=https://dograh.company.com \
  -t my-dograh-ui:latest \
  -f ui/Dockerfile .

# Run the image
docker run \
  -e BACKEND_URL=https://api.internal.company.com \
  -e NEXT_PUBLIC_BACKEND_URL=https://dograh.company.com \
  -p 3010:3010 \
  my-dograh-ui:latest
```

---

## Troubleshooting

### Issue: "Failed to proxy http://api:8000" Error

**Cause**: `BACKEND_URL` contains service name `api` that doesn't resolve in your environment.

**Fix for CapRover**:
```json
{
  "env": {
    "BACKEND_URL": "http://srv-captain--dograh-api:8000"  // Use your actual service name
  }
}
```

### Issue: Browser Can't Reach Backend (CORS Error)

**Cause**: `NEXT_PUBLIC_BACKEND_URL` points to unreachable URL from browser.

**Solution**: Ensure the URL is accessible from your browser's network:
```json
{
  "env": {
    "NEXT_PUBLIC_BACKEND_URL": "https://dograh-api.voices.shifo.org"  // External URL
  }
}
```

### Issue: Server Works, But Client Requests Fail

**Cause**: Mismatch between server-side and client-side URLs.

**Solution**: 
- **Server requests** use `BACKEND_URL` (internal network)
- **Browser requests** use `NEXT_PUBLIC_BACKEND_URL` (external URL)
- These can be different, but ensure both URLs reach the same backend

```json
{
  "env": {
    "BACKEND_URL": "http://srv-captain--dograh-api:8000",          // Internal
    "NEXT_PUBLIC_BACKEND_URL": "https://dograh-api.voices.shifo.org"  // External
  }
}
```

### Issue: Health Check Fails on Startup

**Cause**: Backend not running when UI starts.

**Fix**: Increase startup delay in your orchestration tool:
- Docker Compose: Set `start_period` in healthcheck
- CapRover: Increase "Initial Delay (s)" in health checks
- Kubernetes: Increase `initialDelaySeconds`

---

## Environment Variable Reference

| Variable | Usage | Required | Example |
|----------|-------|----------|---------|
| `BACKEND_URL` | Next.js server-side API proxying | Yes | `http://api:8000` |
| `NEXT_PUBLIC_BACKEND_URL` | Browser-side API calls | Yes | `https://dograh.company.com` |
| `NODE_ENV` | Node.js environment | No | `production` |
| `ENABLE_TELEMETRY` | Enable/disable telemetry | No | `true` |
| `POSTHOG_KEY` | PostHog analytics key | No | (auto-configured) |
| `CHECK_BACKEND` | Verify backend on startup | No | `false` |

---

## Quick Reference: Common Deployments

### Docker Compose (Default)
```bash
docker compose up
```
✅ Auto-configured for localhost development

### CapRover with HTTP Backend
```json
"env": {
  "BACKEND_URL": "http://srv-captain--dograh-api:8000",
  "NEXT_PUBLIC_BACKEND_URL": "https://dograh.company.com"
}
```

### Remote Server (HTTPS)
```bash
BACKEND_URL=https://api.internal.company.com:8443 \
NEXT_PUBLIC_BACKEND_URL=https://dograh.company.com \
docker compose up
```

### Kubernetes
```bash
kubectl set env deployment/dograh-ui \
  BACKEND_URL=http://dograh-api:8000 \
  NEXT_PUBLIC_BACKEND_URL=https://dograh.company.com
```

---

## Testing Your Configuration

### 1. Check Server-side Configuration

```bash
# SSH into container
docker exec dograh-ui env | grep BACKEND

# Check server logs for config output
docker logs dograh-ui | grep "Backend URL"
```

### 2. Check Client-side Configuration

```bash
# Open browser developer console
# Go to http://localhost:3010
# Run in console:
console.log(window.__CONFIG__)  // Or check Network tab for API requests
```

### 3. Test API Connectivity

From browser console:
```javascript
// Test server-side proxying (through Next.js)
fetch('/api/v1/health').then(r => r.json()).then(console.log)

// Test client-side backend (direct)
fetch('https://dograh-api.voices.shifo.org/api/v1/health')
  .then(r => r.json())
  .then(console.log)
  .catch(e => console.error('CORS or connection error:', e))
```

---

## Best Practices

1. **Use Internal URLs for Server**: `BACKEND_URL` can be internal service names (Docker/K8s specific)
2. **Use External URLs for Client**: `NEXT_PUBLIC_BACKEND_URL` must be reachable from browsers
3. **HTTPS in Production**: Always use HTTPS URLs in production deployments
4. **CORS Configuration**: If server and client URLs differ, ensure backend CORS is configured correctly
5. **Environment Parity**: Keep dev, staging, and production configurations consistent

---

## Support

For issues with backend URL configuration:
1. Check container logs: `docker logs dograh-ui`
2. Verify environment variables: `docker exec dograh-ui env | grep BACKEND`
3. Test connectivity: Use the "Testing Your Configuration" section above
4. Join Slack: [Dograh Community Slack](https://join.slack.com/t/dograh-community/shared_invite/zt-3zjb5vwvl-j7hRz3_F1SOn5cH~jm5f5g)
5. Open issue: [GitHub Issues](https://github.com/dograh-hq/dograh/issues)
