#!/usr/bin/env bash
# node-assistant — installer and management CLI.
#
# One command on a fresh server:
#
#   curl -fsSL https://raw.githubusercontent.com/vitabled/node-assistant/main/install.sh \
#     | sudo bash -s -- --domain panel.example.com --email you@example.com
#
# After the first run the same script is reachable as `node-assistant`:
#
#   sudo node-assistant status          # what is running, cert expiry, version
#   sudo node-assistant check-updates   # is a newer version available?
#   sudo node-assistant update          # pull, rebuild, restart
#   sudo node-assistant set-domain new.example.com
#   sudo node-assistant set-ports --http-port 8080 --https-port 8443
#   sudo node-assistant reset-admin     # forgot the panel password
#
# Everything is idempotent: existing secrets and certificates are reused, never
# regenerated.
set -Eeuo pipefail

REPO_URL="${REPO_URL:-https://github.com/vitabled/node-assistant.git}"
BRANCH="${BRANCH:-main}"
TARGET_DIR="${TARGET_DIR:-/opt/node-assistant}"
SHORTCUT="${SHORTCUT:-/usr/local/bin/node-assistant}"

COMMAND=""
DOMAIN=""
EMAIL=""
HTTP_PORT=""
HTTPS_PORT=""
ASSUME_YES=0
USE_TLS=1
STAGING=0
FORCE_CERT=0
ASK_PORTS=1          # install prompts for the web ports unless they came as flags
RESET_LOGIN=""       # reset-admin: which superuser
RESET_GENERATE=0     # reset-admin: generate the password instead of asking

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

# Prompts must read the TERMINAL, not stdin: piped through `curl | bash`, stdin is
# the script itself and a plain `read` would swallow the rest of it.
ask() {
    local prompt="$1" default="${2:-}" reply=""
    if [ ! -r /dev/tty ]; then printf '%s' "$default"; return 0; fi
    read -r -p "$prompt" reply < /dev/tty || reply=""
    printf '%s' "${reply:-$default}"
}
confirm() {
    [ "$ASSUME_YES" = 1 ] && return 0
    local a; a="$(ask "$1 [y/N]: " "n")"
    case "$a" in [yY]*) return 0 ;; *) return 1 ;; esac
}

# Same terminal-not-stdin rule, with echo off. `read -p` prints the prompt on
# stderr, so command substitution still captures only the typed value.
# Asked twice on purpose: the input is invisible, and a typo here locks the owner
# out a second time.
ask_secret() {
    local prompt="$1" first="" again=""
    # Opened in a subshell instead of testing with `-r`: inside a container
    # /dev/tty is a readable device node that still refuses to open, and the raw
    # `read` failures that follow look like a bug, not "you have no terminal".
    ( : < /dev/tty ) 2>/dev/null \
        || die "No terminal to read the password from — use --generate."
    read -r -s -p "$prompt" first < /dev/tty || first=""
    printf '\n' >&2
    read -r -s -p "Repeat: " again < /dev/tty || again=""
    printf '\n' >&2
    [ "$first" = "$again" ] || die "The two entries differ — nothing was changed."
    printf '%s' "$first"
}

usage() {
    cat <<EOF
${C_B}node-assistant${C_RST} — installer and management CLI

Usage: sudo ./install.sh [command] [options]
       sudo node-assistant [command] [options]

Commands:
  install            (default) install, or re-install/repair an existing setup
  status             stack state, certificate expiry, installed version
  check-updates      report whether a newer version is available (exit 10 if yes)
  update             fetch, rebuild and restart on the tracked branch
  set-domain <fqdn>  change the panel domain and issue a certificate for it
  set-ports          change the web entry ports (HTTP/HTTPS published on the host)
  reset-admin        set a new password for a panel superuser (locked out?)

reset-admin options:
  --login <name>      Which superuser (required only if there are several)
  --generate          Generate the password and print it once

Install options:
  --domain <fqdn>     Domain to serve the panel on (enables HTTPS)
  --email <mail>      Contact e-mail for Let's Encrypt expiry notices
  --no-tls            Install without a domain — plain HTTP only
  --http-port <n>     Host port for HTTP  (default 80)
  --https-port <n>    Host port for HTTPS (default 443)
  --default-ports     Keep 80/443 without asking
  --staging           Use the Let's Encrypt STAGING CA (untrusted, no rate limit)
  --force-cert        Re-issue the certificate even if a valid one exists
  --dir <path>        Install directory (default: $TARGET_DIR)
  --branch <name>     Git branch to deploy (default: $BRANCH)
  --repo <url>        Git remote to clone from
  -y, --yes           Assume yes for every confirmation (non-interactive)
  -h, --help          This text

Examples:
  sudo ./install.sh --domain panel.example.com --email me@example.com -y
  sudo ./install.sh --no-tls --http-port 8080 --default-ports
  sudo node-assistant update
  sudo node-assistant reset-admin --login owner --generate
EOF
}

