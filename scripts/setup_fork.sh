#!/usr/bin/env bash
# Contributor bootstrap. Run this once after cloning your fork.
# Configures git remotes (origin = your fork, upstream = dograh-hq/dograh),
# initializes the pipecat submodule, creates the Python venv, and copies
# the .env templates.

set -e

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
RED='\033[0;31m'
NC='\033[0m'

UPSTREAM_URL="https://github.com/dograh-hq/dograh.git"

BASE_DIR="$(cd "$(dirname "$(dirname "${BASH_SOURCE[0]}")")" && pwd)"
cd "$BASE_DIR"

if ! git rev-parse --git-dir > /dev/null 2>&1; then
    echo -e "${RED}Error: not a git repository. Run this from inside your cloned fork.${NC}"
    exit 1
fi

echo -e "${BLUE}╔══════════════════════════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║              Dograh Contributor Bootstrap                    ║${NC}"
echo -e "${BLUE}╚══════════════════════════════════════════════════════════════╝${NC}"
echo ""

###############################################################################
### 1) Configure git remotes
###############################################################################

echo -e "${BLUE}[1/4] Configuring git remotes${NC}"

current_origin=$(git remote get-url origin 2>/dev/null || echo "")
canonical_https="https://github.com/dograh-hq/dograh.git"
canonical_ssh="git@github.com:dograh-hq/dograh.git"

# If origin is missing or points at the canonical repo (i.e. user cloned the
# canonical repo directly without forking), prompt for the fork URL.
needs_fork_prompt=false
if [[ -z "$current_origin" ]]; then
    needs_fork_prompt=true
elif [[ "$current_origin" == "$canonical_https" || "$current_origin" == "$canonical_ssh" ]]; then
    echo -e "${YELLOW}origin currently points at the canonical repo ($current_origin).${NC}"
    echo -e "${YELLOW}You should push to your own fork, not the canonical repo.${NC}"
    needs_fork_prompt=true
fi

if $needs_fork_prompt; then
    echo -e "${YELLOW}Enter your fork URL (e.g. https://github.com/<YOUR_HANDLE>/dograh.git):${NC}"
    read -r -p "> " FORK_URL
    if [[ -z "$FORK_URL" ]]; then
        echo -e "${RED}Fork URL is required.${NC}"
        exit 1
    fi
    if [[ -n "$current_origin" ]]; then
        git remote remove origin
    fi
    git remote add origin "$FORK_URL"
    echo -e "${GREEN}✓ origin set to $FORK_URL${NC}"
else
    echo -e "${GREEN}✓ origin already set: $current_origin${NC}"
fi

existing_upstream=$(git remote get-url upstream 2>/dev/null || echo "")
if [[ -z "$existing_upstream" ]]; then
    git remote add upstream "$UPSTREAM_URL"
    echo -e "${GREEN}✓ upstream set to $UPSTREAM_URL${NC}"
elif [[ "$existing_upstream" != "$UPSTREAM_URL" && "$existing_upstream" != "$canonical_ssh" ]]; then
    echo -e "${YELLOW}upstream currently points at $existing_upstream (expected $UPSTREAM_URL).${NC}"
    echo -e "${YELLOW}Reset upstream to dograh-hq/dograh? [y/N]:${NC}"
    read -r -p "> " RESET_UPSTREAM
    if [[ "$RESET_UPSTREAM" =~ ^[Yy] ]]; then
        git remote set-url upstream "$UPSTREAM_URL"
        echo -e "${GREEN}✓ upstream reset to $UPSTREAM_URL${NC}"
    else
        echo -e "${YELLOW}Leaving upstream alone.${NC}"
    fi
else
    echo -e "${GREEN}✓ upstream already set${NC}"
fi

echo ""
git remote -v
echo ""

###############################################################################
### 2) Initialize submodules
###############################################################################

echo -e "${BLUE}[2/4] Initializing pipecat submodule${NC}"
git submodule update --init --recursive
echo -e "${GREEN}✓ submodules initialized${NC}"
echo ""

###############################################################################
### 3) Python venv
###############################################################################

echo -e "${BLUE}[3/4] Python virtual environment${NC}"
VENV_PATH="$BASE_DIR/venv"

if [[ -d "$VENV_PATH" && -f "$VENV_PATH/bin/activate" ]]; then
    echo -e "${GREEN}✓ venv already exists at $VENV_PATH${NC}"
else
    PY=""
    for candidate in python3.13 python3 python; do
        if command -v "$candidate" >/dev/null 2>&1; then
            PY="$candidate"
            break
        fi
    done
    if [[ -z "$PY" ]]; then
        echo -e "${RED}Error: no python interpreter found on PATH. Install Python 3.13.${NC}"
        exit 1
    fi
    "$PY" -m venv "$VENV_PATH"
    echo -e "${GREEN}✓ venv created at $VENV_PATH using $PY ($("$PY" --version))${NC}"
fi
echo ""

###############################################################################
### 4) .env files
###############################################################################

echo -e "${BLUE}[4/4] Environment files${NC}"
for pair in "api/.env.example|api/.env" "ui/.env.example|ui/.env"; do
    src="${pair%|*}"
    dst="${pair#*|}"
    if [[ -f "$dst" ]]; then
        echo -e "${GREEN}✓ $dst already exists${NC}"
    elif [[ -f "$src" ]]; then
        cp "$src" "$dst"
        echo -e "${GREEN}✓ created $dst from $src${NC}"
    else
        echo -e "${YELLOW}⚠ $src not found, skipping${NC}"
    fi
done
echo ""

###############################################################################
### Done
###############################################################################

echo -e "${GREEN}╔══════════════════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║                  Bootstrap complete                          ║${NC}"
echo -e "${GREEN}╚══════════════════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "${YELLOW}Next steps:${NC}"
echo "  1. source venv/bin/activate"
echo "  2. bash scripts/setup_requirements.sh"
echo "  3. (cd ui && npm install)"
echo "  4. docker compose -f docker-compose-local.yaml up -d"
echo "  5. bash scripts/start_services_dev.sh"
echo ""
echo -e "${YELLOW}To sync your fork with upstream later:${NC}"
echo "  git fetch upstream"
echo "  git checkout main && git merge upstream/main"
echo "  git push origin main"
echo ""
