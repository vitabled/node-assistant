from __future__ import annotations
from datetime import datetime, timezone
from typing import Optional
from fastapi import APIRouter, HTTPException
from app.services import storage
from app.models.settings import AppSettings
from app.models.traffic_rules import TrafficRule, TrafficRuleCreate, TrafficRuleUpdate, GiB
from app.services.remnawave_client import RemnavaveClient, RemnavaveError

router = APIRouter(prefix="/api")


def _client() -> RemnavaveClient:
    raw = storage.load_settings()
    cfg = AppSettings(**raw).remnawave
    if not cfg.panel_url or not cfg.api_token:
        raise HTTPException(400, "Remnawave не настроен")
    return RemnavaveClient(cfg.panel_url, cfg.api_token)


async def apply_rule_to_remnawave(client: RemnavaveClient, rule: TrafficRule) -> None:
    """
    Push a traffic rule to Remnawave.

    ALL scope:  PATCH /api/nodes — sets node-level monthly bandwidth cap.
                Only MONTH and NO_RESET periods are supported; DAY/WEEK raise 422.

    SQUAD scope: POST /api/users/bulk/update — sets per-user traffic limit for
                 all members of the selected squads. Limits are global (not
                 node-specific), which is the closest the Remnawave API allows.
    """
    limit_bytes = int(rule.limit_gb * GiB)

    if rule.scope == "ALL":
        if rule.period in ("DAY", "WEEK"):
            raise HTTPException(
                422,
                "Периоды «в день» и «в неделю» не поддерживаются для области "
                "«Все пользователи» — Remnawave управляет нодовым лимитом только "
                "с ежемесячным сбросом. Выберите «В месяц» или область «Сквад».",
            )
        is_active = rule.period == "MONTH" and rule.limit_gb > 0
        await client.update_node_traffic(
            rule.node_uuid,
            is_active=is_active,
            limit_bytes=limit_bytes if is_active else 0,
            reset_day=1,
        )

    else:  # SQUAD scope
        strategy_map = {
            "DAY":      "DAY",
            "WEEK":     "WEEK",
            "MONTH":    "MONTH",
            "NO_RESET": "NO_RESET",
        }
        strategy      = strategy_map[rule.period]
        actual_limit  = 0 if rule.period == "NO_RESET" else limit_bytes

        all_uuids: list[str] = []
        for squad_uuid in rule.squad_uuids:
            uuids = await client.get_users_in_squad(squad_uuid)
            all_uuids.extend(uuids)

        deduped = list(dict.fromkeys(all_uuids))

        if deduped:
            await client.bulk_update_users_traffic(
                deduped,
                limit_bytes=actual_limit,
                strategy=strategy,
            )


# ── CRUD ──────────────────────────────────────────────────────────────────────

@router.get("/traffic-rules")
async def list_traffic_rules():
    return storage.load_traffic_rules()


@router.post("/traffic-rules", status_code=201)
async def create_traffic_rule(body: TrafficRuleCreate):
    client = _client()
    rule = TrafficRule(**body.model_dump())

    try:
        await apply_rule_to_remnawave(client, rule)
        rule.sync_status    = "synced"
        rule.last_synced_at = datetime.now(timezone.utc).isoformat()
        rule.sync_error     = None
    except HTTPException:
        raise
    except RemnavaveError as exc:
        rule.sync_status = "error"
        rule.sync_error  = exc.detail
    except Exception as exc:
        rule.sync_status = "error"
        rule.sync_error  = str(exc)

    rules = storage.load_traffic_rules()
    rules.append(rule.model_dump())
    storage.save_traffic_rules(rules)
    return rule.model_dump()


@router.patch("/traffic-rules/{rule_id}")
async def update_traffic_rule(rule_id: str, body: TrafficRuleUpdate):
    rules = storage.load_traffic_rules()
    idx = next((i for i, r in enumerate(rules) if r["id"] == rule_id), None)
    if idx is None:
        raise HTTPException(404, "Правило не найдено")

    data = {**rules[idx], **body.model_dump(exclude_none=True)}
    rule = TrafficRule(**data)
    client = _client()

    try:
        await apply_rule_to_remnawave(client, rule)
        rule.sync_status    = "synced"
        rule.last_synced_at = datetime.now(timezone.utc).isoformat()
        rule.sync_error     = None
    except HTTPException:
        raise
    except RemnavaveError as exc:
        rule.sync_status = "error"
        rule.sync_error  = exc.detail
    except Exception as exc:
        rule.sync_status = "error"
        rule.sync_error  = str(exc)

    rules[idx] = rule.model_dump()
    storage.save_traffic_rules(rules)
    return rules[idx]


@router.delete("/traffic-rules/{rule_id}", status_code=204)
async def delete_traffic_rule(rule_id: str):
    rules = storage.load_traffic_rules()
    storage.save_traffic_rules([r for r in rules if r["id"] != rule_id])


@router.post("/traffic-rules/{rule_id}/sync")
async def sync_traffic_rule(rule_id: str):
    """Re-apply a rule to Remnawave (re-sync after a previous error)."""
    rules = storage.load_traffic_rules()
    idx = next((i for i, r in enumerate(rules) if r["id"] == rule_id), None)
    if idx is None:
        raise HTTPException(404, "Правило не найдено")

    rule = TrafficRule(**rules[idx])
    client = _client()

    try:
        await apply_rule_to_remnawave(client, rule)
        rule.sync_status    = "synced"
        rule.last_synced_at = datetime.now(timezone.utc).isoformat()
        rule.sync_error     = None
    except HTTPException:
        raise
    except RemnavaveError as exc:
        rule.sync_status = "error"
        rule.sync_error  = exc.detail
    except Exception as exc:
        rule.sync_status = "error"
        rule.sync_error  = str(exc)

    rules[idx] = rule.model_dump()
    storage.save_traffic_rules(rules)
    return rules[idx]


# ── Remnawave proxy: nodes list ───────────────────────────────────────────────

@router.get("/remnawave/nodes")
async def get_remnawave_nodes():
    """Proxy GET /api/nodes from Remnawave; returns [{uuid, name, address}]."""
    client = _client()
    try:
        nodes = await client.list_nodes()
        return [
            {
                "uuid":    n["uuid"],
                "name":    n["name"],
                "address": n.get("address", ""),
            }
            for n in nodes
        ]
    except RemnavaveError as exc:
        raise HTTPException(exc.status or 502, exc.detail)
    except Exception as exc:
        raise HTTPException(502, str(exc))
