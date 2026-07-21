import uuid as _uuid
from typing import Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from app.services import storage
from app.models.settings import (
    AppSettings,
    RemnavaveConfig,
    PanelEntry,
    DeployDefaults,
    OptimizationSettings,
    XrayCheckerConfig,
    AppearanceConfig,
    TemplateCreate,
    TemplateUpdate,
)
from app.services.remnawave_client import RemnavaveClient, RemnavaveError

router = APIRouter(prefix="/api")


# ── App settings ──────────────────────────────────────────────────────────────

@router.get("/settings")
async def get_settings():
    raw = storage.load_settings()
    return AppSettings(**raw).model_dump()


@router.post("/settings/remnawave")
async def save_remnawave_settings(body: RemnavaveConfig):
    """Backward-compat: edits the ACTIVE panel (Wave-5 Plan K). The registry is the
    source of truth; `remnawave` is a computed view (AppSettings validator)."""
    raw = storage.load_settings()
    settings = AppSettings(**raw)
    reg = settings.remnawave_registry
    if not reg.panels:
        entry = PanelEntry(name="Основная", kind="custom", panel_url=body.panel_url, api_token=body.api_token,
                           default_internal_squad_ids=body.default_internal_squad_ids,
                           default_external_squad_ids=body.default_external_squad_ids)
        reg.panels = [entry]
        reg.active_panel_id = entry.id
    else:
        active = next((p for p in reg.panels if p.id == reg.active_panel_id), reg.panels[0])
        active.panel_url = body.panel_url
        active.api_token = body.api_token
        active.default_internal_squad_ids = body.default_internal_squad_ids
        active.default_external_squad_ids = body.default_external_squad_ids
    storage.save_settings(settings.model_dump())
    return {"ok": True}


# ── Panel registry (Wave-5 Plan K) ────────────────────────────────────────────

class PanelEntryBody(BaseModel):
    name: str = "Основная"
    kind: str = "custom"
    panel_url: str = ""
    api_token: str = ""
    default_internal_squad_ids: list[str] = []
    default_external_squad_ids: list[str] = []


@router.get("/settings/remnawave/panels")
async def list_panels():
    reg = AppSettings(**storage.load_settings()).remnawave_registry
    return {"panels": [p.model_dump() for p in reg.panels], "active_panel_id": reg.active_panel_id}


@router.post("/settings/remnawave/panels", status_code=201)
async def create_panel(body: PanelEntryBody):
    settings = AppSettings(**storage.load_settings())
    reg = settings.remnawave_registry
    entry = PanelEntry(**body.model_dump())
    reg.panels.append(entry)
    if not reg.active_panel_id:
        reg.active_panel_id = entry.id
    storage.save_settings(settings.model_dump())
    return entry.model_dump()


@router.put("/settings/remnawave/panels/{panel_id}")
async def update_panel(panel_id: str, body: PanelEntryBody):
    settings = AppSettings(**storage.load_settings())
    reg = settings.remnawave_registry
    entry = next((p for p in reg.panels if p.id == panel_id), None)
    if entry is None:
        raise HTTPException(404, "Панель не найдена")
    for k, v in body.model_dump().items():
        setattr(entry, k, v)
    storage.save_settings(settings.model_dump())
    return entry.model_dump()


@router.delete("/settings/remnawave/panels/{panel_id}")
async def delete_panel(panel_id: str):
    settings = AppSettings(**storage.load_settings())
    reg = settings.remnawave_registry
    before = len(reg.panels)
    reg.panels = [p for p in reg.panels if p.id != panel_id]
    if len(reg.panels) == before:
        raise HTTPException(404, "Панель не найдена")
    if reg.active_panel_id == panel_id:
        reg.active_panel_id = reg.panels[0].id if reg.panels else ""
    storage.save_settings(settings.model_dump())
    return {"ok": True, "active_panel_id": reg.active_panel_id}


@router.post("/settings/remnawave/panels/{panel_id}/activate")
async def activate_panel(panel_id: str):
    settings = AppSettings(**storage.load_settings())
    reg = settings.remnawave_registry
    if not any(p.id == panel_id for p in reg.panels):
        raise HTTPException(404, "Панель не найдена")
    reg.active_panel_id = panel_id
    storage.save_settings(settings.model_dump())
    return {"ok": True, "active_panel_id": panel_id}


@router.post("/settings/optimization")
async def save_optimization_settings(body: OptimizationSettings):
    raw = storage.load_settings()
    settings = AppSettings(**raw)
    settings.optimization = body
    storage.save_settings(settings.model_dump())
    return {"ok": True}


@router.post("/settings/appearance")
async def save_appearance(body: AppearanceConfig):
    """Persist the account's UI appearance prefs (skin/mode/accent/density/motion).
    No secrets → plain per-account settings.json. Invalid enum values → 422."""
    raw = storage.load_settings()
    settings = AppSettings(**raw)
    settings.appearance = body
    storage.save_settings(settings.model_dump())
    return {"ok": True}


@router.post("/settings/deploy-defaults")
async def save_deploy_defaults(body: DeployDefaults):
    raw = storage.load_settings()
    settings = AppSettings(**raw)
    settings.deploy_defaults = body
    storage.save_settings(settings.model_dump())
    return {"ok": True}


