#!/usr/bin/env bash
# node-assistant — one-shot installer for a fresh server.
#
#   sudo ./install.sh --domain panel.example.com --email you@example.com
#
# What it does: installs Docker if missing, clones/updates the repo, generates the
# secrets in .env, brings the stack up behind the nginx reverse proxy, obtains a
# Let's Encrypt certificate over http-01 and leaves renewal running in the
# `certbot` container. Safe to re-run — existing secrets and certificates are
# reused, never regenerated.
#
# Without --domain it still installs everything and serves the panel over plain
# HTTP on port 80 (add a domain later by re-running with --domain).
set -Eeuo pipefail

REPO_URL="${REPO_URL:-https://github.com/vitabled/node-assistant.git}"
BRANCH="${BRANCH:-main}"
TARGET_DIR="${TARGET_DIR:-/opt/node-assistant}"

DOMAIN=""
EMAIL=""
ASSUME_YES=0
USE_TLS=1
STAGING=0
FORCE_CERT=0

# ── output helpers ─────────────────────────────────────────────
if [ -t 1 ]; then
    C_RST=$'\033[0m'; C_B=$'\033[1m'; C_R=$'\033[31m'; C_G=$'\033[32m'; C_Y=$'\033[33m'; C_C=$'\033[36m'
else
    C_RST=""; C_B=""; C_R=""; C_G=""; C_Y=""; C_C=""
fi
log()  { printf '%s==>%s %s\n' "$C_C" "$C_RST" "$*"; }
ok()   { printf '%s  ok%s %s\n' "$C_G" "$C_RST" "$*"; }
warn() { printf '%s warn%s %s\n' "$C_Y" "$C_RST" "$*" >&2; }
die()  { printf '%serror%s %s\n' "$C_R" "$C_RST" "$*" >&2; exit 1; }

# Prompts must read the TERMINAL, not stdin: with `curl … | bash` stdin is the
# script itself, and a plain `read` would swallow the rest of it.
ask() {
    local prompt="$1" default="${2:-}" reply=""
    if [ ! -r /dev/tty ]; then
        printf '%s' "$default"
        return 0
    fi
    read -r -p "$prompt" reply < /dev/tty || reply=""
    printf '%s' "${reply:-$default}"
}
confirm() {
    [ "$ASSUME_YES" = 1 ] && return 0
    local a
    a="$(ask "$1 [y/N]: " "n")"
    case "$a" in [yY]*) return 0 ;; *) return 1 ;; esac
}

usage() {
    cat <<EOF
${C_B}node-assistant installer${C_RST}

Usage: sudo ./install.sh [options]

  --domain <fqdn>     Domain the panel is served on (enables HTTPS)
  --email <mail>      Contact e-mail for Let's Encrypt (expiry notices)
  --no-tls            Install without a domain — plain HTTP on port 80
  --staging           Use the Let's Encrypt STAGING CA (untrusted certs, no rate
                      limit) — use this while testing DNS/firewall
  --force-cert        Re-issue the certificate even if a valid one exists
  --dir <path>        Install directory (default: $TARGET_DIR)
  --branch <name>     Git branch to deploy (default: $BRANCH)
  --repo <url>        Git remote to clone from
  -y, --yes           Assume yes for all confirmations (non-interactive)
  -h, --help          This text

Examples:
  sudo ./install.sh --domain panel.example.com --email me@example.com -y
  sudo ./install.sh --no-tls
EOF
}

while [ $# -gt 0 ]; do
    case "$1" in
        --domain)     DOMAIN="${2:-}"; shift 2 ;;
        --email)      EMAIL="${2:-}"; shift 2 ;;
        --no-tls)     USE_TLS=0; shift ;;
        --staging)    STAGING=1; shift ;;
        --force-cert) FORCE_CERT=1; shift ;;
        --dir)        TARGET_DIR="${2:-}"; shift 2 ;;
        --branch)     BRANCH="${2:-}"; shift 2 ;;
        --repo)       REPO_URL="${2:-}"; shift 2 ;;
        -y|--yes)     ASSUME_YES=1; shift ;;
        -h|--help)    usage; exit 0 ;;
        *)            die "Unknown option: $1 (try --help)" ;;
    esac
done

# ── preflight ──────────────────────────────────────────────────
[ "$(id -u)" = "0" ] || die "Run as root (sudo ./install.sh …) — it installs packages and binds ports 80/443."

