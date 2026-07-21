"""Wave-4 Plan E (E7) — domain-replacement script generators (node + panel).

Pure generators (no I/O) so they're unit-testable. The heavy lifting is a
scoped `sed` that swaps the OLD FQDN for the NEW one across the relevant config
files, plus re-issuing the cert (reusing `pipeline.build_ssl_script`) and a
compose restart. The FQDN is replaced literally (dots escaped) so native nginx
vars (`$http_upgrade`, …) and unrelated content are untouched. Idempotent: a
second pass finds no OLD occurrences and is a no-op.

Domains reaching these functions are FQDN-validated by the request models, so
`.replace()` interpolation is shell-safe (hostname charset only).
"""
from __future__ import annotations

import re

# FQDN allowlist (mirrors certs.py / models) — hostname chars only.
DOMAIN_RE = re.compile(
    r"^[A-Za-z0-9]([A-Za-z0-9\-]{0,61}[A-Za-z0-9])?"
    r"(\.[A-Za-z0-9]([A-Za-z0-9\-]{0,61}[A-Za-z0-9])?)*\.[A-Za-z]{2,}$"
)


def is_fqdn(v: str) -> bool:
    return bool(DOMAIN_RE.fullmatch(v or ""))


# ── Node ──────────────────────────────────────────────────────────
# Rewrites /opt/remnanode/{docker-compose.yml,nginx.conf}: server_name + the
# nginx cert mount paths (which embed the domain). Re-links the acme cert bridge
# for the new domain and recreates the stack. OLD may be empty → detected from
# nginx.conf's server_name. Plain (non-f) template — `{{.Names}}` is docker Go
# template syntax, not a format placeholder.
_NODE_REPLACE_TPL = r"""set -e
cd /opt/remnanode 2>/dev/null || { echo "__NO_REMNANODE__"; exit 1; }
NEW="__NEW_DOMAIN__"
OLD="__OLD_DOMAIN__"
if [ -z "$OLD" ]; then
  OLD=$(grep -hoE 'server_name[[:space:]]+[^;]+' nginx.conf 2>/dev/null | awk '{print $2}' | head -1)
  echo "detected old domain: $OLD"
fi
if [ -z "$OLD" ] || [ "$OLD" = "$NEW" ]; then echo "__NO_OLD_DOMAIN__"; exit 1; fi
OLD_ESC=$(printf '%s' "$OLD" | sed 's/[.]/\./g')
for f in docker-compose.yml nginx.conf; do
  if [ -f "$f" ]; then
    sed -i "s/${OLD_ESC}/${NEW}/g" "$f" && echo "updated $f"
  fi
done
mkdir -p /etc/letsencrypt/live/"$NEW"
ln -sf /etc/ssl/certs/"${NEW}"_fullchain.pem /etc/letsencrypt/live/"$NEW"/fullchain.pem
ln -sf /etc/ssl/private/"${NEW}".key        /etc/letsencrypt/live/"$NEW"/privkey.pem
echo "cert bridge for $NEW ready"
docker compose down 2>/dev/null || docker-compose down 2>/dev/null || true
docker compose up -d 2>/dev/null || docker-compose up -d 2>/dev/null
sleep 3
if docker ps --filter name=remnanode --filter status=running --format '{{.Names}}' | grep -q remnanode; then
  echo __NODE_OK__
else
  echo __NODE_FAIL__; exit 1
fi
"""


def node_replace_script(old_domain: str, new_domain: str) -> str:
    return _NODE_REPLACE_TPL.replace("__NEW_DOMAIN__", new_domain).replace(
        "__OLD_DOMAIN__", old_domain or ""
    )


# ── Panel ─────────────────────────────────────────────────────────
# Swaps the panel domain and/or sub domain across /opt/remnawave/{.env,
# docker-compose.yml,Caddyfile,caddy/Caddyfile,nginx.conf} (FRONT_END_DOMAIN /
# SUB_PUBLIC_DOMAIN live in .env; the reverse-proxy config carries them too).
# Each pair is independent (either may be empty). Restarts the panel stack.
_PANEL_REPLACE_TPL = r"""set -e
cd /opt/remnawave 2>/dev/null || { echo "__NO_PANEL__"; exit 1; }
replace_in() {
  OLD="$1"; NEW="$2"
  [ -z "$OLD" ] && return 0
  [ -z "$NEW" ] && return 0
  [ "$OLD" = "$NEW" ] && return 0
  OLD_ESC=$(printf '%s' "$OLD" | sed 's/[.]/\./g')
  for f in .env docker-compose.yml Caddyfile caddy/Caddyfile nginx.conf; do
    if [ -f "$f" ]; then
      sed -i "s/${OLD_ESC}/${NEW}/g" "$f" && echo "  $f: $OLD -> $NEW"
    fi
  done
}
replace_in "__OLD_PANEL__" "__NEW_PANEL__"
replace_in "__OLD_SUB__" "__NEW_SUB__"
docker compose down 2>/dev/null || docker-compose down 2>/dev/null || true
docker compose up -d 2>/dev/null || docker-compose up -d 2>/dev/null
sleep 4
if docker ps --filter name=remnawave-backend --filter status=running --format '{{.Names}}' | grep -q remnawave-backend; then
  echo __PANEL_OK__
else
  echo __PANEL_FAIL__; exit 1
fi
"""


def panel_replace_script(
    old_panel: str, new_panel: str, old_sub: str, new_sub: str
) -> str:
    return (
        _PANEL_REPLACE_TPL.replace("__OLD_PANEL__", old_panel or "")
        .replace("__NEW_PANEL__", new_panel or "")
        .replace("__OLD_SUB__", old_sub or "")
        .replace("__NEW_SUB__", new_sub or "")
    )
