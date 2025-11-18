# Remote Deployment Guide

Deploy Dograh on a remote server and access it from your local machine.

## The Problem

When deploying on remote servers, the frontend tries to connect to `localhost:8000` instead of your server's IP.

## The Solution

**Automatic detection with manual override support!**

```bash
# Automatic detection (works for most setups)
docker-compose up

# Manual override (for complex networks)
DOGRAH_BACKEND_HOST=192.168.1.100 docker-compose up
```

## Quick Start

### 1. Deploy on Your Server

```bash
# Option A: Automatic detection (recommended)
docker-compose up

# Option B: Manual override (for complex networks)
DOGRAH_BACKEND_HOST=192.168.1.100 docker-compose up
```

### 2. Access from Your Local Machine

Open your browser to: `http://YOUR_SERVER_IP:3010`

(Replace `YOUR_SERVER_IP` with your actual server IP)

## Examples

### Local Development
```bash
docker-compose up
# Access: http://localhost:3010
# Backend auto-detects: localhost:8000
```

### Home Network Server
```bash
docker-compose up
# Access: http://192.168.1.100:3010
# Backend auto-detects: 192.168.1.100:8000
```

### Cloud Server
```bash
docker-compose up
# Access: http://203.0.113.10:3010
# Backend auto-detects: 203.0.113.10:8000
```

### ZimaOS
```bash
docker-compose up
# Access: http://192.168.1.150:3010
# Backend auto-detects: 192.168.1.150:8000
```

## Troubleshooting

### "Can't connect to backend"
1. **Try manual override**: `DOGRAH_BACKEND_HOST=YOUR_IP docker-compose up`
2. **Check browser console** for `[Dograh] Backend detection` logs
3. **Verify IP is correct**: `ping YOUR_IP`
4. **Check ports are open**: `telnet YOUR_IP 8000`
5. **Confirm Docker containers are running**: `docker-compose ps`

### "Page not loading"
- ✅ Check frontend port: `telnet YOUR_IP 3010`
- ✅ Verify firewall settings on your server
- ✅ Try accessing from the server itself: `curl localhost:3010`

### "Auto-detection not working"
For complex networks, use manual override:
```bash
# Find your IP
hostname -I | cut -d' ' -f1  # Linux
ifconfig | grep inet         # macOS/Linux
ipconfig                     # Windows

# Use manual override
DOGRAH_BACKEND_HOST=YOUR_IP docker-compose up
```

## How It Works

The frontend automatically detects the backend URL:

```typescript
// Client-side JavaScript automatically detects
const hostname = window.location.hostname;
const backendUrl = hostname === 'localhost' 
  ? 'http://localhost:8000'
  : `http://${hostname}:8000`;
```

- **localhost access**: Uses `localhost:8000`
- **Remote access**: Uses same hostname with port 8000
- **No configuration**: Works automatically everywhere

## Testing Your Setup

Run our validation script:
```bash
./test-manual-ip.sh
```

This confirms the configuration works for both local and remote scenarios.

---

**That's it!** One environment variable solves the remote deployment problem. Simple, reliable, and works everywhere.