# ── argument parsing ───────────────────────────────────────────
case "${1:-}" in
    install|status|check-updates|update|set-domain|set-ports|reset-admin) COMMAND="$1"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) COMMAND="install" ;;
esac

# `set-domain <fqdn>` takes the domain positionally too.
if [ "$COMMAND" = "set-domain" ] && [ $# -gt 0 ]; then
    case "${1:-}" in -*) : ;; *) DOMAIN="$1"; shift ;; esac
fi

while [ $# -gt 0 ]; do
    case "$1" in
        --domain)        DOMAIN="${2:-}"; shift 2 ;;
        --email)         EMAIL="${2:-}"; shift 2 ;;
        --http-port)     HTTP_PORT="${2:-}";  ASK_PORTS=0; shift 2 ;;
        --https-port)    HTTPS_PORT="${2:-}"; ASK_PORTS=0; shift 2 ;;
        --default-ports) ASK_PORTS=0; shift ;;
        --login)         RESET_LOGIN="${2:-}"; shift 2 ;;
        --generate)      RESET_GENERATE=1; shift ;;
        --no-tls)        USE_TLS=0; shift ;;
        --staging)       STAGING=1; shift ;;
        --force-cert)    FORCE_CERT=1; shift ;;
        --dir)           TARGET_DIR="${2:-}"; shift 2 ;;
        --branch)        BRANCH="${2:-}"; shift 2 ;;
        --repo)          REPO_URL="${2:-}"; shift 2 ;;
        -y|--yes)        ASSUME_YES=1; ASK_PORTS=0; shift ;;
        -h|--help)       usage; exit 0 ;;
        *)               die "Unknown option: $1 (try --help)" ;;
    esac
done

[ "$(id -u)" = "0" ] || die "Run as root (sudo …) — it manages containers, packages and ports 80/443."

# ── locating the checkout ──────────────────────────────────────
# `readlink -f` matters: the $SHORTCUT is a symlink into the checkout, so without
# resolving it every management command would look for the repo in /usr/local/bin.
SELF=""
if [ -n "${BASH_SOURCE[0]:-}" ] && [ -f "${BASH_SOURCE[0]}" ]; then
    SELF="$(readlink -f "${BASH_SOURCE[0]}" 2>/dev/null || printf '%s' "${BASH_SOURCE[0]}")"
fi
SCRIPT_DIR=""
[ -n "$SELF" ] && SCRIPT_DIR="$(cd -- "$(dirname -- "$SELF")" && pwd)"

is_checkout() { [ -f "${1:-}/docker-compose.yml" ] && [ -d "${1:-}/proxy" ]; }

APP_DIR=""
if is_checkout "$SCRIPT_DIR"; then
    APP_DIR="$SCRIPT_DIR"
elif is_checkout "$TARGET_DIR"; then
    APP_DIR="$TARGET_DIR"
fi

DC=""
resolve_dc() {
    if docker compose version >/dev/null 2>&1; then DC="docker compose"
    else die "Docker Compose v2 is missing. Install the docker-compose-plugin package."; fi
}

need_install() {
    [ -n "$APP_DIR" ] || die "node-assistant is not installed here. Run the installer first (see --help)."
    cd "$APP_DIR"
    resolve_dc
}

