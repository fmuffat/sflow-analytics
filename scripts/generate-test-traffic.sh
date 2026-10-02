#!/usr/bin/env bash
# Send synthetic sFlow to a collector using the sflow-gen tool inside the
# collector image (no Go toolchain needed on the host).
#
#   scripts/generate-test-traffic.sh                    # 3 exporters -> 127.0.0.1:6343
#   scripts/generate-test-traffic.sh -exporters 50 -rate 20
#   scripts/generate-test-traffic.sh -replay /fixtures/two_exporters.pcap -loop -speed 1
set -euo pipefail
cd "$(dirname "$0")/.."
TARGET="${TARGET:-127.0.0.1:6343}"
IMAGE="sflow-analytics/collector:${APP_VERSION:-dev}"
exec docker run --rm -it --network host --entrypoint /usr/local/bin/sflow-gen "$IMAGE" -target "$TARGET" "$@"
