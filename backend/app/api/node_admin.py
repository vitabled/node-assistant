"""Write-endpoints for node management (add existing node / full deploy / edit / delete).

Family router `/api/node-ops` — the management side of `/api/node/detect` and
`/api/node/step` (read-only probe + per-component ops live in `node_ops.py`).

  - POST   /api/node-ops/add-node   — register an ALREADY-DEPLOYED node in the
                                      Remnawave panel (POST /api/nodes).
  - POST   /api/node-ops/deploy     — full NodeFlow/HAProxy deploy over SSH as a
                                      streamed background Task; reuses the exact
                                      pipeline the rest of the app runs
                                      (`run_pipeline` / pipeline.step_* — the
                                      same builders `node_ops._reinstall` calls),
                                      so a component choice is just
                                      `skip_components` / `install_*` flags.
  - PATCH  /api/node-ops/{uuid}     — edit an existing node (rename, port,
                                      address, status, traffic, config profile).
  - DELETE /api/node-ops/{uuid}     — delete a node (confirm required) or
                                      soft-disable it (`soft: true`).

Secrets rule (same as everywhere in this app): SSH password / panel tokens are
per-request or resolved server-side from settings — they are NEVER logged into
the task stream and NEVER returned in a response. The deploy response carries
only `{task_id, task_type}`; the task log is written by the pipeline, which
never echoes creds.
"""
from __future__ import annotations

import asyncio
import re
from typing import Literal, Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query
from pydantic import BaseModel, Field, field_validator

from app.api.downstream import downstream_exception
from app.config import settings
from app.models.deploy import DeployRequest
from app.models.settings import AppSettings, PanelEntry
from app.services import job_runner, storage
from app.services.pipeline import run_pipeline
from app.services.remnawave_client import RemnavaveClient, RemnavaveError
from app.services.task_store import STEP_LABELS, task_store

router = APIRouter(prefix="/api/node-ops")

# Caps concurrent deploys at the API layer (own semaphore — mirrors deploy.py,
# which keeps its own for /api/deploy).
_deploy_sem = asyncio.Semaphore(settings.max_ssh_sessions)

_DOMAIN_RE = re.compile(
    r"^[A-Za-z0-9]([A-Za-z0-9\-]{0,61}[A-Za-z0-9])?"
    r"(\.[A-Za-z0-9]([A-Za-z0-9\-]{0,61}[A-Za-z0-9])?)*\.[A-Za-z]{2,}$"
)
_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)

NodeStatus = Literal["active", "disabled"]


def _validate_ip(v: str) -> None:
    if not re.fullmatch(r"^(\d{1,3}\.){3}\d{1,3}$", v) or any(
        int(p) > 255 for p in v.split(".")
    ):
        raise ValueError("Invalid IPv4 address")


def _validate_ip_or_domain(v: str) -> str:
    """Node address may be an IPv4 or a hostname (both are shell-safe here —
    the value goes to the Remnawave API, never into a shell command)."""
    v = v.strip()
    if not v:
        raise ValueError("address is required")
    if re.fullmatch(r"^(\d{1,3}\.){3}\d{1,3}$", v):
        _validate_ip(v)
        return v
    if not _DOMAIN_RE.fullmatch(v):
        raise ValueError("Invalid address (IPv4 or domain expected)")
    return v


def _validate_uuid(v: str) -> str:
    """Pydantic-field validator: ValueError → automatic 422."""
    if not _UUID_RE.fullmatch(v):
        raise ValueError("Invalid UUID format")
    return v


def _check_uuid(node_uuid: str) -> None:
    """Endpoint-level guard (a ValueError escaping a handler would be a 500)."""
    if not _UUID_RE.fullmatch(node_uuid):
        raise HTTPException(422, "Invalid UUID format")


# ── Panel resolution ──────────────────────────────────────────────────────────
# Remnawave creds live in the per-account settings registry (Wave-5 Plan K),
# never in request bodies. `panel_id` selects an entry; empty → active panel.

