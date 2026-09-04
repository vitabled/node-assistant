"""API for the per-account Fail2Ban list and per-request node sync."""
from __future__ import annotations

import ipaddress
import re

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator

from app.models.ssh_creds import SshCreds
from app.services import f2b_list

router = APIRouter(prefix="/api/f2b-list")
_JAIL_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")


@router.get("")
async def get_list():
    return {"entries": f2b_list.load()}


class PutBody(BaseModel):
    entries: list[str] = []


@router.put("")
async def put_list(body: PutBody):
    try:
        saved = f2b_list.save(body.entries)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    return {"ok": True, "entries": saved, "count": len(saved)}


class NodeRequest(SshCreds):
    """SSH credentials supplied by the browser for one non-persistent action."""
    ssh_port: int = Field(default=22, ge=1, le=65535)
    jails: list[str] = Field(default_factory=lambda: ["sshd"], max_length=20)

    @field_validator("ip")
    @classmethod
    def validate_ip(cls, value: str) -> str:
        try:
            return str(ipaddress.ip_address(value))
        except ValueError:
            raise ValueError("Некорректный IP-адрес ноды") from None

    @field_validator("ssh_user")
    @classmethod
    def validate_ssh_user(cls, value: str) -> str:
        value = value.strip()
        if not value or len(value) > 64:
            raise ValueError("Некорректный SSH-пользователь")
        return value

    @field_validator("jails")
    @classmethod
    def validate_jails(cls, value: list[str]) -> list[str]:
        result: list[str] = []
        for jail in value:
            jail = jail.strip()
            if not _JAIL_RE.fullmatch(jail):
                raise ValueError("Некорректное имя jail fail2ban")
            if jail not in result:
                result.append(jail)
        if not result:
            raise ValueError("Нужен хотя бы один jail fail2ban")
        return result


class SyncNodeRequest(NodeRequest):
    pull: bool = True
    push: bool = True


class NodesSyncRequest(BaseModel):
    nodes: list[SyncNodeRequest] = Field(min_length=1, max_length=50)
    merge_collected: bool = False


def _node_error(req: NodeRequest, exc: Exception) -> HTTPException:
    return HTTPException(
        status_code=502,
        detail=(
            f"Не удалось выполнить Fail2Ban-синхронизацию с нодой "
            f"{req.ip}:{req.ssh_port}: {str(exc)[:200]}"
        ),
    )


@router.post("/node/collect")
async def collect_node(body: NodeRequest):
    """Read active node bans only; central entries are never changed here."""
    try:
        return {"ips": await f2b_list.collect_node(body, body.jails)}
    except Exception as exc:
        raise _node_error(body, exc) from None


@router.post("/node/push")
async def push_node(body: NodeRequest):
    """Apply the authenticated account's central list to one node."""
    try:
        result = await f2b_list.apply_node(body, f2b_list.load(), body.jails)
    except Exception as exc:
        raise _node_error(body, exc) from None
    return {key: value for key, value in result.items() if key != "skipped" or value}


@router.post("/nodes/sync")
async def sync_nodes(body: NodesSyncRequest):
    """Optionally pull from each node, merge, then push the final central list."""
    central = f2b_list.load()
    results = [{"ip": node.ip, "ok": True} for node in body.nodes]
    changed = False

    for node, result in zip(body.nodes, results):
        if not node.pull:
            continue
        try:
            collected = await f2b_list.collect_node(node, node.jails)
            result["collected"] = len(collected)
            if body.merge_collected:
                merged = central + [ip for ip in collected if ip not in central]
                if len(merged) != len(central):
                    central = merged
                    changed = True
        except Exception as exc:
            result.update(ok=False, error=str(exc)[:200])

    if changed:
        try:
            central = f2b_list.save(central)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None

    for node, result in zip(body.nodes, results):
        if not node.push:
            continue
        try:
            applied = await f2b_list.apply_node(node, central, node.jails)
            result["applied"] = applied["applied"]
        except Exception as exc:
            result.update(ok=False, error=str(exc)[:200])
    return {"results": results, "central_count": len(central)}
