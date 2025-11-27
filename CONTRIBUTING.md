# Contributing to Dograh AI

Welcome to Dograh AI! 🎉 Thank you for your interest in contributing to the future of open-source voice AI.

Dograh AI is a comprehensive voice agent platform that helps developers build, test, and deploy conversational AI systems with minimal setup. This guide will help you understand the project structure, set up your development environment, and start contributing effectively.

👉 Join our community → [Dograh Community Slack](https://join.slack.com/t/dograh-community/shared_invite/zt-3czr47sw5-MSg1J0kJ7IMPOCHF~03auQ)

## 🏗️ Project Overview

### What is Dograh AI?
Dograh AI is a full-stack platform for building voice agents with a drag-and-drop workflow builder. It combines multiple technologies to provide a seamless experience from development to production deployment.

### Monorepo Architecture
This project uses a monorepo structure with the following main components:

```
dograh/
├── api/                    # FastAPI backend service
│   ├── routes/            # API endpoints
│   ├── services/          # Business logic & integrations
│   ├── db/                # Database models & migrations
│   └── requirements.txt   # Python dependencies
├── ui/                    # Next.js frontend application
│   ├── src/               # React components & pages
│   ├── package.json       # Node.js dependencies
│   └── tailwind.config.js # Styling configuration
├── pipecat/               # Voice processing engine (git submodule)
├── scripts/               # Development & deployment scripts
├── docs/                  # Documentation
├── docker-compose.yaml    # Multi-service orchestration
└── Dockerfile             # Container configurations
```

### Technology Stack

**Backend (Python)**
- **Framework**: FastAPI
- **Database**: PostgreSQL with Alembic migrations
- **Cache**: Redis for session management and caching
- **Storage**: S3-compatible storage for audio files and assets (MinIO in OSS mode, AWS S3 in SaaS mode)
- **Voice Engine**: Pipecat integration for real-time voice processing
- **Background Tasks**: ARQ for asynchronous job processing

**Frontend (TypeScript)**
- **Framework**: Next.js 15 with React 19
- **UI Components**: Shadcn/ui with Radix UI primitives
- **Styling**: Tailwind CSS with animations
- **State Management**: Zustand for client-side state

**Infrastructure**
- **Containerization**: Docker with multi-service composition
- **Tunneling**: Cloudflared tunnel for telephony webhook access during development
- **Monitoring**: Sentry for error tracking, Langfuse for LLM observability

### Deployment Modes

Dograh AI supports two deployment modes:

**OSS (Open Source) Mode**
- Self-hosted deployment with local services
- Uses local MinIO for file storage
- Local authentication system
- Full control over data and infrastructure
- Ideal for development and private deployments

**SaaS (Software as a Service) Mode**
- Cloud-hosted with managed services  
- AWS S3 for file storage
- Integrated authentication providers
- Managed infrastructure and scaling
- Production-ready for commercial use



## 🙌 How You Can Contribute

- 🐛 **Report bugs** via GitHub Issues  
- 💡 **Suggest features**  
- 🔧 **Submit pull requests**  
- 📖 **Improve documentation**  
- 💬 **Join the Slack community**



## 🧰 Issue Types

On our [GitHub Issues page](../../issues), you'll find these templates:  

- 🐛 **Bug Report** - Create a report to help us improve
- 📚 **Documentation Change Request** - Suggest improvements, corrections, or additions to the documentation
- 💡 **Feature Request** - Suggest any ideas you have using our discussion forums
- 🔒 **Report a Security Vulnerability** - Privately report security vulnerabilities to maintainers
- 📝 **Blank issue** - Create a new issue from scratch  

👉 A great place to start is with issues tagged **`good first issue`**.  



## 🛠 Development Guidelines

- Keep PRs focused and scoped
- Follow Python best practices (PEP8)
- **Study existing code structure** before implementing new features - understand patterns, naming conventions, and architectural decisions
- **Follow established patterns** for similar functionality already in the codebase
- Please **link the issue** in your PR description using: `fixes #<issue_number>` - this auto-closes the issue when merged  



## 🚀 Development Setup

Choose your preferred development approach:

### Option 1: Docker Development (Recommended for Quick Start)

The fastest way to get started is using Docker, which provides all services pre-configured:

```bash
# Clone the repository
git clone https://github.com/dograh-hq/dograh.git
cd dograh

# Start all services with Docker
docker compose up --pull always
```

**Services will be available at:**
- **UI Dashboard**: http://localhost:3010
- **API Backend**: http://localhost:8000

### Option 2: Local Development Setup

For active development with hot reloading and debugging capabilities:

#### Prerequisites
- **Python 3.9+** with conda or virtualenv
- **Node.js 18+** and npm
- **PostgreSQL 17** (or use Docker for databases only)
- **Redis 7** (or use Docker for databases only)
- **Git** with submodule support

#### Backend Development

1. **Environment Setup**
   ```bash
   # Create and activate virtual environment
   conda create -n dograh python=3.9
   conda activate dograh
   # OR using venv: python -m venv venv && source venv/bin/activate
   ```

2. **Database Services** (if not using local installations)
   ```bash
   # Start only database services
   docker compose up postgres redis minio -d
   ```

3. **Project Dependencies**
   ```bash
   # Initialize Pipecat submodule and install dependencies
   ./scripts/setup_pipecat.sh
   ```
   
   This script handles:
   - Git submodule initialization for Pipecat voice engine
   - Pipecat installation with all required extras (Cartesia, Deepgram, OpenAI, etc.)
   - Backend API dependencies installation

4. **Environment Configuration**
   ```bash
   # Copy and configure environment variables
   cp api/.env.example api/.env
   # Edit api/.env with your local database URLs and API keys
   ```

5. **Launch Backend Services**
   ```bash
   # Start all backend services with database migrations
   ./scripts/start_services.sh
   ```
   
   This starts:
   - FastAPI application (port 8000)
   - ARQ background workers
   - Campaign orchestrator
   - ARI telephony manager
   - Automatic database migrations

#### Frontend Development

1. **Install Dependencies**
   ```bash
   cd ui
   npm install
   ```

2. **Code Quality Setup**
   ```bash
   # Fix any linting issues
   npm run fix-lint
   ```

3. **Start Development Server**
   ```bash
   # Start with hot reloading
   npm run dev
   ```
   
   Frontend will be available at `http://localhost:3000`

### Quick Command Reference

```bash
# Full local development setup
conda activate dograh                    # Activate Python environment
./scripts/setup_pipecat.sh              # Setup voice engine + dependencies
./scripts/start_services.sh             # Launch backend services
cd ui && npm install && npm run dev     # Start frontend (new terminal)

# Docker-only development  
docker compose up --pull always         # Everything in containers
```

#### Composite Scripts (Recommended)
These scripts handle multiple tasks automatically:
```bash
./scripts/pre_commit.sh                 # Format Python + fix frontend linting + restage files
./scripts/start_services.sh             # Run migrations + start all backend services
./scripts/setup_pipecat.sh              # Initialize submodules + install dependencies
```

#### Individual Commands
For specific tasks when you need more control:
```bash
./scripts/migrate.sh                    # Run database migrations only
./scripts/format.sh                     # Format Python code only
./pipecat/scripts/fix-ruff.sh           # Format Pipecat source code only
cd ui && npm run fix-lint               # Fix frontend linting issues
cd ui && npm run lint                   # Check frontend code quality (no fixes)
```

> 💡 **Tip**: Use composite scripts for most development workflows. Individual commands are useful for debugging or working on specific components.

### Environment Configuration

Key environment variables (see `api/.env.example` for full list):

- **Database**: `DATABASE_URL` - PostgreSQL connection string
- **Cache**: `REDIS_URL` - Redis connection for sessions/tasks  
- **Storage**: MinIO configuration for audio file storage
- **AI Services**: API keys for OpenAI, Deepgram, ElevenLabs, etc.
- **Telephony**: Twilio or other provider credentials (configured via UI)



## 🔄 Contributing Workflow

### Getting Started with Your First Contribution

1. **Fork & Clone**
   ```bash
   # Fork the repository on GitHub, then clone your fork
   git clone https://github.com/YOUR_USERNAME/dograh.git
   cd dograh
   
   # Add upstream remote
   git remote add upstream https://github.com/dograh-hq/dograh.git
   ```

2. **Create Feature Branch**
   ```bash
   # Create a descriptive branch name
   git checkout -b feature/add-voice-detection
   git checkout -b fix/authentication-bug
   git checkout -b docs/improve-setup-guide
   ```

3. **Set Up Development Environment**
   - Follow the [Development Setup](#-development-setup) guide above
   - Test that everything works before making changes

4. **Make Your Changes**
   - Write clean, documented code following existing patterns
   - Add tests for new functionality
   - Update documentation as needed

5. **Test Your Changes**
   ```bash
   # Backend testing
   cd api && python -m pytest
   
   # Frontend linting and building
   cd ui && npm run lint && npm run build
   ```

6. **Commit & Push**
   ```bash
   git add .
   git commit -m "feat: add voice activity detection to improve turn-taking"
   git push origin feature/add-voice-detection
   ```

7. **Open Pull Request**
   - Use a descriptive title and link to any related issues
   - Include: fixes #<issue_number> to auto-close issues
   - Provide context about what your changes do and why

### Code Standards

**Python (Backend)**
- Follow PEP 8 style guidelines
- Use type hints for function parameters and return values
- Write docstrings for public functions and classes
- Run `./scripts/pre_commit.sh` before committing (handles both Python formatting and frontend linting)

**TypeScript (Frontend)**
- Follow existing ESLint configuration
- Use TypeScript strictly (no `any` types)
- Follow existing component patterns and naming conventions
- Run `npm run fix-lint` before committing

**General Guidelines**
- Keep PRs focused and scoped to one feature/fix
- Write clear commit messages following [Conventional Commits](https://www.conventionalcommits.org/) (e.g., `feat:`, `fix:`, `docs:`)
- Update documentation for user-facing changes
- Add tests for new functionality

### Getting Help

**Before You Start**
- Check existing [GitHub Issues](../../issues) for similar work
- Join our [Slack community](https://join.slack.com/t/dograh-community/shared_invite/zt-3czr47sw5-MSg1J0kJ7IMPOCHF~03auQ) to discuss your plans
- Look for issues tagged `good first issue` for beginner-friendly tasks

**During Development**
- Ask questions in our Slack community
- Reference related issues and PRs in your discussions
- Share early drafts for feedback on complex features

## 💬 Community & Support

Our Slack community is the heart of Dograh AI development:

- **Get Help**: Setup assistance and debugging support
- **Collaborate**: Discuss features and architectural decisions  
- **Connect**: Meet other contributors and maintainers
- **Stay Updated**: Learn about contribution opportunities and releases

👉 **Join us**: [Dograh Community Slack](https://join.slack.com/t/dograh-community/shared_invite/zt-3czr47sw5-MSg1J0kJ7IMPOCHF~03auQ)

### Other Ways to Contribute

Beyond code, you can help by:
- **Reporting bugs** with detailed reproduction steps
- **Suggesting features** that solve real problems
- **Improving documentation** and examples
- **Testing releases** and providing feedback
- **Helping other community members** in Slack

Thank you for helping us keep voice AI open and accessible! 🎉
