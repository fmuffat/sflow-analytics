#!/usr/bin/env bash
# sFlow Analytics — host-side updater, started by systemd (sflow-update-<project>.path) when the
# web interface asks for an update by writing a version number in updates/request.
#
# The API container only provides a version number. This script downloads the package itself
# from the official releases, verifies its SHA-256, refuses downgrades, backs up the data, then
# runs the install.sh of the new version (data and settings are kept). Progress for the web
# interface: updates/status.json; full log: updates/update.log.
set -uo pipefail

DIR="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
# Run from a copy: the upgrade replaces this file.
if [ "${SFLOW_UPDATER_COPY:-}" != 1 ]; then
  tmp="$(mktemp /tmp/sflow-updater.XXXXXX)"; cp "$0" "$tmp"; chmod 700 "$tmp"
  SFLOW_UPDATER_COPY=1 SFLOW_UPDATER_DIR="$DIR" exec "$tmp" "$@"
fi
DIR="${SFLOW_UPDATER_DIR:-$DIR}"
U="$DIR/updates"; REQ="$U/request"; ST="$U/status.json"; LOG="$U/update.log"; WORK="$U/work"
trap 'rm -f "$0"' EXIT

status() {  # phase, message, [version]
  python3 - "$1" "$2" "${3:-}" "$ST" <<'PY'
import json, sys, datetime, os
phase, msg, version, path = sys.argv[1:5]
try:
    tail = open(os.path.join(os.path.dirname(path), "update.log"), errors="replace").read().splitlines()[-25:]
except OSError:
    tail = []
st = {"phase": phase, "message": msg, "target_version": version or None,
      "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"), "log_tail": tail}
tmp = path + ".tmp"
with open(tmp, "w") as f:
    json.dump(st, f)
os.chmod(tmp, 0o644)
os.replace(tmp, path)
PY
}
log() { echo "$(date -u +%FT%TZ) $*" >>"$LOG"; }
fail() { log "FAILED: $1"; status failed "$1" "${V:-}"; rm -rf "$WORK"; exit 1; }

[ -f "$REQ" ] || exit 0
V="$(head -c 32 "$REQ" | tr -d '[:space:]')"
rm -f "$REQ"
: >"$LOG"; chmod 644 "$LOG"
log "update requested to $V"
[[ "$V" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || fail "invalid version requested: $V"
CUR="$(cat "$DIR/VERSION" 2>/dev/null || echo 0.0.0)"
if [ "$(printf '%s\n%s\n' "$CUR" "$V" | sort -V | tail -1)" != "$V" ] || [ "$CUR" = "$V" ]; then
  fail "version $V is not newer than the installed $CUR"
fi

# Download source: official GitHub releases (UPDATES_DOWNLOAD_URL in .env only for tests).
BASE="https://github.com/fmuffat/sflow-analytics/releases/download"
url_line="$(grep -E '^UPDATES_DOWNLOAD_URL=' "$DIR/.env" 2>/dev/null | tail -1 | cut -d= -f2-)"
[ -n "$url_line" ] && BASE="$url_line"
NAME="sflow-analytics-$V"

status downloading "Downloading $NAME" "$V"
rm -rf "$WORK"; mkdir -p "$WORK"
curl -fsSL --retry 3 -o "$WORK/$NAME.tar.gz" "$BASE/v$V/$NAME.tar.gz" >>"$LOG" 2>&1 || fail "download failed ($BASE/v$V/$NAME.tar.gz)"
curl -fsSL --retry 3 -o "$WORK/$NAME.tar.gz.sha256" "$BASE/v$V/$NAME.tar.gz.sha256" >>"$LOG" 2>&1 || fail "checksum download failed"
(cd "$WORK" && sha256sum -c "$NAME.tar.gz.sha256" >>"$LOG" 2>&1) || fail "checksum mismatch: package rejected"
log "checksum OK"

status backup "Backing up data and settings" "$V"
if [ -x "$DIR/backup.sh" ]; then
  (cd "$DIR" && ./backup.sh "$DIR/backups") >>"$LOG" 2>&1 || fail "backup failed: update cancelled, nothing changed"
fi

status installing "Installing $V (the web interface restarts; about 2 minutes)" "$V"
tar xzf "$WORK/$NAME.tar.gz" -C "$WORK" >>"$LOG" 2>&1 || fail "cannot extract the package"
[ "$(cat "$WORK/$NAME/VERSION" 2>/dev/null)" = "$V" ] || fail "package content does not match version $V"
if (cd "$WORK/$NAME" && SFLOW_DIR="$DIR" SFLOW_WAIT=900 ./install.sh) >>"$LOG" 2>&1; then
  log "installed $V"
  rm -rf "$WORK"
  status done "Updated to $V" "$V"
else
  fail "installation of $V failed (see update.log; the backup is in $DIR/backups)"
fi
