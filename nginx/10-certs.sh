#!/bin/sh
# Generates a self-signed certificate on first start if none is provided.
# The certificate can then be replaced from Administration → HTTPS certificate (the API,
# uid 10001, writes in this directory; 20-cert-watch.sh reloads nginx on change).
set -eu
DIR=/etc/nginx/certs
if [ ! -s "$DIR/server.crt" ] || [ ! -s "$DIR/server.key" ]; then
  mkdir -p "$DIR"
  CN="${TLS_COMMON_NAME:-sflow-analytics}"
  SAN="DNS:${CN},DNS:localhost${TLS_EXTRA_SAN:+,$TLS_EXTRA_SAN}"
  openssl req -x509 -newkey rsa:2048 -nodes -days 825 \
    -keyout "$DIR/server.key" -out "$DIR/server.crt" \
    -subj "/CN=${CN}" -addext "subjectAltName=${SAN}" >/dev/null 2>&1
  echo "10-certs.sh: generated self-signed certificate for ${SAN}"
fi
chown -R 10001:10001 "$DIR"
chmod 750 "$DIR"
chmod 600 "$DIR/server.key"
