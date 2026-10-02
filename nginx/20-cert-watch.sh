#!/bin/sh
# Reloads nginx (no downtime) when the certificate files change, e.g. after an import from
# Administration → HTTPS certificate. An invalid certificate is not loaded.
(
  last=""
  while sleep 10; do
    cur="$(cat /etc/nginx/certs/server.crt /etc/nginx/certs/server.key 2>/dev/null | md5sum)"
    if [ -n "$last" ] && [ "$cur" != "$last" ]; then
      if nginx -t -q 2>/dev/null; then
        nginx -s reload && echo "cert-watch: certificate changed, nginx reloaded"
      else
        echo "cert-watch: new certificate rejected by nginx -t, previous one kept in memory"
      fi
    fi
    last="$cur"
  done
) &