@router.post("/settings/xray-checker")
async def save_xray_checker(body: XrayCheckerConfig):
    """Persist xray-checker config. If it's enabled + has a subscription URL,
    (re)start the container so new settings take effect immediately."""
    from app.services import xray_checker as xc
    raw = storage.load_settings()
    settings = AppSettings(**raw)
    settings.xray_checker = body
    storage.save_settings(settings.model_dump())
    # Settings are persisted regardless. Starting the container is best-effort —
    # if Docker isn't available we still return 200 with a warning (not an error),
    # so the UI shows the saved state plus a hint rather than a failure.
    if body.enabled and body.subscription_url.strip():
        try:
            await xc.start(body)
        except Exception as exc:
            return {"ok": True, "warning": f"Настройки сохранены, но чекер не запущен: {exc}"}
    return {"ok": True}


class RemnawaveCheckBody(BaseModel):
    # Values typed into the form (unsaved). When present they're tested directly,
    # so «Проверить соединение» validates what the operator entered — not the
    # last-saved settings. Empty → fall back to the stored config.
    panel_url: Optional[str] = None
    api_token: Optional[str] = None


@router.post("/settings/remnawave/check")
async def check_remnawave(body: Optional[RemnawaveCheckBody] = None):
    panel_url = (body.panel_url if body else None) or ""
    api_token = (body.api_token if body else None) or ""
    if not panel_url.strip() or not api_token.strip():
        cfg = AppSettings(**storage.load_settings()).remnawave
        panel_url = panel_url.strip() or cfg.panel_url
        api_token = api_token.strip() or cfg.api_token
    else:
        panel_url, api_token = panel_url.strip(), api_token.strip()
    if not panel_url or not api_token:
        raise HTTPException(400, "Remnawave не настроен (нет URL или токена)")
    client = RemnavaveClient(panel_url, api_token)
    try:
        info = await client.check_connection()
        return {"ok": True, "detail": info}
    except RemnavaveError as exc:
        raise HTTPException(exc.status or 502, exc.detail)
    except Exception as exc:
        raise HTTPException(502, str(exc))


# ── Remnawave proxy endpoints ─────────────────────────────────────────────────

def _client() -> RemnavaveClient:
    raw = storage.load_settings()
    cfg = AppSettings(**raw).remnawave
    if not cfg.panel_url or not cfg.api_token:
        raise HTTPException(400, "Remnawave не настроен")
    return RemnavaveClient(cfg.panel_url, cfg.api_token)


@router.get("/remnawave/squads/internal")
async def get_internal_squads():
    """Returns list of {uuid, name} for internal squads."""
    client = _client()
    try:
        squads = await client.list_internal_squads()
        return [{"uuid": s["uuid"], "name": s["name"]} for s in squads]
    except RemnavaveError as exc:
        raise HTTPException(exc.status or 502, exc.detail)
    except Exception as exc:
        raise HTTPException(502, str(exc))


@router.get("/remnawave/squads/external")
async def get_external_squads():
    """Returns list of {uuid, name} for external squads."""
    client = _client()
    try:
        squads = await client.list_external_squads()
        return [{"uuid": s["uuid"], "name": s["name"]} for s in squads]
    except RemnavaveError as exc:
        raise HTTPException(exc.status or 502, exc.detail)
    except Exception as exc:
        raise HTTPException(502, str(exc))


@router.get("/remnawave/node-plugins")
async def get_node_plugins():
    """Returns list of {uuid, name} for available node plugins."""
    client = _client()
    try:
        plugins = await client.list_node_plugins()
        return [{"uuid": p["uuid"], "name": p["name"]} for p in plugins]
    except RemnavaveError as exc:
        raise HTTPException(exc.status or 502, exc.detail)
    except Exception as exc:
        raise HTTPException(502, str(exc))


# ── Templates ─────────────────────────────────────────────────────────────────

@router.get("/templates")
async def list_templates():
    return storage.load_templates()


@router.post("/templates", status_code=201)
async def create_template(body: TemplateCreate):
    templates = storage.load_templates()
    if body.is_default:
        for t in templates:
            t["is_default"] = False
    tpl = {
        "id": str(_uuid.uuid4()),
        "name": body.name,
        "config": body.config,
        "is_default": body.is_default,
        "host_template_ids": body.host_template_ids,
    }
    templates.append(tpl)
    storage.save_templates(templates)
    return tpl


@router.put("/templates/{template_id}")
async def update_template(template_id: str, body: TemplateUpdate):
    templates = storage.load_templates()
    idx = next((i for i, t in enumerate(templates) if t["id"] == template_id), None)
    if idx is None:
        raise HTTPException(404, "Template not found")
    if body.is_default:
        for t in templates:
            t["is_default"] = False
    if body.name is not None:
        templates[idx]["name"] = body.name
    if body.config is not None:
        templates[idx]["config"] = body.config
    if body.is_default is not None:
        templates[idx]["is_default"] = body.is_default
    if body.host_template_ids is not None:
        templates[idx]["host_template_ids"] = body.host_template_ids
    storage.save_templates(templates)
    return templates[idx]


@router.delete("/templates/{template_id}", status_code=204)
async def delete_template(template_id: str):
    templates = storage.load_templates()
    storage.save_templates([t for t in templates if t["id"] != template_id])
