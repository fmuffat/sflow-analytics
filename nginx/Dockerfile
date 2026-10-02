FROM nginx:stable-alpine
RUN apk add --no-cache openssl
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY 10-certs.sh 20-cert-watch.sh /docker-entrypoint.d/
RUN chmod +x /docker-entrypoint.d/10-certs.sh /docker-entrypoint.d/20-cert-watch.sh
EXPOSE 80 443
HEALTHCHECK --interval=30s --timeout=3s CMD wget --no-check-certificate -qO- https://127.0.0.1/api/v1/health >/dev/null || exit 1
