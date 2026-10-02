#!/usr/bin/env bash
# Stop the development stack (volumes are kept).
set -euo pipefail
cd "$(dirname "$0")/.."
docker compose --profile test down