def _resolve_panel(panel_id: str = "") -> PanelEntry:
    reg = AppSettings(**storage.load_settings()).remnawave_registry
    if panel_id:
        entry = next((p for p in reg.panels if p.id == panel_id), None)
        if entry is None:
            raise HTTPException(404, "Панель не найдена в реестре")
    else:
        entry = (
            next((p for p in reg.panels if p.id == reg.active_panel_id), None)
            or (reg.panels[0] if reg.panels else None)
        )
    if entry is None or not entry.panel_url or not entry.api_token:
        raise HTTPException(400, "Remnawave не настроен")
    return entry


def _client(panel_id: str = "") -> RemnavaveClient:
    entry = _resolve_panel(panel_id)
    return RemnavaveClient(entry.panel_url, entry.api_token)


def _remnawave_error(exc: RemnavaveError) -> HTTPException:
    """Map a panel error to HTTP. 404 → «нода не найдена»; 401/403 → 502
    (see downstream.py — a panel 401 must not kill the operator's session)."""
    if exc.status == 404:
        return HTTPException(404, "Нода не найдена в Remnawave")
    return downstream_exception(exc.status, exc.detail, "Панель Remnawave")


async def _apply_status(client: RemnavaveClient, node_uuid: str, status: NodeStatus) -> None:
    """enable/disable action endpoint (idempotent server-side)."""
    if status == "disabled":
        await client.disable_node(node_uuid)
    else:
        await client.enable_node(node_uuid)


# ── POST /api/node-ops/add-node ───────────────────────────────────────────────

