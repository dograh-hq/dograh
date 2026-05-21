#!/usr/bin/env bash
set -euo pipefail

ruff check api --select I --select F401 --fix
ruff format api

ruff format pipecat

(cd ui && npm run fix-lint)
