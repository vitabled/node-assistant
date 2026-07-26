#!/bin/sh
# Render the nginx config for the CURRENT state and keep it fresh.
#
# ⚠️ The chicken-and-egg that breaks most nginx+certbot setups: a `listen 443 ssl`
# block whose certificate file does not exist yet makes nginx REFUSE TO START —
# so the very first `certbot --webroot` run (which needs a live nginx on :80)
# can never happen. We dodge it by choosing the template at runtime:
#   no cert  → HTTP-only template (serves the app + the ACME challenge path)
#   cert     → TLS template (HTTP redirects, except the ACME path)
# A background watcher re-renders every 6h, so the HTTP→TLS switch after the
# first issuance and every later renewal are picked up without manual steps.
set -eu

DOMAIN="${PROXY_DOMAIN:-}"
# `_` is nginx's catch-all server_name — used when no domain is configured yet.
[ -n "$DOMAIN" ] || DOMAIN="_"

CONF=/etc/nginx/conf.d/default.conf
CERT_DIR="/etc/letsencrypt/live/$DOMAIN"
WATCH_INTERVAL="${PROXY_RELOAD_INTERVAL:-21600}"   # seconds (busybox sleep: no suffixes)

# The HTTP→HTTPS redirect must carry the PUBLIC https port when it is not 443.
# `$host` stays a literal here — envsubst only replaces the placeholders it is
# given, and does not rescan the values it substitutes in.
HTTPS_PORT="${PROXY_HTTPS_PORT:-443}"
if [ "$HTTPS_PORT" = "443" ]; then
    # shellcheck disable=SC2016  # literal on purpose: nginx expands $host, not us
    REDIRECT_HOST='$host'
else
    REDIRECT_HOST="\$host:$HTTPS_PORT"
fi

render() {
    if [ "$DOMAIN" != "_" ] && [ -s "$CERT_DIR/fullchain.pem" ] && [ -s "$CERT_DIR/privkey.pem" ]; then
        _tpl=/templates/app-tls.conf.template
        _mode=https
    else
        _tpl=/templates/app-http.conf.template
        _mode=http
    fi
    # Restrict envsubst to PROXY_DOMAIN ONLY. Without the allow-list it would
    # also eat nginx's own runtime variables ($host, $request_uri, $http_upgrade,
    # …) and silently produce a broken config.
    # shellcheck disable=SC2016  # the single quotes are REQUIRED: envsubst takes
    # the literal strings as its allow-list, not their values. Without the
    # allow-list it would also eat nginx's own $host/$request_uri/$http_upgrade.
    PROXY_DOMAIN="$DOMAIN" REDIRECT_HOST="$REDIRECT_HOST" \
        envsubst '${PROXY_DOMAIN} ${REDIRECT_HOST}' < "$_tpl" > "$1"

    # ⚠️ On a host booted with ipv6.disable=1 the container has no AF_INET6, and
    # `listen [::]:80` makes nginx abort at startup with
    # `socket() [::]:80 failed (97: Address family not supported by protocol)`.
    # Drop the v6 listeners in that case instead of shipping two more templates.
    if [ ! -f /proc/net/if_inet6 ]; then
        sed -i '/listen .*\[::\]/d' "$1"
        _mode="$_mode,ipv4-only"
    fi
    echo "[proxy] rendered mode=$_mode domain=$DOMAIN"
}

mkdir -p /var/www/certbot /etc/letsencrypt
render "$CONF"
nginx -t

# Watcher: re-render, and reload only when something actually changed or a
# renewal may have landed. A failed `nginx -t` keeps the previous config loaded.
(
    while :; do
        sleep "$WATCH_INTERVAL"
        render "$CONF.new" || continue
        if ! cmp -s "$CONF" "$CONF.new"; then
            cp "$CONF" "$CONF.bak"
            mv "$CONF.new" "$CONF"
            if nginx -t 2>/dev/null; then
                echo "[proxy] config changed — reloading"
                nginx -s reload
            else
                echo "[proxy] rendered config invalid — rolling back" >&2
                mv "$CONF.bak" "$CONF"
            fi
        else
            rm -f "$CONF.new"
            # Same config, but certbot may have rewritten the cert files.
            nginx -s reload 2>/dev/null || true
        fi
    done
) &

exec nginx -g 'daemon off;'
