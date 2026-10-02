#!/usr/bin/env bash
# sFlow Analytics — uninstall.
#   sudo ./uninstall.sh            stop and remove the containers (data kept: reinstall finds it again)
#   sudo ./uninstall.sh --purge    also delete ALL data (flows, history, users, settings) and images
set -euo pipefail
[ "$(id -u)" -eq 0 ] || exec sudo -E "$0" "$@"
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"
if [ "${1:-}" = "--purge" ]; then
  read -r -p "Delete ALL sFlow Analytics data in $DIR? Type 'delete' to confirm: " answer
  [ "$answer" = "delete" ] || { echo "cancelled"; exit 1; }
  docker compose --profile demo down -v --rmi all
  rm -f /etc/sysctl.d/90-sflow-analytics.conf
  cd / && rm -rf "$DIR"
  echo "sFlow Analytics removed with its data."
else
  docker compose --profile demo down
  echo "Containers removed; data kept in Docker volumes. Reinstall with install.sh, or delete everything with: sudo $DIR/uninstall.sh --purge"
fi
