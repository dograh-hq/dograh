# CapRover Quick Start: Dograh UI with Custom Backend URLs

## For @gaurovsoni-bit and Similar CapRover Deployments

This guide solves the exact issue you encountered in [#400](https://github.com/dograh-hq/dograh/issues/400).

### The Problem You Experienced
```
Failed to proxy http://api:8000/api/v1/auth/login [Error: getaddrinfo ENOTFOUND api]
```

You set `BACKEND_URL: http://srv-captain--dograh-api:8000` in CapRover, but the UI kept trying to reach `http://api:8000` (the hardcoded Docker Compose service name).

### The Solution

With the fix in this PR, you now have **two ways** to deploy in CapRover:

---

## Option 1: Simplest - Use Pre-built Image with Environment Variables

This works immediately with the updated code.

### Step 1: Add Environment Variables in CapRover

In your **dograh-ui Captain Definition**:

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
  "ports": ["3010/http"]
}
```

### Step 2: Verify Configuration

SSH into the running container:

```bash
# SSH into dograh-ui container
docker exec dograh-ui env | grep -E "BACKEND|NEXT_PUBLIC"

# Should output:
# BACKEND_URL=http://srv-captain--dograh-api:8000
# NEXT_PUBLIC_BACKEND_URL=https://dograh-api.voices.shifo.org
```

### Step 3: Check Logs for Confirmation

```bash
docker logs dograh-ui | head -20

# Should show:
# [2026-06-02 12:34:56] 🚀 Dograh UI Server - Production Ready
# [2026-06-02 12:34:56] Configuration:
# [2026-06-02 12:34:56]    Backend URL (Server-side): http://srv-captain--dograh-api:8000
# [2026-06-02 12:34:56]    Backend URL (Client-side): https://dograh-api.voices.shifo.org
```

✅ **Done!** UI will now use your custom backend URLs.

---

## Option 2: Build Custom Image in CapRover

If you want to bake the URLs into the image itself.

### Step 1: Push Custom Dockerfile to Your Git Repo

Update your docker-compose.yaml section in the repo with your custom URLs, then push to git.

### Step 2: Configure CapRover to Build from Git

In Captain Definition, change from `sourceType: "image"` to:

```json
{
  "schemaVersion": 2,
  "sourceType": "github",
  "repo": "your-org/your-fork",
  "branch": "main",
  "dockerfilePath": "./ui/Dockerfile",
  "build": {
    "args": {
      "BACKEND_URL": "http://srv-captain--dograh-api:8000",
      "NEXT_PUBLIC_BACKEND_URL": "https://dograh-api.voices.shifo.org"
    }
  },
  "env": {
    "NODE_ENV": "oss",
    "ENABLE_TELEMETRY": "false"
  },
  "ports": ["3010/http"]
}
```

### Step 3: Deploy

CapRover will clone your repo, build the Dockerfile with the specified args, and deploy.

---

## Environment Variables Reference

| Variable | Purpose | Your Value |
|----------|---------|-----------|
| `BACKEND_URL` | Server-side API proxying (internal network) | `http://srv-captain--dograh-api:8000` |
| `NEXT_PUBLIC_BACKEND_URL` | Browser API calls (external URL) | `https://dograh-api.voices.shifo.org` |
| `NODE_ENV` | Node.js environment | `oss` or `production` |
| `ENABLE_TELEMETRY` | Anonymous usage telemetry | `true` or `false` |

---

## Testing Your Setup

### Test 1: Verify Server Can Reach Backend

```bash
# SSH into dograh-ui container
docker exec dograh-ui wget -qO- http://srv-captain--dograh-api:8000/api/v1/health

# Should return JSON response
```

### Test 2: Check Browser-side Configuration

1. Open https://dograh.uivcoded.com in your browser
2. Open Developer Console (F12 → Console tab)
3. Run: `fetch('/api/v1/health').then(r => r.json()).then(console.log)`
4. Should return success response

### Test 3: Try Login

Navigate to login page and try to sign up/login. Should work now!

---

## Troubleshooting

### Still Getting "Failed to proxy" Error?

1. **Check BACKEND_URL is set**: `docker exec dograh-ui env | grep BACKEND_URL`
2. **Verify server is reachable**: `docker exec dograh-ui wget -qO- http://srv-captain--dograh-api:8000/health`
3. **Check container logs**: `docker logs dograh-ui | tail -50`

### CORS Errors in Browser?

Ensure your backend API has CORS configured to accept requests from your UI domain:
```python
# In your backend API configuration
CORS_ORIGINS = [
    "https://dograh.uivcoded.com",
    "http://localhost:3010"  # for local dev
]
```

### "Cannot GET /" Error?

Make sure the UI container actually started:
```bash
docker ps | grep dograh-ui
docker logs dograh-ui
```

---

## What Changed (In This Fix)

### Before
- Dockerfile hardcoded `BACKEND_URL=http://api:8000`
- No way to override without rebuilding image
- CapRover users had to fork the repo and modify Dockerfile
- Error: "getaddrinfo ENOTFOUND api" because `api` service doesn't exist in CapRover

### After
- Dockerfile accepts `BACKEND_URL` as build arg with sensible default
- Entrypoint script reads URL from environment at runtime
- Can deploy with pre-built image + environment variables
- Clear logging shows what URLs are being used
- Works in Docker Compose, CapRover, Kubernetes, etc.

---

## Next Steps

1. ✅ Upgrade dograh-ui to latest version
2. ✅ Set environment variables in CapRover Captain Definition
3. ✅ Verify configuration in container logs
4. ✅ Test login/signup flow
5. ✅ Enjoy your working Dograh UI!

---

## Still Having Issues?

Join us on [Dograh Community Slack](https://join.slack.com/t/dograh-community/shared_invite/zt-3zjb5vwvl-j7hRz3_F1SOn5cH~jm5f5g) - we're happy to help debug CapRover deployments!

Reference this issue: [#400 - dograh-ui docker image not pointing to BACKEND_URL](https://github.com/dograh-hq/dograh/issues/400)
