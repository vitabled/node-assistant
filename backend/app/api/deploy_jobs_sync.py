"""Remnawave → deploy-job cards synchronisation (dry-run + apply).

The "Деплой нод" cards are the operator's inventory; Remnawave is the
authoritative list of registered nodes. `POST /api/deploy-jobs/sync-remnawave`
reconciles them on demand:

  * **add** — every Remnawave node whose ``address`` matches no card ip gets a
    card with a STABLE synthetic taskId (``rw-<node uuid>``), so a second run
    never duplicates it. Credentials come ONLY from the server-side node store
    (`services/node_creds`); a node with no stored creds still gets a card, just
    without a password — secrets are never invented.
  * **remove** — only ``remnanode`` cards whose ip is absent from Remnawave.
    HAProxy relay cards (never registered in Remnawave) and cards whose node is
    still registered are NEVER removed.
  * **skipped** — private/service addresses (10/8, 172.16/12, 192.168/16,
    127/8) are listed with reason ``private-address`` instead of being added.

``dry_run=true`` returns the same report without writing anything.

Removal sticks across browsers thanks to tombstones (see ``api/deploy_jobs``):
the card is hidden from GET, its synthetic taskId can't be recreated by a stale
client's POST, and the client drops it from its localStorage cache.
"""
from __future__ import annotations

import ipaddress
import time
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.api import deploy_jobs
from app.api.downstream import downstream_exception
from app.models.settings import AppSettings
from app.services import node_creds, storage
from app.services.remnawave_client import RemnavaveClient, RemnavaveError

router = APIRouter(prefix="/api/deploy-jobs")

# Exact subnets from the contract: a node address inside one of them is not a
# real node to add (e.g. the docker-internal 172.30.0.1).
_PRIVATE_NETS = tuple(
    ipaddress.ip_network(net)
    for net in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "127.0.0.0/8")
)


class SyncRemnawaveRequest(BaseModel):
    dry_run: bool = False


def _client() -> RemnavaveClient:
    raw = storage.load_settings()
    cfg = AppSettings(**raw).remnawave
    if not cfg.panel_url or not cfg.api_token:
        raise HTTPException(400, "Remnawave не настроен")
    return RemnavaveClient(cfg.panel_url, cfg.api_token)


def _is_private(address: str) -> bool:
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    return any(ip in net for net in _PRIVATE_NETS)


def _default_new_ssh_port() -> int:
    try:
        port = int(AppSettings(**storage.load_settings()).deploy_defaults.new_ssh_port)
    except Exception:
        port = 2222
    return port if 1 <= port <= 65535 else 2222


def _country_code(node: dict[str, Any]) -> str:
    raw = node.get("country_code") or node.get("countryCode") or "XX"
    return str(raw)[:2] or "XX"


def _build_job(
    task_id: str, address: str, node: dict[str, Any], creds: dict[str, Any], port: int
) -> deploy_jobs.DeployJob:
    """A card for an existing node: status ``success`` (the node is already in
    Remnawave) and no live data; missing creds simply mean an empty password —
    rendering shows the yellow "no SSH access" marker, never an error."""
    domain = str(node.get("domain") or "").strip()
    saved_form: dict[str, Any] = {
        "mode": "remnanode",
        "ip": address,
        "domain": domain,
        "ssh_user": str(creds.get("ssh_user") or "root"),
        "ssh_password": str(creds.get("ssh_password") or ""),
        "ssh_key_ref": str(creds.get("ssh_key_ref") or ""),
        "current_ssh_port": port,
        "new_ssh_port": port,
        "change_ssh_port": False,
        "remnanode_port": int(creds.get("remnanode_port") or 2222),
        "xhttp_path": "",
        "open_ports": "",
        "country_code": _country_code(node),
        "install_vnstat": True,
        "install_hysteria2": True,
    }
    return deploy_jobs.DeployJob(
        taskId=task_id,
        domain=domain,
        ip=address,
        newSshPort=port,
        startedAt=time.time() * 1000,
        savedForm=saved_form,
        finalStatus="success",
    )


@router.post("/sync-remnawave")
async def sync_remnawave(body: SyncRemnawaveRequest) -> dict[str, Any]:
    client = _client()
    try:
        nodes = await client.list_nodes()
    except RemnavaveError as exc:
        raise downstream_exception(exc.status, exc.detail, "Панель Remnawave")
    except Exception as exc:
        raise HTTPException(502, str(exc))

    creds_by_ip = node_creds.creds_by_ip()
    default_port = _default_new_ssh_port()

    cards = deploy_jobs._jobs_for_response()
    cards_by_ip = {str(c.get("ip") or "").strip() for c in cards}
    node_addresses = {
        str(n.get("address") or "").strip() for n in nodes if str(n.get("address") or "").strip()
    }

    added: list[dict[str, str]] = []
    skipped: list[dict[str, str]] = []
    seen: set[str] = set()
    for node in nodes:
        address = str(node.get("address") or "").strip()
        uuid = str(node.get("uuid") or "").strip()
        if not address or address in seen:
            continue
        seen.add(address)
        if _is_private(address):
            skipped.append({"ip": address, "reason": "private-address"})
            continue
        if address in cards_by_ip:
            continue
        if not uuid:
            skipped.append({"ip": address, "reason": "no-uuid"})
            continue
        task_id = f"rw-{uuid}"
        creds = creds_by_ip.get(address, {})
        try:
            port = int(creds.get("new_ssh_port") or creds.get("ssh_port") or default_port)
        except (TypeError, ValueError):
            port = default_port
        if not 1 <= port <= 65535:
            port = default_port
        job = _build_job(task_id, address, node, creds, port)
        added.append({"taskId": task_id, "ip": address, "domain": job.domain})
        if not body.dry_run:
            # An explicit re-add is allowed to clear the tombstone: the node is in
            # Remnawave right now, so the card is wanted.
            deploy_jobs.clear_tombstone(task_id)
            deploy_jobs.upsert_server_job(job, respect_tombstone=False)

    removed: list[dict[str, str]] = []
    for card in cards:
        saved_form = card.get("savedForm") or {}
        if str(saved_form.get("mode") or "") != "remnanode":
            continue  # HAProxy/legacy cards are out of scope for removal
        ip = str(card.get("ip") or "").strip()
        if not ip or ip in node_addresses:
            continue
        removed.append(
            {"taskId": card["taskId"], "ip": ip, "domain": card.get("domain") or ""}
        )
        if not body.dry_run:
            deploy_jobs.delete_server_job(card["taskId"])

    cards_before = len(cards)
    return {
        "ok": True,
        "dry_run": body.dry_run,
        "added": added,
        "removed": removed,
        "skipped": skipped,
        "nodes_total": len(nodes),
        "cards_before": cards_before,
        "cards_after": cards_before + len(added) - len(removed),
    }
