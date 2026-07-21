"""Wave-4 Plan D (E6) — Certwarden (centralised ACME) deploy + client generators.

Recon (R1-R3, CLAUDE.md §9f):
  - Images (ghcr): server `ghcr.io/gregtwallace/certwarden:latest`, client
    `ghcr.io/gregtwallace/certwarden-client:latest`. Server ports 4050 (HTTP
    UI/API), 4055 (HTTPS), 4060 (HTTP-01 challenge). Volume ./data (sqlite +
    config.yaml). Download API (headless issue to nodes):
      GET https://<srv>/certwarden/api/v1/download/certificates/<Name>  X-API-Key: <certApiKey>
      GET https://<srv>/certwarden/api/v1/download/privatekeys/<Name>   X-API-Key: <keyApiKey>
  - Client (R2): we use OUR OWN cron script (option a) — curl the two endpoints
    into /etc/ssl/... and `docker restart` the targets ourselves. We do NOT hand
    the CW client the docker socket (safer than the official client container).
  - R3: Certwarden is an ALTERNATIVE to per-node acme.sh; a node on Certwarden
    must not also run acme (enforced by the caller, not here).

Pure generators + a per-account single-server registry (certwarden.json). SSH
creds to deploy are transient (per-request). The node's download API-keys are
passed per client-install request and land only in the node's own cron script
(root-owned) — never stored in our DB.
"""
from __future__ import annotations

import shlex
import time
from typing import Optional

from app.services import storage

SERVER_IMAGE = "ghcr.io/gregtwallace/certwarden:latest"
_PLACEMENTS = ("panel", "dedicated")


# ── Registry ──────────────────────────────────────────────────────
def get_server(account_id: Optional[str] = None) -> dict:
    return storage.load_certwarden(account_id)


def set_server(
    placement: str, base_url: str, domain: str, account_id: Optional[str] = None
) -> dict:
    if placement not in _PLACEMENTS:
        raise ValueError(f"placement must be one of {_PLACEMENTS}")
    rec = {
        "placement": placement,
        "base_url": base_url.rstrip("/"),
        "domain": domain,
        "deployed_at": int(time.time()),
    }
    storage.save_certwarden(rec, account_id)
    return rec


def clear_server(account_id: Optional[str] = None) -> None:
    storage.save_certwarden({}, account_id)


# ── Server deploy ─────────────────────────────────────────────────
_SERVER_COMPOSE = """\
services:
  certwarden:
    image: __IMAGE__
    container_name: certwarden
    restart: unless-stopped
    ports:
      - "127.0.0.1:4050:4050"
      - "4055:4055"
      - "4060:4060"
    volumes:
      - ./data:/app/data
"""


def server_compose(image: str = SERVER_IMAGE) -> str:
    return _SERVER_COMPOSE.replace("__IMAGE__", image)


_SERVER_DEPLOY_TPL = r"""set -e
command -v docker >/dev/null 2>&1 || { echo "__NO_DOCKER__"; exit 1; }
mkdir -p /opt/certwarden/data
cat > /opt/certwarden/docker-compose.yml <<'CW_COMPOSE_EOF'
__COMPOSE__
CW_COMPOSE_EOF
cd /opt/certwarden
docker compose pull 2>/dev/null || docker-compose pull 2>/dev/null || true
docker compose up -d 2>/dev/null || docker-compose up -d 2>/dev/null
sleep 4
if docker ps --filter name=certwarden --filter status=running --format '{{.Names}}' | grep -q certwarden; then
  echo __CW_OK__
else
  echo __CW_FAIL__; exit 1
fi
"""


def server_deploy_script(image: str = SERVER_IMAGE) -> str:
    return _SERVER_DEPLOY_TPL.replace("__COMPOSE__", server_compose(image))


# ── Node client (our own pull-and-restart cron, option a) ────────
# Domains/URL/names are interpolated; the caller validates them (FQDN / hostname
# / name charset). API keys go through the SILENT channel and land only in the
# root-owned script on the node. `restart_containers` default remnanode+nginx.
_CLIENT_TPL = r"""set -e
SRV="__SERVER_URL__"
CERT_NAME="__CERT_NAME__"
KEY_NAME="__KEY_NAME__"
CERT_KEY="__CERT_APIKEY__"
KEY_KEY="__KEY_APIKEY__"
DOMAIN="__DOMAIN__"
RESTART="__RESTART__"
mkdir -p /opt/certwarden-client
cat > /opt/certwarden-client/pull-cert.sh <<PULL_EOF
#!/usr/bin/env bash
set -e
umask 077
FULL=\$(curl -fsS -H "X-API-Key: ${CERT_KEY}" "${SRV}/certwarden/api/v1/download/certificates/${CERT_NAME}")
KEY=\$(curl -fsS -H "X-API-Key: ${KEY_KEY}" "${SRV}/certwarden/api/v1/download/privatekeys/${KEY_NAME}")
if [ -z "\$FULL" ] || [ -z "\$KEY" ]; then echo "empty cert/key from Certwarden"; exit 1; fi
printf '%s\n' "\$FULL" > /etc/ssl/certs/${DOMAIN}_fullchain.pem
printf '%s\n' "\$KEY" > /etc/ssl/private/${DOMAIN}.key
chmod 644 /etc/ssl/certs/${DOMAIN}_fullchain.pem
chmod 600 /etc/ssl/private/${DOMAIN}.key
for c in ${RESTART}; do docker restart "\$c" 2>/dev/null || true; done
echo "certwarden-client: cert for ${DOMAIN} refreshed"
PULL_EOF
chmod 700 /opt/certwarden-client/pull-cert.sh
/opt/certwarden-client/pull-cert.sh
# daily renewal check
( crontab -l 2>/dev/null | grep -v certwarden-client/pull-cert.sh; \
  echo "17 3 * * * /opt/certwarden-client/pull-cert.sh >> /var/log/certwarden-client.log 2>&1" ) | crontab -
echo __CW_CLIENT_OK__
"""


def client_install_script(
    server_url: str,
    domain: str,
    cert_name: str,
    key_name: str,
    cert_apikey: str,
    key_apikey: str,
    restart_containers: Optional[list[str]] = None,
) -> str:
    restart = " ".join(
        shlex.quote(c) for c in (restart_containers or ["remnanode", "remnawave-nginx"])
    )
    return (
        _CLIENT_TPL.replace("__SERVER_URL__", server_url.rstrip("/"))
        .replace("__CERT_NAME__", cert_name)
        .replace("__KEY_NAME__", key_name)
        .replace("__CERT_APIKEY__", cert_apikey)
        .replace("__KEY_APIKEY__", key_apikey)
        .replace("__DOMAIN__", domain)
        .replace("__RESTART__", restart)
    )