class AddNodeRequest(BaseModel):
    """Register an EXISTING (already-deployed) node in Remnawave.

    Only name/address/port are required — Remnawave also demands a config
    profile + active inbounds, so when `config_profile_uuid` is omitted the
    handler picks the panel's FIRST config profile (and its inbounds), which is
    the sensible default for the «add existing server» flow.
    """
    name: str = Field(..., min_length=3, max_length=30)
    address: str
    port: int = Field(default=62050, ge=1, le=65535)
    # Registry panel id (Wave-5 Plan K); empty → the active panel.
    panel_id: str = ""
    status: Optional[NodeStatus] = None
    country_code: str = Field(default="XX", max_length=2)
    config_profile_uuid: Optional[str] = None
    active_inbounds: list[str] = Field(default_factory=list)
    plugin_uuid: Optional[str] = None

    @field_validator("address")
    @classmethod
    def _address(cls, v: str) -> str:
        return _validate_ip_or_domain(v)

    @field_validator("config_profile_uuid")
    @classmethod
    def _profile_uuid(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        return _validate_uuid(v)

    @field_validator("active_inbounds")
    @classmethod
    def _inbound_uuids(cls, v: list[str]) -> list[str]:
        return [_validate_uuid(x) for x in v]

    @field_validator("plugin_uuid")
    @classmethod
    def _plugin_uuid(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        return _validate_uuid(v)


async def _default_profile(client: RemnavaveClient) -> tuple[str, list[str]]:
    """First config profile + its inbound uuids (the add-existing-node default)."""
    profiles = await client.list_config_profiles()
    profile = next((p for p in profiles if p.get("uuid")), None)
    if profile is None:
        raise HTTPException(
            400,
            "В панели нет config-профилей — укажите config_profile_uuid и active_inbounds "
            "или создайте профиль в Remnawave.",
        )
    inbounds = profile.get("inbounds") or []
    inbound_uuids = [
        ib["uuid"] for ib in inbounds
        if isinstance(ib, dict) and ib.get("uuid")
    ]
    return profile["uuid"], inbound_uuids


@router.post("/add-node", status_code=201)
async def add_node(req: AddNodeRequest):
    """Register an existing node in the Remnawave panel (POST /api/nodes).

    With `status: "disabled"` the node is created and then immediately
    disabled, so it does not serve traffic until the operator enables it.
    """
    client = _client(req.panel_id)
    profile_uuid = req.config_profile_uuid
    inbounds = req.active_inbounds
    if profile_uuid is None:
        try:
            profile_uuid, inbounds = await _default_profile(client)
        except RemnavaveError as exc:
            raise _remnawave_error(exc)
    try:
        node = await client.create_node(
            name=req.name,
            address=req.address,
            port=req.port,
            config_profile_uuid=profile_uuid,
            active_inbounds=inbounds,
            country_code=req.country_code,
            active_plugin_uuid=req.plugin_uuid,
        )
        if req.status == "disabled":
            await client.disable_node(node["uuid"])
    except RemnavaveError as exc:
        raise _remnawave_error(exc)
    except Exception as exc:
        raise HTTPException(502, str(exc))
    return node


# ── POST /api/node-ops/deploy ─────────────────────────────────────────────────

async def _run_deploy(req: DeployRequest, task_id: str) -> None:
    """Local runner for the queued deploy. Mirrors deploy.py's safe wrapper:
    run_pipeline owns the task lifecycle (steps/logs/finish), we only hold the
    semaphore. Never logs creds — the pipeline logs hosts/domains only."""
    task = task_store.get(task_id)
    if not task:
        return
    try:
        async with _deploy_sem:
            await run_pipeline(req, task)
    except Exception:
        pass  # status already set to FAILED inside run_pipeline


@router.post("/deploy")
async def node_deploy(req: DeployRequest, background_tasks: BackgroundTasks):
    """Full node deploy (NodeFlow/HAProxy) over SSH as a streamed background
    Task. Reuses the exact pipeline builders `node_ops._reinstall` delegates to
    (`pipeline.step_*` / `run_pipeline`) — component selection is the standard
    `skip_components` / `install_*` / `mode` set of DeployRequest.

    Creds are per-request (SshCreds) and are never echoed into the task stream
    nor returned: the response is only `{task_id, task_type}`.
    """
    offloading = job_runner.offload_available("deploy")
    busy = (
        task_store.stats().get("queued", 0) >= settings.max_ssh_sessions
        if offloading else _deploy_sem._value == 0
    )
    if busy:
        raise HTTPException(
            status_code=503,
            detail=f"Server busy — max {settings.max_ssh_sessions} concurrent deploys reached",
        )
    task = task_store.create(total_steps=len(STEP_LABELS))
    task_id = task.task_id

    # Same payload shape + task type as /api/deploy, so a live deploy-worker
    # (which has deploy.py's handler registered) picks it up unchanged.
    if job_runner.offload(task, "deploy", req.model_dump(mode="json")):
        return {"task_id": task_id, "task_type": "deploy"}

    background_tasks.add_task(_run_deploy, req, task_id)
    return {"task_id": task_id, "task_type": "deploy"}


# ── PATCH /api/node-ops/{node_uuid} ───────────────────────────────────────────

class UpdateNodeRequest(BaseModel):
    """Optional fields; only provided ones are sent to PATCH /api/nodes (uuid
    travels in the body, per UpdateNodeRequestDto). `status` is applied via the
    enable/disable action endpoint after the patch."""
    name: Optional[str] = Field(default=None, min_length=3, max_length=30)
    address: Optional[str] = None
    port: Optional[int] = Field(default=None, ge=1, le=65535)
    status: Optional[NodeStatus] = None
    country_code: Optional[str] = Field(default=None, max_length=2)
    is_traffic_tracking_active: Optional[bool] = None
    traffic_limit_bytes: Optional[int] = Field(default=None, ge=0)
    traffic_reset_day: Optional[int] = Field(default=None, ge=1, le=31)
    config_profile_uuid: Optional[str] = None
    active_inbounds: Optional[list[str]] = None
    plugin_uuid: Optional[str] = None

    @field_validator("address")
    @classmethod
    def _address(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        return _validate_ip_or_domain(v)

    @field_validator("config_profile_uuid")
    @classmethod
    def _profile_uuid(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        return _validate_uuid(v)

    @field_validator("active_inbounds")
    @classmethod
    def _inbound_uuids(cls, v: Optional[list[str]]) -> Optional[list[str]]:
        if v is None:
            return v
        return [_validate_uuid(x) for x in v]

    @field_validator("plugin_uuid")
    @classmethod
    def _plugin_uuid(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        return _validate_uuid(v)


@router.patch("/{node_uuid}")
async def update_node(node_uuid: str, req: UpdateNodeRequest):
    """Edit an existing Remnawave node: rename, address/port, status
    (enable/disable), traffic tracking or config-profile reassignment."""
    _check_uuid(node_uuid)
    if not req.model_dump(exclude_none=True):
        raise HTTPException(400, "Нет полей для обновления — передайте хотя бы одно")

    client = _client()
    body: dict = {}
    if req.name is not None:
        body["name"] = req.name
    if req.address is not None:
        body["address"] = req.address
    if req.port is not None:
        body["port"] = req.port
    if req.country_code is not None:
        body["countryCode"] = req.country_code.upper()[:2]
    if req.is_traffic_tracking_active is not None:
        body["isTrafficTrackingActive"] = req.is_traffic_tracking_active
    if req.traffic_limit_bytes is not None:
        body["trafficLimitBytes"] = req.traffic_limit_bytes
    if req.traffic_reset_day is not None:
        body["trafficResetDay"] = req.traffic_reset_day
    if req.plugin_uuid is not None:
        body["activePluginUuid"] = req.plugin_uuid
    if req.config_profile_uuid is not None or req.active_inbounds is not None:
        profile = {
            "activeConfigProfileUuid": req.config_profile_uuid,
            "activeInbounds": req.active_inbounds or [],
        }
        if req.config_profile_uuid is None:
            # PATCH with only inbounds — keep the current profile; the panel
            # rejects an empty activeConfigProfileUuid.
            try:
                current = await client.list_nodes()
                cur = next((n for n in current if n.get("uuid") == node_uuid), None)
            except RemnavaveError as exc:
                raise _remnawave_error(exc)
            if cur is None:
                raise HTTPException(404, "Нода не найдена в Remnawave")
            profile["activeConfigProfileUuid"] = (
                (cur.get("configProfile") or {}).get("activeConfigProfileUuid")
                or ""
            )
        body["configProfile"] = profile

    try:
        node = await client.update_node(node_uuid, body)
        if req.status is not None:
            await _apply_status(client, node_uuid, req.status)
    except RemnavaveError as exc:
        raise _remnawave_error(exc)
    except Exception as exc:
        raise HTTPException(502, str(exc))
    return {"ok": True, "node": node}


# ── DELETE /api/node-ops/{node_uuid} ──────────────────────────────────────────

@router.delete("/{node_uuid}")
async def delete_node(
    node_uuid: str,
    confirm: bool = Query(default=False),
    soft: bool = Query(default=False),
):
    """Delete a node from Remnawave (DELETE /api/nodes/{uuid}), or disable it
    when `soft=true`. `confirm=true` is required — a bare DELETE is refused
    (guard against accidental clicks; Remnawave has no trash bin, so `soft`
    is the reversible variant)."""
    _check_uuid(node_uuid)
    if not confirm:
        raise HTTPException(
            400,
            "Подтвердите удаление ноды: передайте ?confirm=true "
            "(или ?confirm=true&soft=true, чтобы отключить, а не удалять).",
        )
    client = _client()
    try:
        if soft:
            await client.disable_node(node_uuid)
            return {"ok": True, "soft": True, "message": "Нода отключена"}
        await client.delete_node(node_uuid)
    except RemnavaveError as exc:
        raise _remnawave_error(exc)
    except Exception as exc:
        raise HTTPException(502, str(exc))
    return {"ok": True}
