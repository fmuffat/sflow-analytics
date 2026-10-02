#!/usr/bin/env bash
# Screenshots of every UI page (headless Chromium in Docker) into ./screenshots.
# Signs in with a dedicated "ui-test" account (never the real admin), created once
# with a random password kept outside the repository ($UI_TEST_CREDENTIALS).
# Make it read-only (python -m app.users set-role ui-test viewer): admin pages are then skipped.
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p screenshots

CRED="${UI_TEST_CREDENTIALS:-$HOME/.config/sflow-ui-test}"
if [ ! -s "$CRED" ]; then
  mkdir -p "$(dirname "$CRED")"
  PW="$(head -c 32 /dev/urandom | base64 | tr -dc A-Za-z0-9 | head -c 20)"
  for cmd in create reset-password; do
    if printf '%s\n' "$PW" | docker compose exec -T api sh -c \
        "cat > /tmp/pw && python -m app.users $cmd ui-test --password-file /tmp/pw --no-change >/dev/null; rc=\$?; rm -f /tmp/pw; exit \$rc"; then
      break
    fi
  done
  printf 'ui-test\n%s\n' "$PW" > "$CRED"
  chmod 600 "$CRED"
fi
UI_USER="$(sed -n 1p "$CRED")"
UI_PASSWORD="$(sed -n 2p "$CRED")"

JAR="$(mktemp)"
trap 'rm -f "$JAR"' EXIT
curl -sk -c "$JAR" -H 'Content-Type: application/json' \
  -d "{\"username\":\"$UI_USER\",\"password\":\"$UI_PASSWORD\"}" https://127.0.0.1/api/v1/auth/login >/dev/null
EXPORTER_ID="${EXPORTER_ID:-$(curl -sk -b "$JAR" https://127.0.0.1/api/v1/exporters | python3 -c 'import sys,json; i=json.load(sys.stdin)["items"]; print(i[0]["id"] if i else "")')}"

docker run --rm --network host --init -e EXPORTER_ID="$EXPORTER_ID" -e SCHEME="${SCHEME:-light}" \
  -e UI_USER="$UI_USER" -e UI_PASSWORD="$UI_PASSWORD" \
  -v "$PWD/scripts/ui-screenshots.mjs:/work/ui-screenshots.mjs:ro" -v "$PWD/screenshots:/out" \
  -v sflow-playwright-cache:/root/.cache -w /work node:22-bookworm \
  sh -c "npm i -s --no-audit --no-fund playwright@1 >/dev/null 2>&1 && npx -y playwright install --with-deps chromium >/dev/null 2>&1 && node ui-screenshots.mjs ${BASE_URL:-https://127.0.0.1} /out"
