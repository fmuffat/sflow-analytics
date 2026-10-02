#!/usr/bin/env bash
# Backup of an installation: ClickHouse data, configuration volume (users, encrypted
# secrets and their key), TLS certificate and .env, into one archive.
#
#   scripts/backup.sh [output-dir]          # default: ./backups
#
# The archive contains secrets (.env, secret.key, password hashes): keep it private.
# Restore: see docs/deployment.md "Backup and restore".
set -euo pipefail
cd "$(dirname "$0")/.."
[ -f .env ] || { echo ".env missing" >&2; exit 1; }
set -a; . ./.env; set +a

OUT_DIR="${1:-backups}"
STAMP="$(date -u +%Y%m%d-%H%M%S)"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
mkdir -p "$OUT_DIR" "$WORK/clickhouse"
PROJECT="$(docker compose config --format json | python3 -c 'import sys,json; print(json.load(sys.stdin)["name"])')"
DB="${CLICKHOUSE_DATABASE:-sflow}"
ch() { docker compose exec -T clickhouse clickhouse-client --user "${CLICKHOUSE_USER:-sflow}" --password "$CLICKHOUSE_PASSWORD" "$@"; }

echo "ClickHouse database $DB:"
for t in $(ch --query "SELECT name FROM system.tables WHERE database = '$DB' AND engine NOT LIKE '%View' ORDER BY name"); do
  ch --query "SHOW CREATE TABLE $DB.$t" --format TSVRaw > "$WORK/clickhouse/$t.sql"
  ch --query "SELECT * FROM $DB.$t FORMAT Native" | gzip -c > "$WORK/clickhouse/$t.native.gz"
  echo "  $t: $(ch --query "SELECT count() FROM $DB.$t") rows"
done

echo "Volumes:"
for v in app-config nginx-certs; do
  if docker volume inspect "${PROJECT}_$v" >/dev/null 2>&1; then
    docker run --rm -v "${PROJECT}_$v:/v:ro" -v "$WORK:/b" alpine tar czf "/b/$v.tar.gz" -C /v .
    echo "  $v"
  fi
done
cp .env "$WORK/env"
cat > "$WORK/MANIFEST.txt" <<EOF
sFlow Analytics backup
created:  $STAMP (UTC)
version:  $(git describe --tags --always 2>/dev/null || echo unknown)
database: $DB
contents: clickhouse/<table>.sql + .native.gz, app-config.tar.gz, nginx-certs.tar.gz, env
EOF

ARCHIVE="$OUT_DIR/sflow-backup-$STAMP.tar.gz"
tar czf "$ARCHIVE" -C "$WORK" .
chmod 600 "$ARCHIVE"
echo "Backup: $ARCHIVE ($(du -h "$ARCHIVE" | cut -f1))"
