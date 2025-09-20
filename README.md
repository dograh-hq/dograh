


# Dograh AI

<h3 align="center">⭐ <strong>If you find value in this project, PLEASE STAR IT to help others discover our FOSS platform!</strong></h3>


<p align="center">
  <a href="LICENSE">
    <img src="https://img.shields.io/badge/license-BSD%202--Clause-blue.svg" alt="License: BSD 2-Clause">
  </a>
  <a href="https://join.slack.com/t/dograh-community/shared_invite/zt-3czr47sw5-MSg1J0kJ7IMPOCHF~03auQ">
    <img src="https://img.shields.io/badge/chat-on%20Slack-4A154B?logo=slack" alt="Slack Community">
  </a>
  <a href="https://www.docker.com/">
    <img src="https://img.shields.io/badge/docker-ready-blue?logo=docker" alt="Docker Ready">
  </a>
</p>

The fastest way to build voice bots - get started with any voice AI use case in under 2 minutes (our hard SLA standards).
Build voice agents in just one line or drag-and-drop, then test them using AI personas that mimic real customer calls. It's 100% open source, self-hosted if you want, and never hides a line of code- ever. The project has a strong commitment to **100% open source** and every line of code is released in the open.
Maintained by YC alumni and exit founders, we're making sure the future of voice AI stays open, not monopolized.

