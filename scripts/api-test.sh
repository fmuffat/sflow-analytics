#!/usr/bin/env bash
# API tests: unit tests + tests against the ClickHouse service of the dev stack
# (a throw-away database is created and dropped). Requires Docker only.
set -euo pipefail
cd "$(dirname "$0")/.."
[ -f .env ] || { echo ".env missing: run scripts/dev-up.sh first" >&2; exit 1; }
set -a; . ./.env; set +a

docker compose up -d clickhouse >/dev/null
for _ in $(seq 1 60); do
  [ "$(docker inspect -f '{{.State.Health.Status}}' "$(docker compose ps -q clickhouse)")" = "healthy" ] && break
  sleep 2
done

docker build -q --target test -t sflow-analytics/api:test api >/dev/null
docker run --rm --network host \
  -e SFLOW_TEST_CLICKHOUSE_HOST=127.0.0.1 \
  -e SFLOW_TEST_CLICKHOUSE_PORT=8123 \
  -e SFLOW_TEST_CLICKHOUSE_USER="${CLICKHOUSE_USER:-sflow}" \
  -e SFLOW_TEST_CLICKHOUSE_PASSWORD="${CLICKHOUSE_PASSWORD}" \
  -e SFLOW_SCHEMA_DIR=/schema \
  -v "$PWD/collector/internal/storage/clickhouse/migrations:/schema:ro" \
  sflow-analytics/api:test pytest -q "$@"