# ── .env helpers ───────────────────────────────────────────────
env_get() { [ -f "${ENV_FILE:-}" ] && sed -n "s/^$1=//p" "$ENV_FILE" | head -1 || true; }
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
        cat "$tmp" > "$ENV_FILE"   # keep the inode/permissions
        rm -f "$tmp"
    else
        printf '%s=%s\n' "$k" "$v" >> "$ENV_FILE"
    fi
}
rand_hex() { openssl rand -hex 32 2>/dev/null || head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n'; }

# ── shared validation ──────────────────────────────────────────
valid_port() {
    case "${1:-}" in ''|*[!0-9]*) return 1 ;; esac
    [ "$1" -ge 1 ] && [ "$1" -le 65535 ]
}
normalize_domain() {
    local d="${1:-}"
    printf '%s' "$d" | grep -Eq '^[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?$' \
        || die "'$d' does not look like a domain name"
    case "$d" in *.*) : ;; *) die "Use a fully-qualified domain (e.g. panel.example.com)" ;; esac
    # ⚠️ Lowercase: certbot lowercases the lineage directory, so `--domain
    # Panel.Example.COM` would store the cert under live/panel.example.com/ while
    # the proxy looked for the mixed-case path — TLS would never switch on.
    printf '%s' "$d" | tr '[:upper:]' '[:lower:]'
}
resolve_a() {
    local host="$1"
    if command -v dig  >/dev/null 2>&1; then dig +short A "$host" 2>/dev/null | grep -E '^[0-9.]+$' | head -1; return; fi
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
dns_preflight() {
    local d="$1" dns_ip my_ip
    log "Checking that $d points here"
    dns_ip="$(resolve_a "$d" || true)"; my_ip="$(public_ip || true)"
    if [ -z "$dns_ip" ]; then
        warn "$d has no A record yet."
    elif [ -n "$my_ip" ] && [ "$dns_ip" != "$my_ip" ]; then
        warn "$d resolves to $dns_ip, but this server is $my_ip."
        warn "(Fine behind a proxy/CDN — but Let's Encrypt http-01 will fail.)"
    else
        ok "$d → ${dns_ip:-?}"; return 0
    fi
    confirm "Continue anyway? The stack will start, but the certificate may fail" \
        || die "Point the A record at ${my_ip:-this server} and try again."
}
port_free_or_ours() {
    local port="$1"
    command -v ss >/dev/null 2>&1 || return 0
    ss -H -ltn "sport = :$port" 2>/dev/null | grep -q . || return 0
    if docker ps --format '{{.Names}} {{.Ports}}' 2>/dev/null | grep -q "node-installer-proxy.*:$port->"; then
        return 0    # our own proxy from a previous run — compose will recreate it
    fi
    warn "Port $port is already in use by something else:"
    ss -ltnp "sport = :$port" 2>/dev/null | tail -n +2 | sed 's/^/       /' >&2 || true
    confirm "Continue? The proxy will fail to start until that is freed" \
        || die "Free port $port (e.g. systemctl disable --now nginx apache2) and try again."
}

# Certificates are validated over public port 80 — nothing we can change.
warn_http01_port() {
    [ "${HTTP_PORT:-80}" = "80" ] && return 0
    warn "HTTP is published on ${HTTP_PORT}, not 80 — Let's Encrypt http-01 validation"
    warn "cannot reach it, so no certificate can be issued automatically."
    warn "Either publish 80 (forward it to ${HTTP_PORT} upstream), or bring your own"
    warn "certificate into the node-letsencrypt volume."
}

# ── certificate helpers ────────────────────────────────────────
cert_exists() {
    $DC run --rm --entrypoint sh certbot -c \
        "[ -s /etc/letsencrypt/live/$1/fullchain.pem ]" >/dev/null 2>&1
}
# A staging certificate is indistinguishable from a real one by filename, so
# presence alone would silently keep an untrusted cert after a --staging run.
cert_is_staging() {
    $DC run --rm --entrypoint sh certbot -c \
        "openssl x509 -in /etc/letsencrypt/live/$1/fullchain.pem -noout -issuer 2>/dev/null | grep -qi staging" \
        >/dev/null 2>&1
}
cert_enddate() {
    $DC run --rm --entrypoint sh certbot -c \
        "openssl x509 -in /etc/letsencrypt/live/$1/fullchain.pem -noout -enddate 2>/dev/null | cut -d= -f2" \
        2>/dev/null | tr -d '\r' | tail -1
}

issue_cert() {
    local d="$1"
    if [ "$FORCE_CERT" = 0 ] && [ "$STAGING" = 0 ] && cert_exists "$d" && cert_is_staging "$d"; then
        warn "The stored certificate for $d came from the STAGING CA — replacing it."
        FORCE_CERT=1
    fi
    if [ "$FORCE_CERT" = 0 ] && cert_exists "$d"; then
        ok "A certificate for $d already exists — reusing it"
        return 0
    fi
    log "Requesting a certificate for $d"
    local args=(certonly --webroot -w /var/www/certbot -d "$d"
                --non-interactive --agree-tos --no-eff-email --keep-until-expiring)
    if [ -n "$EMAIL" ]; then args+=(--email "$EMAIL"); else args+=(--register-unsafely-without-email); fi
    [ "$STAGING" = 1 ]    && args+=(--staging)
    [ "$FORCE_CERT" = 1 ] && args+=(--force-renewal)

    if $DC run --rm --entrypoint certbot certbot "${args[@]}"; then
        ok "Certificate issued"
        return 0
    fi
    warn "Certificate issuance FAILED — the panel still works over HTTP."
    warn "Most common causes, in order:"
    warn "  1. the A record for $d does not point at this server"
    warn "  2. port 80 is blocked by a cloud/provider firewall (not just ufw)"
    warn "  3. Let's Encrypt rate limit — retry with --staging while debugging"
    return 1
}

# Bring the stack to the current .env and wait for the entry point.
apply_stack() {
    log "Applying configuration"
    $DC up -d || die "$DC up failed"
    local p; p="$(env_get HTTP_PORT)"; [ -n "$p" ] || p=80
    local i
    for i in $(seq 1 60); do
        curl -fsS --max-time 3 "http://127.0.0.1:$p/healthz" >/dev/null 2>&1 && break
        [ "$i" = 60 ] && { warn "The proxy is not answering on :$p — check: $DC logs proxy"; return 0; }
        sleep 2
    done
    ok "Proxy is serving on :$p"
}

# The proxy picks its template at start-up (cert present → TLS), so this is what
# flips a freshly issued certificate into service.
reload_proxy() { $DC up -d proxy >/dev/null 2>&1 || true; $DC restart proxy >/dev/null 2>&1 || true; }

panel_url() {
    local d hp sp
    d="$(env_get PROXY_DOMAIN)"; hp="$(env_get HTTP_PORT)"; sp="$(env_get HTTPS_PORT)"
    [ -n "$hp" ] || hp=80; [ -n "$sp" ] || sp=443
    if [ -n "$d" ] && cert_exists "$d"; then
        [ "$sp" = "443" ] && printf 'https://%s/' "$d" || printf 'https://%s:%s/' "$d" "$sp"
    else
        local host; host="$(public_ip 2>/dev/null || echo 'your-server-ip')"
        [ "$hp" = "80" ] && printf 'http://%s/' "$host" || printf 'http://%s:%s/' "$host" "$hp"
    fi
}

# ── command: install ───────────────────────────────────────────
cmd_install() {
    local pkg_missing=()
    log "Checking prerequisites"
    for bin in curl git openssl; do command -v "$bin" >/dev/null 2>&1 || pkg_missing+=("$bin"); done
    if [ ${#pkg_missing[@]} -gt 0 ]; then
        log "Installing: ${pkg_missing[*]}"
        if command -v apt-get >/dev/null 2>&1; then
            DEBIAN_FRONTEND=noninteractive apt-get update -qq
            DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends ca-certificates "${pkg_missing[@]}"
        elif command -v dnf >/dev/null 2>&1; then dnf install -y -q "${pkg_missing[@]}"
        elif command -v yum >/dev/null 2>&1; then yum install -y -q "${pkg_missing[@]}"
        else die "No apt/dnf/yum — install manually: ${pkg_missing[*]}"; fi
    fi
    ok "curl, git, openssl present"

    if ! command -v docker >/dev/null 2>&1; then
        log "Installing Docker (get.docker.com)"
        curl -fsSL https://get.docker.com -o /tmp/get-docker.sh || die "Could not download the Docker installer"
        sh /tmp/get-docker.sh >/dev/null || die "Docker installation failed"
        rm -f /tmp/get-docker.sh
    fi
    command -v systemctl >/dev/null 2>&1 && { systemctl enable --now docker >/dev/null 2>&1 || true; }
    docker info >/dev/null 2>&1 || die "Docker is installed but not running (try: systemctl start docker)"
    ok "Docker $(docker version --format '{{.Server.Version}}' 2>/dev/null || echo '?')"

    if ! docker compose version >/dev/null 2>&1; then
        log "Installing the Docker Compose plugin"
        command -v apt-get >/dev/null 2>&1 && \
            DEBIAN_FRONTEND=noninteractive apt-get install -y -qq docker-compose-plugin >/dev/null 2>&1 || true
    fi
    resolve_dc
    ok "Compose $($DC version --short 2>/dev/null || echo v2)"

    # sources
    if [ -n "$APP_DIR" ]; then
        log "Using the existing checkout: $APP_DIR"
    else
        log "Cloning $REPO_URL ($BRANCH) into $TARGET_DIR"
        mkdir -p "$(dirname "$TARGET_DIR")"
        git clone --depth 1 --branch "$BRANCH" "$REPO_URL" "$TARGET_DIR" >/dev/null 2>&1 \
            || die "Clone failed. Check the URL/branch, or clone manually and run install.sh from inside."
        APP_DIR="$TARGET_DIR"
    fi
    cd "$APP_DIR"
    ENV_FILE="$APP_DIR/.env"

    # A re-run is the documented way to update, so remember the last settings.
    [ "$USE_TLS" = 1 ] && [ -z "$DOMAIN" ] && DOMAIN="$(env_get PROXY_DOMAIN)" && [ -n "$DOMAIN" ] \
        && log "Reusing the configured domain: $DOMAIN"
    [ -z "$EMAIL" ] && EMAIL="$(env_get ACME_EMAIL)"

    if [ "$USE_TLS" = 1 ] && [ -z "$DOMAIN" ]; then
        DOMAIN="$(ask "Domain for the panel (blank = HTTP only): " "")"
    fi
    if [ -z "$DOMAIN" ]; then
        USE_TLS=0
        warn "No domain given — the panel will be served over plain HTTP."
        warn "Session tokens would travel unencrypted; add a domain later with:"
        warn "  sudo node-assistant set-domain panel.example.com"
    else
        DOMAIN="$(normalize_domain "$DOMAIN")"
    fi
    [ "$USE_TLS" = 1 ] && [ -z "$EMAIL" ] && \
        EMAIL="$(ask "E-mail for Let's Encrypt expiry notices (blank = none): " "")"

    # ── web entry ports ────────────────────────────────────────
    [ -z "$HTTP_PORT" ]  && HTTP_PORT="$(env_get HTTP_PORT)"
    [ -z "$HTTPS_PORT" ] && HTTPS_PORT="$(env_get HTTPS_PORT)"
    if [ "$ASK_PORTS" = 1 ] && [ -r /dev/tty ]; then
        printf '\n  Web entry ports (press Enter to keep the defaults 80 and 443).\n'
        printf '  Change these only if something else already owns them — note that\n'
        printf '  Let%ss Encrypt validates over public port 80.\n' "'"
        HTTP_PORT="$(ask "  HTTP port  [${HTTP_PORT:-80}]: "  "${HTTP_PORT:-80}")"
        HTTPS_PORT="$(ask "  HTTPS port [${HTTPS_PORT:-443}]: " "${HTTPS_PORT:-443}")"
        printf '\n'
    fi
    [ -n "$HTTP_PORT" ]  || HTTP_PORT=80
    [ -n "$HTTPS_PORT" ] || HTTPS_PORT=443
    valid_port "$HTTP_PORT"  || die "Invalid HTTP port: $HTTP_PORT"
    valid_port "$HTTPS_PORT" || die "Invalid HTTPS port: $HTTPS_PORT"
    [ "$HTTP_PORT" = "$HTTPS_PORT" ] && die "HTTP and HTTPS ports must differ"
    ok "Web entry ports: HTTP $HTTP_PORT, HTTPS $HTTPS_PORT"

    [ "$USE_TLS" = 1 ] && dns_preflight "$DOMAIN"
    port_free_or_ours "$HTTP_PORT"
    port_free_or_ours "$HTTPS_PORT"

    # ── .env ───────────────────────────────────────────────────
    log "Writing $ENV_FILE"
    if [ ! -f "$ENV_FILE" ]; then
        if [ -f "$APP_DIR/.env.example" ]; then
            # Strip CRs: a CRLF .env leaves a trailing \r inside every value, so
            # PROXY_DOMAIN would carry one into nginx's server_name and cert path.
            tr -d '\r' < "$APP_DIR/.env.example" > "$ENV_FILE"
        else
            : > "$ENV_FILE"
        fi
    fi
    chmod 600 "$ENV_FILE"

    # ENCRYPTION_KEY signs every session JWT and encrypts the credential vaults —
    # regenerating it would invalidate all sessions and stored secrets.
    case "$(env_get ENCRYPTION_KEY)" in
        ""|change_me*|dev_key_change_in_production_000)
            env_set ENCRYPTION_KEY "$(rand_hex)"; ok "Generated a fresh ENCRYPTION_KEY" ;;
        *)  ok "Kept the existing ENCRYPTION_KEY (sessions and vaults stay valid)" ;;
    esac
    [ -z "$(env_get AGG_TOKEN)" ] && { env_set AGG_TOKEN "$(rand_hex)"; ok "Generated AGG_TOKEN"; }

    env_set HTTP_PORT  "$HTTP_PORT"
    env_set HTTPS_PORT "$HTTPS_PORT"
    if [ "$USE_TLS" = 1 ]; then
        env_set PROXY_DOMAIN "$DOMAIN"
        env_set PROXY_HTTPS_PORT "$HTTPS_PORT"
        if [ "$HTTPS_PORT" = "443" ]; then env_set CORS_ORIGIN "https://$DOMAIN"
        else env_set CORS_ORIGIN "https://$DOMAIN:$HTTPS_PORT"; fi
        [ -n "$EMAIL" ] && env_set ACME_EMAIL "$EMAIL"
    else
        env_set PROXY_DOMAIN ""
    fi

    # ── firewall ───────────────────────────────────────────────
    if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q "Status: active"; then
        ufw allow "$HTTP_PORT/tcp"  >/dev/null 2>&1 || true
        ufw allow "$HTTPS_PORT/tcp" >/dev/null 2>&1 || true
        ok "Opened $HTTP_PORT/$HTTPS_PORT in ufw"
    elif command -v firewall-cmd >/dev/null 2>&1 && firewall-cmd --state >/dev/null 2>&1; then
        firewall-cmd --permanent --add-port="$HTTP_PORT/tcp"  >/dev/null 2>&1 || true
        firewall-cmd --permanent --add-port="$HTTPS_PORT/tcp" >/dev/null 2>&1 || true
        firewall-cmd --reload >/dev/null 2>&1 || true
        ok "Opened $HTTP_PORT/$HTTPS_PORT in firewalld"
    fi

    log "Building images (the first run takes a few minutes)"
    $DC build --pull >/dev/null || die "Image build failed — see: $DC build"
    ok "Images built"
    apply_stack

    for i in $(seq 1 60); do
        curl -fsS --max-time 5 "http://127.0.0.1:$HTTP_PORT/api/health" >/dev/null 2>&1 && break
        [ "$i" = 60 ] && warn "The backend is not answering /api/health — check: $DC logs backend"
        sleep 2
    done

    if [ "$USE_TLS" = 1 ]; then
        if [ "$HTTP_PORT" != "80" ]; then
            warn_http01_port
        elif issue_cert "$DOMAIN"; then
            log "Enabling HTTPS"; reload_proxy
        fi
    fi

    install_shortcut
    print_summary
}

# ── first-run shortcut ─────────────────────────────────────────
install_shortcut() {
    # Symlink, not a copy: the shortcut must always run the CURRENT checkout, so
    # `node-assistant update` cannot leave a stale management CLI behind.
    chmod +x "$APP_DIR/install.sh" 2>/dev/null || true
    if [ -L "$SHORTCUT" ] && [ "$(readlink -f "$SHORTCUT" 2>/dev/null)" = "$(readlink -f "$APP_DIR/install.sh")" ]; then
        return 0
    fi
    if [ -e "$SHORTCUT" ] && [ ! -L "$SHORTCUT" ]; then
        warn "$SHORTCUT exists and is not a symlink — leaving it alone."
        return 0
    fi
    if ln -sfn "$APP_DIR/install.sh" "$SHORTCUT" 2>/dev/null; then
        ok "Installed the shortcut: $(basename "$SHORTCUT")"
    else
        warn "Could not create $SHORTCUT — use $APP_DIR/install.sh directly."
    fi
}

print_summary() {
    local url; url="$(panel_url)"
    local d; d="$(env_get PROXY_DOMAIN)"
    printf '\n%s────────────────────────────────────────────────────────%s\n' "$C_G" "$C_RST"
    printf '%s node-assistant is ready%s\n' "$C_B" "$C_RST"
    printf '%s────────────────────────────────────────────────────────%s\n' "$C_G" "$C_RST"
    printf '  Panel:      %s%s%s\n' "$C_B" "$url" "$C_RST"
    printf '  Directory:  %s\n' "$APP_DIR"
    printf '  Secrets:    %s (chmod 600 — back this up)\n' "$ENV_FILE"
    printf '  Ports:      HTTP %s · HTTPS %s\n' "$(env_get HTTP_PORT)" "$(env_get HTTPS_PORT)"
    if [ -n "$d" ] && cert_exists "$d"; then
        printf '  TLS:        %s, expires %s (renewed automatically)\n' \
            "$([ "$STAGING" = 1 ] && printf "Let's Encrypt STAGING" || printf "Let's Encrypt")" \
            "$(cert_enddate "$d")"
    fi
    printf '\n  First step: open the panel and create your account — the first account\n'
    printf '              inherits pre-existing data, so do it before sharing the URL.\n'
    printf '\n  Manage:     sudo node-assistant status | check-updates | update\n'
    printf '              sudo node-assistant set-domain <fqdn>\n'
    printf '              sudo node-assistant set-ports --http-port N --https-port N\n'
    printf '  Logs:       cd %s && %s logs -f\n\n' "$APP_DIR" "$DC"
}

# ── command: status ────────────────────────────────────────────
cmd_status() {
    need_install
    ENV_FILE="$APP_DIR/.env"
    local d; d="$(env_get PROXY_DOMAIN)"
    printf '%sInstallation%s\n' "$C_B" "$C_RST"
    printf '  directory : %s\n' "$APP_DIR"
    printf '  panel     : %s\n' "$(panel_url)"
    printf '  ports     : HTTP %s · HTTPS %s\n' "$(env_get HTTP_PORT || echo 80)" "$(env_get HTTPS_PORT || echo 443)"
    printf '  domain    : %s\n' "${d:-(none — HTTP only)}"
    if [ -n "$d" ] && cert_exists "$d"; then
        printf '  certificate: expires %s%s\n' "$(cert_enddate "$d")" \
            "$(cert_is_staging "$d" && printf ' [STAGING — untrusted]' || true)"
    elif [ -n "$d" ]; then
        printf '  certificate: none yet\n'
    fi
    if git -C "$APP_DIR" rev-parse --git-dir >/dev/null 2>&1; then
        printf '  version   : %s @ %s\n' \
            "$(git -C "$APP_DIR" rev-parse --abbrev-ref HEAD)" \
            "$(git -C "$APP_DIR" rev-parse --short HEAD)"
    fi
    printf '\n%sServices%s\n' "$C_B" "$C_RST"
    $DC ps --format '  {{.Service}}\t{{.State}}\t{{.Status}}' 2>/dev/null \
        || $DC ps 2>/dev/null | tail -n +2 | sed 's/^/  /'
}

# ── commands: check-updates / update ───────────────────────────
# Returns 0 = up to date, 10 = an update is available (handy for cron).
git_update_state() {
    git -C "$APP_DIR" rev-parse --git-dir >/dev/null 2>&1 \
        || die "$APP_DIR is not a git checkout — updates must be applied manually."
    UP_BRANCH="$(git -C "$APP_DIR" rev-parse --abbrev-ref HEAD)"
    log "Fetching origin/$UP_BRANCH"
    git -C "$APP_DIR" fetch --quiet origin "$UP_BRANCH" 2>/dev/null \
        || die "Could not reach the git remote."
    UP_LOCAL="$(git -C "$APP_DIR" rev-parse HEAD)"
    UP_REMOTE="$(git -C "$APP_DIR" rev-parse FETCH_HEAD)"
    UP_BEHIND="$(git -C "$APP_DIR" rev-list --count HEAD..FETCH_HEAD 2>/dev/null || echo 0)"
}

cmd_check_updates() {
    need_install
    git_update_state
    if [ "$UP_LOCAL" = "$UP_REMOTE" ] || [ "$UP_BEHIND" = "0" ]; then
        ok "Up to date ($UP_BRANCH @ $(git -C "$APP_DIR" rev-parse --short HEAD))"
        return 0
    fi
    printf '%s  update available%s — %s new commit(s) on %s\n' "$C_Y" "$C_RST" "$UP_BEHIND" "$UP_BRANCH"
    printf '  installed: %s\n  available: %s\n\n' "${UP_LOCAL:0:8}" "${UP_REMOTE:0:8}"
    git -C "$APP_DIR" log --no-merges --pretty='  · %s' HEAD..FETCH_HEAD | head -15
    printf '\n  Apply with: sudo node-assistant update\n'
    return 10
}

cmd_update() {
    need_install
    git_update_state
    if [ "$UP_LOCAL" = "$UP_REMOTE" ] || [ "$UP_BEHIND" = "0" ]; then
        ok "Already up to date ($UP_BRANCH @ $(git -C "$APP_DIR" rev-parse --short HEAD))"
        return 0
    fi
    printf '  %s new commit(s):\n' "$UP_BEHIND"
    git -C "$APP_DIR" log --no-merges --pretty='  · %s' HEAD..FETCH_HEAD | head -15
    if [ -n "$(git -C "$APP_DIR" status --porcelain)" ]; then
        warn "The checkout has local modifications:"
        git -C "$APP_DIR" status --short | sed 's/^/       /' >&2
        die "Commit, stash or discard them first — refusing to overwrite local work."
    fi
    confirm "Update now? The panel restarts and is briefly unavailable" || die "Aborted."

    log "Updating sources"
    git -C "$APP_DIR" merge --ff-only FETCH_HEAD >/dev/null || die "Fast-forward failed — resolve manually."
    ok "Now at $(git -C "$APP_DIR" rev-parse --short HEAD)"

    log "Rebuilding images"
    $DC build --pull >/dev/null || die "Build failed — see: $DC build"
    apply_stack
    # An update may have changed the proxy templates or the entrypoint.
    reload_proxy
    install_shortcut
    ok "Update complete"
    ENV_FILE="$APP_DIR/.env"
    printf '  Panel: %s\n' "$(panel_url)"
}

# ── command: set-domain ────────────────────────────────────────
cmd_set_domain() {
    need_install
    ENV_FILE="$APP_DIR/.env"
    [ -f "$ENV_FILE" ] || die "$ENV_FILE is missing — run the installer first."
    if [ -z "$DOMAIN" ]; then
        DOMAIN="$(ask "New domain for the panel (blank = disable TLS): " "")"
    fi
    local old; old="$(env_get PROXY_DOMAIN)"

    if [ -z "$DOMAIN" ]; then
        confirm "Serve the panel over plain HTTP (no TLS)?" || die "Aborted."
        env_set PROXY_DOMAIN ""
        env_set CORS_ORIGIN "http://localhost"
        apply_stack; reload_proxy
        ok "TLS disabled — the panel is served over HTTP only"
        printf '  Panel: %s\n' "$(panel_url)"
        return 0
    fi

    DOMAIN="$(normalize_domain "$DOMAIN")"
    [ "$DOMAIN" = "$old" ] && log "Domain is already $DOMAIN — re-checking the certificate"
    HTTP_PORT="$(env_get HTTP_PORT)";  [ -n "$HTTP_PORT" ]  || HTTP_PORT=80
    HTTPS_PORT="$(env_get HTTPS_PORT)"; [ -n "$HTTPS_PORT" ] || HTTPS_PORT=443
    [ -z "$EMAIL" ] && EMAIL="$(env_get ACME_EMAIL)"

    dns_preflight "$DOMAIN"

    env_set PROXY_DOMAIN "$DOMAIN"
    env_set PROXY_HTTPS_PORT "$HTTPS_PORT"
    if [ "$HTTPS_PORT" = "443" ]; then env_set CORS_ORIGIN "https://$DOMAIN"
    else env_set CORS_ORIGIN "https://$DOMAIN:$HTTPS_PORT"; fi
    [ -n "$EMAIL" ] && env_set ACME_EMAIL "$EMAIL"

    # Start on the new name FIRST: the ACME challenge is served by this proxy, so
    # it has to answer for $DOMAIN before certbot can validate it.
    apply_stack

    if [ "$HTTP_PORT" != "80" ]; then
        warn_http01_port
    elif issue_cert "$DOMAIN"; then
        reload_proxy
        ok "Panel is now on https://$DOMAIN${HTTPS_PORT:+$([ "$HTTPS_PORT" = 443 ] && echo "" || echo ":$HTTPS_PORT")}/"
    else
        reload_proxy
    fi
    [ -n "$old" ] && [ "$old" != "$DOMAIN" ] && \
        log "The old certificate for $old stays in the volume, unused."
    printf '  Panel: %s\n' "$(panel_url)"
}

# ── command: set-ports ─────────────────────────────────────────
cmd_set_ports() {
    need_install
    ENV_FILE="$APP_DIR/.env"
    [ -f "$ENV_FILE" ] || die "$ENV_FILE is missing — run the installer first."
    local cur_http cur_https
    cur_http="$(env_get HTTP_PORT)";   [ -n "$cur_http" ]  || cur_http=80
    cur_https="$(env_get HTTPS_PORT)"; [ -n "$cur_https" ] || cur_https=443

    [ -z "$HTTP_PORT" ]  && HTTP_PORT="$(ask "HTTP port  [$cur_http]: "  "$cur_http")"
    [ -z "$HTTPS_PORT" ] && HTTPS_PORT="$(ask "HTTPS port [$cur_https]: " "$cur_https")"
    valid_port "$HTTP_PORT"  || die "Invalid HTTP port: $HTTP_PORT"
    valid_port "$HTTPS_PORT" || die "Invalid HTTPS port: $HTTPS_PORT"
    [ "$HTTP_PORT" = "$HTTPS_PORT" ] && die "HTTP and HTTPS ports must differ"

    if [ "$HTTP_PORT" = "$cur_http" ] && [ "$HTTPS_PORT" = "$cur_https" ]; then
        ok "Ports unchanged (HTTP $cur_http, HTTPS $cur_https)"
        return 0
    fi
    [ "$HTTP_PORT" != "$cur_http" ]   && port_free_or_ours "$HTTP_PORT"
    [ "$HTTPS_PORT" != "$cur_https" ] && port_free_or_ours "$HTTPS_PORT"

    env_set HTTP_PORT  "$HTTP_PORT"
    env_set HTTPS_PORT "$HTTPS_PORT"
    # The 301 from HTTP has to name a non-standard HTTPS port explicitly, or the
    # browser would be sent to :443 where nothing listens.
    env_set PROXY_HTTPS_PORT "$HTTPS_PORT"
    local d; d="$(env_get PROXY_DOMAIN)"
    if [ -n "$d" ]; then
        if [ "$HTTPS_PORT" = "443" ]; then env_set CORS_ORIGIN "https://$d"
        else env_set CORS_ORIGIN "https://$d:$HTTPS_PORT"; fi
    fi

    if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q "Status: active"; then
        ufw allow "$HTTP_PORT/tcp"  >/dev/null 2>&1 || true
        ufw allow "$HTTPS_PORT/tcp" >/dev/null 2>&1 || true
        ok "Opened $HTTP_PORT/$HTTPS_PORT in ufw (old rules left in place)"
    fi

    apply_stack; reload_proxy
    ok "Ports changed: HTTP $cur_http→$HTTP_PORT, HTTPS $cur_https→$HTTPS_PORT"
    [ "$HTTP_PORT" != "80" ] && warn_http01_port
    printf '  Panel: %s\n' "$(panel_url)"
}

# ── command: reset-admin ───────────────────────────────────────
# The only way back in when the panel password is lost: there is no self-service
# registration, and changing a password in the UI requires being logged in.
#
# Not a hole in the permission model — whoever has a shell here already reads
# ENCRYPTION_KEY from .env, and with it can forge a session token for anybody.
# This just makes the legitimate path easier than the illegitimate one.
cmd_reset_admin() {
    need_install
    ENV_FILE="$APP_DIR/.env"

    # `run`, not `exec`: a forgotten password is usually discovered while the
    # stack is down, and `run` brings up a throwaway container of its own.
    # `--no-deps` — editing users.json needs no other service.
    # `-T` — without it compose run insists on a TTY and fails whenever the
    # password arrives over a pipe (which is exactly how it arrives below).
    if [ "$RESET_GENERATE" = 1 ]; then
        local gen=(python -m app.reset_admin --generate)
        [ -n "$RESET_LOGIN" ] && gen+=(--login "$RESET_LOGIN")
        $DC run --rm --no-deps -T backend "${gen[@]}" < /dev/null \
            || die "Password reset failed."
        printf '\n  Sign in with that password and change it in the panel.\n'
        return 0
    fi

    [ -n "$RESET_LOGIN" ] || \
        RESET_LOGIN="$(ask "Superuser login (blank = the only one): " "")"
    local pw; pw="$(ask_secret "New password (min 10 characters): ")"
    [ -n "$pw" ] || die "Empty password — nothing was changed."

    local args=(python -m app.reset_admin --password-stdin)
    [ -n "$RESET_LOGIN" ] && args+=(--login "$RESET_LOGIN")
    # The password travels on stdin and never as an argument: argv is world-
    # readable in /proc/<pid>/cmdline and lands in the shell history.
    printf '%s' "$pw" | $DC run --rm --no-deps -T backend "${args[@]}" \
        || die "Password reset failed."
    printf '  Panel: %s\n' "$(panel_url)"
}

# ── dispatch ───────────────────────────────────────────────────
ENV_FILE="${APP_DIR:-}/.env"
case "$COMMAND" in
    install)       cmd_install ;;
    status)        cmd_status ;;
    check-updates) cmd_check_updates ;;
    update)        cmd_update ;;
    set-domain)    cmd_set_domain ;;
    set-ports)     cmd_set_ports ;;
    reset-admin)   cmd_reset_admin ;;
    *)             usage; exit 1 ;;
esac