pkg_install() {
    [ $# -gt 0 ] || return 0
    if command -v apt-get >/dev/null 2>&1; then
        DEBIAN_FRONTEND=noninteractive apt-get update -qq
        DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends "$@"
    elif command -v dnf >/dev/null 2>&1; then
        dnf install -y -q "$@"
    elif command -v yum >/dev/null 2>&1; then
        yum install -y -q "$@"
    else
        warn "No apt/dnf/yum found — install manually: $*"
        return 1
    fi
}

log "Checking prerequisites"
MISSING=()
for bin in curl git openssl; do
    command -v "$bin" >/dev/null 2>&1 || MISSING+=("$bin")
done
if [ ${#MISSING[@]} -gt 0 ]; then
    log "Installing: ${MISSING[*]}"
    pkg_install ca-certificates "${MISSING[@]}" || die "Could not install: ${MISSING[*]}"
fi
ok "curl, git, openssl present"

if ! command -v docker >/dev/null 2>&1; then
    log "Installing Docker (get.docker.com)"
    curl -fsSL https://get.docker.com -o /tmp/get-docker.sh || die "Could not download the Docker installer"
    sh /tmp/get-docker.sh >/dev/null || die "Docker installation failed"
    rm -f /tmp/get-docker.sh
fi
if command -v systemctl >/dev/null 2>&1; then
    systemctl enable --now docker >/dev/null 2>&1 || true
fi
docker info >/dev/null 2>&1 || die "Docker is installed but not running (try: systemctl start docker)"
ok "Docker $(docker version --format '{{.Server.Version}}' 2>/dev/null || echo '?')"

# Compose v2 (plugin) is required — the v1 `docker-compose` script cannot read
# this project's file (profiles, condition: service_healthy, …).
if docker compose version >/dev/null 2>&1; then
    DC="docker compose"
else
    log "Installing the Docker Compose plugin"
    pkg_install docker-compose-plugin >/dev/null 2>&1 || true
    docker compose version >/dev/null 2>&1 \
        || die "Docker Compose v2 is missing. Install the 'docker-compose-plugin' package and re-run."
    DC="docker compose"
fi
ok "Compose $($DC version --short 2>/dev/null || echo v2)"

# ── locate or fetch the sources ────────────────────────────────
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
if [ -f "$SCRIPT_DIR/docker-compose.yml" ] && [ -d "$SCRIPT_DIR/proxy" ]; then
    APP_DIR="$SCRIPT_DIR"
    log "Using the checkout this script lives in: $APP_DIR"
elif [ -f "$TARGET_DIR/docker-compose.yml" ]; then
    APP_DIR="$TARGET_DIR"
    log "Updating the existing install in $APP_DIR"
    if git -C "$APP_DIR" rev-parse --git-dir >/dev/null 2>&1; then
        if [ -z "$(git -C "$APP_DIR" status --porcelain)" ]; then
            if git -C "$APP_DIR" pull --ff-only >/dev/null 2>&1; then
                ok "Sources updated"
            else
                warn "git pull failed — continuing with the current checkout"
            fi
        else
            warn "Local changes present — skipping git pull"
        fi
    fi
else
    log "Cloning $REPO_URL ($BRANCH) into $TARGET_DIR"
    mkdir -p "$(dirname "$TARGET_DIR")"
    git clone --depth 1 --branch "$BRANCH" "$REPO_URL" "$TARGET_DIR" >/dev/null 2>&1 \
        || die "Clone failed. Private repo? Clone it manually, then run install.sh from inside it."
    APP_DIR="$TARGET_DIR"
fi
cd "$APP_DIR"
[ -f docker-compose.yml ] || die "docker-compose.yml not found in $APP_DIR"

# ── domain / e-mail ────────────────────────────────────────────
# A re-run is the documented way to update, so remember what the last run used:
# `sudo ./install.sh` with no flags must keep serving the same domain instead of
# asking again (or, worse, silently dropping back to HTTP).
ENV_FILE="$APP_DIR/.env"
env_get() { [ -f "$ENV_FILE" ] && sed -n "s/^$1=//p" "$ENV_FILE" | head -1 || true; }
if [ "$USE_TLS" = 1 ] && [ -z "$DOMAIN" ]; then
    DOMAIN="$(env_get PROXY_DOMAIN)"
    [ -n "$DOMAIN" ] && log "Reusing the configured domain: $DOMAIN"
fi
[ -z "$EMAIL" ] && EMAIL="$(env_get ACME_EMAIL)"

if [ "$USE_TLS" = 1 ] && [ -z "$DOMAIN" ]; then
    DOMAIN="$(ask "Domain for the panel (blank = HTTP only): " "")"
fi
if [ -z "$DOMAIN" ]; then
    USE_TLS=0
    warn "No domain given — the panel will be served over plain HTTP on port 80."
    warn "Session tokens would travel unencrypted; add a domain later with:"
    warn "  sudo ./install.sh --domain panel.example.com --email you@example.com"
else
    # Reject anything that is not a hostname: the value lands in an nginx
    # server_name and in certbot's -d argument.
    printf '%s' "$DOMAIN" | grep -Eq '^[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?$' \
        || die "'$DOMAIN' does not look like a domain name"
    case "$DOMAIN" in *.*) : ;; *) die "Use a fully-qualified domain (e.g. panel.example.com)" ;; esac
    # ⚠️ Lowercase it. certbot lowercases the lineage directory name, so
    # `--domain Panel.Example.COM` would store the certificate under
    # live/panel.example.com/ while the proxy looked for live/Panel.Example.COM/
    # — TLS would never switch on, and every run would re-issue.
    DOMAIN="$(printf '%s' "$DOMAIN" | tr '[:upper:]' '[:lower:]')"
fi

if [ "$USE_TLS" = 1 ] && [ -z "$EMAIL" ]; then
    EMAIL="$(ask "E-mail for Let's Encrypt expiry notices (blank = none): " "")"
fi

# ── DNS + port preflight ───────────────────────────────────────
resolve_a() {
    local host="$1"
    if command -v dig >/dev/null 2>&1;  then dig +short A "$host" 2>/dev/null | grep -E '^[0-9.]+$' | head -1; return; fi
    if command -v host >/dev/null 2>&1; then host -t A "$host" 2>/dev/null | awk '/has address/{print $4; exit}'; return; fi
    getent ahostsv4 "$host" 2>/dev/null | awk '{print $1; exit}'
}
public_ip() {
    local u
    for u in https://api.ipify.org https://ifconfig.me/ip https://icanhazip.com; do
        curl -fsS --max-time 6 "$u" 2>/dev/null | tr -d '[:space:]' && return 0
    done
    return 1
}

if [ "$USE_TLS" = 1 ]; then
    log "Checking that $DOMAIN points here"
    DNS_IP="$(resolve_a "$DOMAIN" || true)"
    MY_IP="$(public_ip || true)"
    if [ -z "$DNS_IP" ]; then
        warn "$DOMAIN has no A record yet."
    elif [ -n "$MY_IP" ] && [ "$DNS_IP" != "$MY_IP" ]; then
        warn "$DOMAIN resolves to $DNS_IP, but this server is $MY_IP."
        warn "(Fine if it is behind a proxy/CDN — but Let's Encrypt http-01 will fail.)"
    else
        ok "$DOMAIN → ${DNS_IP:-?}"
    fi
    if [ -z "$DNS_IP" ] || { [ -n "$MY_IP" ] && [ "$DNS_IP" != "$MY_IP" ]; }; then
        if ! confirm "Continue anyway? The stack will start, but the certificate may fail"; then
            die "Point the A record at ${MY_IP:-this server} and re-run."
        fi
    fi
fi

# A listener on 80/443 that is not ours will make the proxy fail to bind.
if command -v ss >/dev/null 2>&1; then
    for port in 80 443; do
        if ss -H -ltn "sport = :$port" 2>/dev/null | grep -q .; then
            if docker ps --format '{{.Names}} {{.Ports}}' 2>/dev/null | grep -q "node-installer-proxy.*:$port->"; then
                : # our own proxy from a previous run — compose will recreate it
            else
                warn "Port $port is already in use by something else:"
                ss -ltnp "sport = :$port" 2>/dev/null | tail -n +2 | sed 's/^/       /' >&2 || true
                confirm "Continue? The proxy will fail to start until that is stopped" \
                    || die "Free port $port (e.g. stop apache2/nginx) and re-run."
            fi
        fi
    done
fi

# ── .env: secrets and settings ─────────────────────────────────
# ENV_FILE / env_get are defined above (the domain is read back from .env).
rand_hex() { openssl rand -hex 32 2>/dev/null || head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n'; }

env_set() {
    local k="$1" v="$2" tmp
    if [ -f "$ENV_FILE" ] && grep -q "^${k}=" "$ENV_FILE"; then
        tmp="$(mktemp)"
        while IFS= read -r line || [ -n "$line" ]; do
            case "$line" in
                "${k}="*) printf '%s=%s\n' "$k" "$v" ;;
                *)        printf '%s\n' "$line" ;;
            esac
        done < "$ENV_FILE" > "$tmp"
        cat "$tmp" > "$ENV_FILE"   # keep the original inode/permissions
        rm -f "$tmp"
    else
        printf '%s=%s\n' "$k" "$v" >> "$ENV_FILE"
    fi
}

