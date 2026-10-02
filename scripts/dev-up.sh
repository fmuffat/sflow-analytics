#!/usr/bin/env bash
# Start the development stack. Pass --test to also start the synthetic generator.
set -euo pipefail
cd "$(dirname "$0")/.."
[ -f .env ] || cp .env.example .env

# Generate a local ClickHouse password on first run (never committed).
if grep -q '^CLICKHOUSE_PASSWORD=change-me$' .env || ! grep -q '^CLICKHOUSE_PASSWORD=.' .env; then
  pw=$(head -c 24 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | head -c 24)
  if grep -q '^CLICKHOUSE_PASSWORD=' .env; then
    sed -i "s/^CLICKHOUSE_PASSWORD=.*/CLICKHOUSE_PASSWORD=${pw}/" .env
  else
    echo "CLICKHOUSE_PASSWORD=${pw}" >> .env
  fi
  echo "Generated CLICKHOUSE_PASSWORD in .env"
fi

if [ "${1:-}" = "--test" ]; then
  docker compose --profile test up -d --build
else
  docker compose up -d --build
fi
docker compose ps