## 🎥 Demo Video
[![Watch a quick demo video](https://img.youtube.com/vi/LK8mvK5TH2Q/1.jpg)](https://www.youtube.com/watch?v=LK8mvK5TH2Q)

## 🚀 Get Started

The only command you need to run:

```bash
# Download and start Dograh
curl -o docker-compose.yaml https://raw.githubusercontent.com/dograh-hq/dograh/main/docker-compose.yaml && docker compose up
```

> **Note**  
> First startup may take 2-3 minutes to download all images. Once running, open http://localhost:3000 to create your first AI voice assistant!  
> For prerequisites, port issues, or troubleshooting, see the [Prerequisites and Troubleshooting](#-prerequisites-and-troubleshooting) section below.
 
### 🎙️ Your First Voice Bot

1. **Open Dashboard**: Launch [http://localhost:3000](http://localhost:3000) on your browser
2. **Choose Call Type**: Select **Inbound** or **Outbound** calling.
3. **Name Your Bot**: Use a short two-word name (e.g., *Lead Qualification*).
4. **Describe Use Case**: In 5–10 words (e.g., *Screen insurance form submissions for purchase intent*).
5. **Launch**: Your bot is ready! Open the bot and click **Web Call** to talk to it.
6. **No API Keys Needed**: We auto-generate Dograh API keys so you can start immediately. You can switch to your own keys anytime.
7. **Default Access**: Includes Dograh’s own LLMs, STT, and TTS stack by default.
8. **Bring Your Own Keys**: Optionally connect your own API keys for LLMs, STT, TTS, or telephony providers like Twilio.

## Quick Summary
⚡ 2-Minute Setup: Hard SLA standards - from zero to working voice bot in under 2 minutes
- 🔧 Minimal setup: Just [run docker command](#get-started) and you're live 
- 🤖 AI Testing Personas: Test your bots with LoopTalk AI that mimics real customer interactions
- 🔓 100% Open Source: Every line of code is open - no hidden logic, no black boxes
- 🔄 Flexible Integration: Bring your own LLM, TTS, or STT - or use Dograh’s API’s
- ☁️ Self-Host or Cloud: Run locally or use our hosted version at app.dograh.com


##  Features
### Voice Capabilities
- Telephony: Built-in Twilio integration (easily add others)
- Languages: English support (expandable to other languages)
- Custom Models: Bring your own TTS/STT models
- Real-time Processing: Low-latency voice interactions

### Developer Experience
- Zero Config Start: Auto-generated API keys for instant testing
- Python-Based: Built on Python for easy customization
- Docker-First: Containerized for consistent deployments
- Modular Architecture: Swap components as needed

### Testing & Quality
- LoopTalk (Beta): Create AI personas to test your voice agents
- Workflow Testing: Test specific workflow IDs with automated calls
- Real-world Simulation: AI personas that mimic actual customer behavior

## 🔧 Prerequisites and Troubleshooting

### Prerequisites

To run Dograh AI locally, make sure you have the following installed:

- [Docker](https://docs.docker.com/get-docker/) (version 20.10 or later)
- [curl](https://curl.se/download.html) – usually preinstalled on macOS/Linux

> **Note**  
> Docker Compose is included with Docker Desktop. Make sure Docker is running before you begin.

### Required Ports

Ensure these ports are available:
- `3000` - Web UI
- `8000` - API Server
- `5432` - PostgreSQL
- `6379` - Redis
- `9000` - MinIO (S3-compatible storage)
- `9001` - MinIO Console

### Checking Port Availability

```bash
# Check if a port is in use (replace 3000 with the port number)
lsof -i :3000
```

### Freeing Up Ports

#### When a port is already in use:
```bash
# Check what's using the port first
lsof -i :3000

# Then kill the process (may require sudo on Linux)
kill -9 $(lsof -t -i :3000)
```

#### When Docker containers are using the ports (with auto-restart enabled):

**Step 1:** Stop all running containers
```bash
docker stop $(docker ps -q)
```

**Step 2:** Disable restart policy for all containers
This prevents containers from automatically restarting:
```bash
docker update --restart=no $(docker ps -a -q)
```

**Step 3:** Verify

Check that no containers are running:
```bash
docker ps
```

Check restart policies (should show 'no' for each container):
```bash
docker inspect -f '{{.Name}} - {{.HostConfig.RestartPolicy.Name}}' $(docker ps -a -q)
```

### Stopping Dograh Services

```bash
# Stop services
docker compose down

# Stop and remove all data (full cleanup)
docker compose down -v
```

## Configuration
Dograh automatically generates API keys on first run, but you can use your own keys. 
  - OPENAI_API_KEY=your_key_here
  - TWILIO_ACCOUNT_SID=your_sid_here
  - TWILIO_AUTH_TOKEN=your_token_here

## Architecture
Architecture diagram  *(coming soon)* 

## Deployment Options
### Local Development
Refer [prerequisites](#prerequisites) and [first steps](#-get-started)

### Production (Self-Hosted)
Production guide coming soon. [Drop in a message](https://join.slack.com/t/dograh-community/shared_invite/zt-3czr47sw5-MSg1J0kJ7IMPOCHF~03auQ) for assistance.

### Cloud Version
Visit [https://www.dograh.com](https://www.dograh.com/) for our managed cloud offering.

## 📚Documentation
Full documentation is in progress. For now, this README will get you started.

##  🤝Community & Support
- GitHub Issues: Report bugs or request features
- Slack: Our Slack community is not just for support — it’s the cornerstone of Dograh AI contributions. Here, you can:
  - Connect with maintainers and other contributors
  - Discuss issues and features before coding
  - Get help with setup and debugging
  - Stay up to date with contribution sprints


👉 Join us → Dograh Community Slack
## Tech Stack
- FastAPI
- Pipecat
- LiveKit
- PostgreSQL
- Next.js
- XYFlow React
- Inbuilt Twilio integration
- Flexible back-end: switch to any LLM, TTS, or STT

## 🙌 Contributing
We love contributions! Dograh AI is 100% open source and we intend to keep it that way.

### Getting Started
- Fork the repository
- Create your feature branch (git checkout -b feature/AmazingFeature)
- Commit your changes (git commit -m 'Add some AmazingFeature')
- Push to the branch (git push origin feature/AmazingFeature)
- Open a Pull Request

## 📄 License
Dograh AI is licensed under the [BSD 2-Clause License](LICENSE)- the same license as projects that were used in building Dograh AI, ensuring compatibility and freedom to use, modify, and distribute.

## 🏢 About
Built with ❤️ by **Dograh** (Zansat Technologies Private Limited)
Founded by YC alumni and exit founders committed to keeping voice AI open and accessible to everyone.

<br><br><br>

  <p align="center">
    <a href="https://github.com/dograh-hq/dograh/stargazers">⭐ Star us on GitHub</a> |
    <a href="https://www.dograh.com">☁️ Try Cloud Version</a> |
    <a href="https://join.slack.com/t/dograh-community/shared_invite/zt-3czr47sw5-MSg1J0kJ7IMPOCHF~03auQ">💬 Join Slack</a>
  </p>