log "Writing $ENV_FILE"
if [ ! -f "$ENV_FILE" ]; then
    if [ -f "$APP_DIR/.env.example" ]; then
        # Strip CRs: a CRLF .env leaves a trailing \r inside every value, so
        # PROXY_DOMAIN would carry one into nginx's server_name and the
        # certificate path. (.gitattributes pins LF too — belt and braces.)
        tr -d '\r' < "$APP_DIR/.env.example" > "$ENV_FILE"
    else
        : > "$ENV_FILE"
    fi
fi
chmod 600 "$ENV_FILE"

# ENCRYPTION_KEY signs every session JWT and encrypts the credential vaults.
# Anything left at a placeholder value lets anyone forge a token for any account.
CUR_KEY="$(env_get ENCRYPTION_KEY)"
case "$CUR_KEY" in
    ""|change_me*|dev_key_change_in_production_000)
        env_set ENCRYPTION_KEY "$(rand_hex)"
        ok "Generated a fresh ENCRYPTION_KEY" ;;
    *)  ok "Kept the existing ENCRYPTION_KEY (sessions and vaults stay valid)" ;;
esac

# AGG_TOKEN guards the ungated /internal/agg-subs endpoint on the internal network.
if [ -z "$(env_get AGG_TOKEN)" ]; then
    env_set AGG_TOKEN "$(rand_hex)"
    ok "Generated AGG_TOKEN"
