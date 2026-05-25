#!/usr/bin/env bash
set -euo pipefail

# Intentionally no `http://localhost:PORT` URLs below — VS Code's terminal
# URL detector adds any printed URL to its auto-forwarded-ports list and
# then polls it, which produces ECONNREFUSED log spam every ~20s for ports
# that aren't bound yet. The Ports panel auto-detects bound ports anyway.
cat <<'EOF'
Dograh devcontainer ready.

Start the backend:
  bash scripts/start_services_dev.sh

Start the UI in another terminal:
  cd ui && npm run dev -- --hostname 0.0.0.0

URLs and other workflow notes: docs/contribution/setup.mdx
EOF
