# Public entry point for the whole stack: TLS termination + reverse proxy.
#
# Pinned minor: the TLS template uses `http2 on;`, which only exists from
# nginx 1.25.1 — an older base would fail to start with a cryptic error.
FROM nginx:1.27-alpine

# `envsubst` (gettext) ships in the official nginx images and is what the
# entrypoint uses to inject the domain into a template.
COPY snippets/  /etc/nginx/snippets/
COPY templates/ /templates/
# http-level config (the WebSocket upgrade map) — conf.d is included in http{}.
# The entrypoint only ever writes conf.d/default.conf, so this file survives.
COPY http.d/    /etc/nginx/conf.d/
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 80 443
ENTRYPOINT ["/entrypoint.sh"]
