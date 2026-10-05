#!/usr/bin/env bash
# Builds the offline installation package (images included) for an internal beta:
#
#   scripts/build-package.sh [version]      # default: latest version in CHANGELOG.md
#   -> dist/sflow-analytics-<version>.tar.gz  (+ .sha256)
#
# Requires Docker. The package installs with `sudo ./install.sh` (see packaging/QUICKSTART.md).
set -euo pipefail
cd "$(dirname "$0")/.."
VERSION="${1:-$(grep -m1 -oE '^## \[[0-9]+\.[0-9]+\.[0-9]+\]' CHANGELOG.md | tr -d '#[] ')}"
[[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+([-.][A-Za-z0-9.]+)?$ ]] || { echo "bad version: $VERSION" >&2; exit 1; }
NAME="sflow-analytics-$VERSION"
OUT="dist/$NAME"
CH_IMAGE="clickhouse/clickhouse-server:25.8"

echo "==> Building images $VERSION"
CLICKHOUSE_PASSWORD=unused APP_VERSION="$VERSION" docker compose build collector api frontend nginx
docker image inspect "$CH_IMAGE" >/dev/null 2>&1 || docker pull "$CH_IMAGE"

echo "==> Assembling $OUT"
rm -rf "$OUT" && mkdir -p "$OUT/clickhouse"
cp packaging/install.sh packaging/uninstall.sh packaging/updater.sh packaging/compose.yml packaging/QUICKSTART.md LICENSE "$OUT/"
cp db/clickhouse/config.d/sflow.xml "$OUT/clickhouse/sflow.xml"
sed -e 's|cd "$(dirname "$0")/.."|cd "$(dirname "$0")"|' \
    -e 's|$(git describe --tags --always 2>/dev/null \|\| echo unknown)|$(cat VERSION 2>/dev/null \|\| echo unknown)|' \
    scripts/backup.sh > "$OUT/backup.sh"
echo "$VERSION" > "$OUT/VERSION"
chmod 755 "$OUT"/*.sh

echo "==> Saving images"
docker save "$CH_IMAGE" \
  "sflow-analytics/collector:$VERSION" "sflow-analytics/api:$VERSION" \
  "sflow-analytics/frontend:$VERSION" "sflow-analytics/nginx:$VERSION" | gzip -1 > "$OUT/images.tar.gz"

tar czf "dist/$NAME.tar.gz" -C dist "$NAME"
(cd dist && sha256sum "$NAME.tar.gz" > "$NAME.tar.gz.sha256")
rm -rf "$OUT"
echo "==> dist/$NAME.tar.gz ($(du -h "dist/$NAME.tar.gz" | cut -f1))"
