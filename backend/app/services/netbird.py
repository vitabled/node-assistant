"""Wave-4 Plan F (E8) — self-hosted Netbird control-plane deploy + agent join.

Recon (R1-R3, CLAUDE.md §9g):
  - Self-hosted stack via the official `getting-started.sh` (management+signal+
    relay+coturn with built-in Dex — no external IdP; + dashboard + reverse-proxy
    + traefik). Env NETBIRD_DOMAIN + NETBIRD_LETSENCRYPT_EMAIL. Needs a public
    FQDN with an A record; TLS auto via Traefik/LE. Ports TCP 80/443, UDP 3478.
  - Setup-key (R1): POST https://<domain>/api/setup-keys, header
    `Authorization: Token <PAT>`, body reusable → response field `key`. The PAT is
    a service-user token created in the dashboard → stored Fernet-encrypted.
  - Agent (R2): install.sh | sh, then `netbird up --setup-key <K>
    --management-url https://<domain>:443 --disable-client-routes
    --disable-server-routes` (R3 — the flags keep it from hijacking the default
    route / SSH, the WARP `Table=off` lesson). Overlay IP:
    `netbird status --json | jq -r '.netbirdIp'` (100.64.0.0/10).

Pure generators + a per-account single-control-plane registry (netbird.json) with
the PAT in a Fernet vault (key = SHA-256 of settings.encryption_key), mirroring
mcp_server / rules_store. SSH creds to deploy/join are transient (per-request).
"""
from __future__ import annotations

import base64
import hashlib
import logging
import time
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken

from app.config import settings
from app.services import storage

log = logging.getLogger(__name__)


# ── Fernet vault (module-scoped, like mcp/rules) ──────────────────
def _fernet() -> Fernet:
    digest = hashlib.sha256(settings.encryption_key.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def _encrypt(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode()).decode()


def _decrypt(token_enc: str) -> Optional[str]:
    if not token_enc:
        return None
    try:
        return _fernet().decrypt(token_enc.encode()).decode()
    except (InvalidToken, Exception):
        log.warning("netbird.pat_decrypt_failed (rekeyed or corrupt vault entry)")
        return None


# ── Registry ──────────────────────────────────────────────────────
def get_control_plane(account_id: Optional[str] = None) -> dict:
    return storage.load_netbird(account_id)


def public_control_plane(account_id: Optional[str] = None) -> dict:
    """Registry WITHOUT the secret — safe to return to the client."""
    rec = storage.load_netbird(account_id)
    if not rec:
        return {}
    return {
        "domain": rec.get("domain", ""),
        "management_url": rec.get("management_url", ""),
        "has_pat": bool(rec.get("pat_enc")),
        "deployed_at": rec.get("deployed_at", 0),
    }


def set_control_plane(domain: str, account_id: Optional[str] = None) -> dict:
    """Register the control plane (keeps any existing encrypted PAT)."""
    rec = storage.load_netbird(account_id) or {}
    rec.update({
        "domain": domain,
        "management_url": f"https://{domain}",
        "deployed_at": int(time.time()),
    })
    rec.setdefault("pat_enc", "")
    storage.save_netbird(rec, account_id)
    return rec


def set_pat(pat: str, account_id: Optional[str] = None) -> None:
    """Store the service-user PAT encrypted at rest. Never logged/returned."""
    rec = storage.load_netbird(account_id) or {}
    rec["pat_enc"] = _encrypt(pat)
    storage.save_netbird(rec, account_id)


def get_pat(account_id: Optional[str] = None) -> Optional[str]:
    return _decrypt(storage.load_netbird(account_id).get("pat_enc", ""))


def clear_control_plane(account_id: Optional[str] = None) -> None:
    storage.save_netbird({}, account_id)


# ── Setup-key API payload (pure) ──────────────────────────────────
def setup_key_payload(name: str = "node-assistant") -> dict:
    return {
        "name": name,
        "type": "reusable",
        "expires_in": 31536000,   # 1 year
        "auto_groups": [],
        "usage_limit": 0,         # unlimited uses
    }


# ── Control-plane deploy script (getting-started.sh) ──────────────
_CP_DEPLOY_TPL = r"""set -e
command -v docker >/dev/null 2>&1 || { echo "__NO_DOCKER__"; exit 1; }
command -v jq >/dev/null 2>&1 || { apt-get update -y >/dev/null 2>&1 && apt-get install -y jq >/dev/null 2>&1 || true; }
mkdir -p /opt/netbird && cd /opt/netbird
export NETBIRD_DOMAIN="__DOMAIN__"
export NETBIRD_LETSENCRYPT_EMAIL="__EMAIL__"
curl -fsSL https://github.com/netbirdio/netbird/releases/latest/download/getting-started.sh -o getting-started.sh
chmod +x getting-started.sh
NETBIRD_DOMAIN="$NETBIRD_DOMAIN" NETBIRD_LETSENCRYPT_EMAIL="$NETBIRD_LETSENCRYPT_EMAIL" bash getting-started.sh
sleep 5
if docker ps --format '{{.Names}}' | grep -q dashboard; then
  echo __NB_OK__
else
  echo __NB_FAIL__; exit 1
fi
"""


def control_plane_deploy_script(domain: str, email: str) -> str:
    return _CP_DEPLOY_TPL.replace("__DOMAIN__", domain).replace("__EMAIL__", email)


# ── Node agent join script ────────────────────────────────────────
# R3: --disable-client-routes --disable-server-routes so the overlay never
# hijacks the default route (would drop SSH — the WARP Table=off lesson).
_AGENT_TPL = r"""set -e
MGMT="__MGMT_URL__"
KEY="__SETUP_KEY__"
command -v netbird >/dev/null 2>&1 || curl -fsSL https://pkgs.netbird.io/install.sh | sh
netbird up --setup-key "$KEY" --management-url "$MGMT" --disable-client-routes --disable-server-routes
sleep 3
IP=$(netbird status --json 2>/dev/null | jq -r '.netbirdIp // empty')
if [ -n "$IP" ]; then
  echo "__NB_PEER_IP__=$IP"
  echo __NB_AGENT_OK__
else
  echo __NB_AGENT_FAIL__; exit 1
fi
"""


def agent_install_script(management_url: str, setup_key: str) -> str:
    return _AGENT_TPL.replace("__MGMT_URL__", management_url.rstrip("/")).replace(
        "__SETUP_KEY__", setup_key
    )


def parse_peer_ip(out: str) -> Optional[str]:
    """Extract the overlay IP the agent reported (100.64.0.0/10)."""
    for line in (out or "").splitlines():
        line = line.strip()
        if line.startswith("__NB_PEER_IP__="):
            return line.split("=", 1)[1].strip() or None
    return None
