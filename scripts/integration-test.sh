#!/usr/bin/env bash
# Integration tests against the ClickHouse service of the dev stack.
# Requires Docker only (tests run in a golang container on the host network).
set -euo pipefail
cd "$(dirname "$0")/.."
[ -f .env ] || { echo ".env missing: run scripts/dev-up.sh first" >&2; exit 1; }
set -a; . ./.env; set +a

docker compose up -d clickhouse
echo -n "waiting for clickhouse"
for _ in $(seq 1 60); do
  if [ "$(docker inspect -f '{{.State.Health.Status}}' "$(docker compose ps -q clickhouse)")" = "healthy" ]; then echo " ok"; break; fi
  echo -n "."; sleep 2
done

docker run --rm --network host \
  -e SFLOW_IT_CLICKHOUSE_ADDR=127.0.0.1:9000 \
  -e SFLOW_IT_CLICKHOUSE_USER="${CLICKHOUSE_USER:-sflow}" \
  -e SFLOW_IT_CLICKHOUSE_PASSWORD="${CLICKHOUSE_PASSWORD}" \
  -v "$PWD/collector:/src" -v sflow-go-cache:/root/go -w /src \
  golang:1.25 go test -tags integration -count=1 -v ./tests/integration "$@"