fi

if [ "$USE_TLS" = 1 ]; then
    env_set PROXY_DOMAIN "$DOMAIN"
    env_set CORS_ORIGIN "https://$DOMAIN"
    [ -n "$EMAIL" ] && env_set ACME_EMAIL "$EMAIL"
else
    env_set PROXY_DOMAIN ""
fi

# ── firewall ───────────────────────────────────────────────────
if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q "Status: active"; then
    ufw allow 80/tcp  >/dev/null 2>&1 || true
    ufw allow 443/tcp >/dev/null 2>&1 || true
    ok "Opened 80/443 in ufw"
elif command -v firewall-cmd >/dev/null 2>&1 && firewall-cmd --state >/dev/null 2>&1; then
    firewall-cmd --permanent --add-service=http  >/dev/null 2>&1 || true
    firewall-cmd --permanent --add-service=https >/dev/null 2>&1 || true
    firewall-cmd --reload >/dev/null 2>&1 || true
    ok "Opened 80/443 in firewalld"
fi

# ── build and start ────────────────────────────────────────────
log "Building images (first run takes a few minutes)"
$DC build --pull >/dev/null || die "Image build failed — re-run without redirection to see the log: $DC build"
ok "Images built"

log "Starting the stack"
$DC up -d || die "$DC up failed"

log "Waiting for the panel to answer on :80"
for i in $(seq 1 60); do
    if curl -fsS --max-time 3 http://127.0.0.1/healthz >/dev/null 2>&1; then break; fi
    [ "$i" = 60 ] && die "The proxy did not come up. Check: $DC logs proxy"
    sleep 2
done
ok "Proxy is serving"

for i in $(seq 1 60); do
    if curl -fsS --max-time 5 http://127.0.0.1/api/health >/dev/null 2>&1; then break; fi
    [ "$i" = 60 ] && warn "The backend is not answering /api/health yet — check: $DC logs backend"
    sleep 2
done

# ── certificate ────────────────────────────────────────────────
cert_exists() {
    $DC run --rm --entrypoint sh certbot -c \
        "[ -s /etc/letsencrypt/live/$DOMAIN/fullchain.pem ]" >/dev/null 2>&1
}
# A staging certificate looks exactly like a real one on disk, so file presence
# alone would make the run after a `--staging` shakeout silently keep the
# untrusted cert (and blame DNS for the browser warning). Check the issuer.
cert_is_staging() {
    $DC run --rm --entrypoint sh certbot -c \
        "openssl x509 -in /etc/letsencrypt/live/$DOMAIN/fullchain.pem -noout -issuer 2>/dev/null | grep -qi staging" \
        >/dev/null 2>&1
}

