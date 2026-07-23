# Dograh - Setup & Deployment Guide

This guide covers everything you need to know about developing locally on Windows and deploying to a production server (Ubuntu/Debian VPS).

## 🛠️ Local Development (Windows)

The local development environment uses Docker for the background databases, but runs the Next.js UI and FastAPI backend natively on your machine so you can edit code and see hot-reloads instantly.

### Starting the Local Environment

To start everything (Postgres, Redis, MinIO, Next.js, and Python API), simply run:
```powershell
.\scripts\start_full_dev.ps1
```

> [!NOTE]
> The script will wait 10 seconds for the Docker databases to fully initialize before starting the Python backend. If you see a timeout error when you first open the browser, the Python backend is just taking a bit longer to load. Wait 10-20 seconds and hard refresh (`Ctrl + Shift + R`).

### Stopping & Cleaning Up

To gracefully stop the background processes, run:
```powershell
.\scripts\stop_services.ps1
```

> [!WARNING]
> If your database gets corrupted or passwords get out of sync, you can completely wipe the Docker databases clean and start fresh by running:
> ```powershell
> docker compose down -v
> ```

---

## 🚀 Production Deployment (Remote VPS)

Deploying to production runs all services (including the UI and API) fully containerized inside Docker. 

### Initial Setup

When setting up your server for the very first time, you must run the remote setup script. This script automatically configures Nginx, generates secure passwords, sets up SSL certificates, and configures the TURN server.

```bash
sudo PUBLIC_HOST="yourdomain.com" SERVER_IP="your.vps.ip.address" ./scripts/setup_remote.sh
```

### Pulling Updates & Rebuilding

If you edit code locally (like changing the UI) and push it to GitHub, you need to pull those changes onto your server and rebuild the Docker containers.

1. **Pull the latest code:**
   ```bash
   git pull
   ```
2. **Rebuild the production containers:**
   ```bash
   sudo ./remote_up.sh --build
   ```

> [!TIP]
> You **never** need to run `setup_remote.sh` again unless your IP address or domain name changes. Always use `remote_up.sh --build` for regular code updates!

### Hybrid Deployments (Native Nginx)

If you chose to run Nginx and Certbot natively on your host machine instead of letting Dograh's Docker networking handle it, running `remote_up.sh` will crash with a `port 80 already in use` error (because Docker tries to start its own Nginx container).

For hybrid native-Nginx setups, **do not use `remote_up.sh`**. Instead, rebuild and restart the specific containers manually:

1. **Pull the latest code:**
   ```bash
   git pull
   ```
2. **Rebuild the specific container (e.g., the UI):**
   ```bash
   sudo docker compose build ui
   ```
3. **Restart the container forcefully:**
   ```bash
   sudo docker compose up -d --force-recreate ui
   ```

### Accessing the Database

Your production database is securely hidden inside the Docker network. To view it from your local computer using a tool like TablePlus or DBeaver:

- **Host:** `yourdomain.com` (or your Server IP)
- **Port:** `5435`
- **Database:** `postgres`
- **Username:** `postgres`
- **Password:** Run `grep POSTGRES_PASSWORD .env` on your server to find the secure password.

---

## 🔑 A Note on API Keys

Since Dograh Open Source is fully self-hosted, you must bring your own AI keys! If your agent answers the phone but stays completely silent and hangs up, it is because you have not configured your keys.

There are two ways to set this up:
1. **Global Default:** Paste your `OPENAI_API_KEY` and `DEEPGRAM_API_KEY` directly into the `.env` file.
2. **UI Override:** Paste your keys into the `Model Configurations` settings page in the UI to dynamically override the `.env` defaults.