if [ "$USE_TLS" = 1 ]; then
    if [ "$FORCE_CERT" = 0 ] && [ "$STAGING" = 0 ] && cert_exists && cert_is_staging; then
        warn "The stored certificate for $DOMAIN was issued by the STAGING CA — replacing it with a real one."
        FORCE_CERT=1
    fi
    if [ "$FORCE_CERT" = 0 ] && cert_exists; then
        ok "A certificate for $DOMAIN already exists — reusing it"
    else
        log "Requesting a certificate for $DOMAIN"
        CB_ARGS=(certonly --webroot -w /var/www/certbot -d "$DOMAIN"
                 --non-interactive --agree-tos --no-eff-email --keep-until-expiring)
        if [ -n "$EMAIL" ]; then CB_ARGS+=(--email "$EMAIL"); else CB_ARGS+=(--register-unsafely-without-email); fi
        [ "$STAGING" = 1 ]    && CB_ARGS+=(--staging)
        [ "$FORCE_CERT" = 1 ] && CB_ARGS+=(--force-renewal)

        if $DC run --rm --entrypoint certbot certbot "${CB_ARGS[@]}"; then
            ok "Certificate issued"
        else
            warn "Certificate issuance FAILED. The panel still works over http://${DOMAIN}."
            warn "Most common causes, in order:"
            warn "  1. the A record for $DOMAIN does not point at this server"
            warn "  2. port 80 is blocked by a cloud/provider firewall (not just ufw)"
            warn "  3. Let's Encrypt rate limit — retry with --staging while debugging"
            warn "Re-run this script once fixed; nothing else needs redoing."
            USE_TLS=0
        fi
    fi
fi

# The proxy picks its template at start-up (cert present → TLS), so a restart is
# what flips a fresh certificate into service.
if [ "$USE_TLS" = 1 ]; then
    log "Enabling HTTPS"
    $DC restart proxy >/dev/null || warn "Could not restart the proxy — do it manually: $DC restart proxy"
    for i in $(seq 1 30); do
        if curl -fsSk --max-time 4 "https://127.0.0.1/healthz" >/dev/null 2>&1; then break; fi
        [ "$i" = 30 ] && warn "HTTPS is not answering locally — check: $DC logs proxy"
        sleep 2
    done
    if curl -fsS --max-time 8 "https://$DOMAIN/api/health" >/dev/null 2>&1; then
        ok "https://$DOMAIN is live with a trusted certificate"
    elif [ "$STAGING" = 1 ]; then
        ok "HTTPS is up with a STAGING certificate (browsers will warn — that is expected)"
    else
        warn "HTTPS is up locally but https://$DOMAIN was not reachable from this host."
        warn "Usually DNS propagation or a provider firewall — try again in a few minutes."
    fi
fi

# ── summary ────────────────────────────────────────────────────
URL="http://$(public_ip 2>/dev/null || echo 'your-server-ip')/"
[ "$USE_TLS" = 1 ] && URL="https://$DOMAIN/"

printf '\n%s────────────────────────────────────────────────────────%s\n' "$C_G" "$C_RST"
printf '%s node-assistant is installed%s\n' "$C_B" "$C_RST"
printf '%s────────────────────────────────────────────────────────%s\n' "$C_G" "$C_RST"
printf '  Panel:      %s%s%s\n' "$C_B" "$URL" "$C_RST"
printf '  Directory:  %s\n' "$APP_DIR"
printf '  Secrets:    %s (chmod 600 — back this up)\n' "$ENV_FILE"
if [ "$USE_TLS" = 1 ]; then
    printf '  TLS:        Let'\''s Encrypt%s, renewed automatically by the certbot container\n' \
        "$([ "$STAGING" = 1 ] && printf ' STAGING' || true)"
fi
printf '\n  First step: open the panel and create your account — the first account\n'
printf '              inherits any pre-existing data, so do it before sharing the URL.\n'
printf '\n  Logs:       cd %s && %s logs -f\n' "$APP_DIR" "$DC"
printf '  Restart:    cd %s && %s restart\n' "$APP_DIR" "$DC"
printf '  Update:     sudo %s/install.sh%s\n\n' "$APP_DIR" "$([ "$USE_TLS" = 1 ] && printf ' --domain %s' "$DOMAIN" || true)"
