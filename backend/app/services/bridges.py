"""Мосты (Wave-4 PR-6): маршрутизация трафика выбранных инбаундов через ноду-выход.

Модель (утверждена): мост = outbound к ноде-выходу + routing-правило В НАЧАЛЕ
списка rules в config-профилях Remnawave. Точка выхода берётся не с потолка:
backend заводит служебного пользователя `nai-bridge` (бессрочный, безлимитный,
во ВСЕХ внутренних сквадах) и извлекает готовый outbound из его подписки в
формате v2ray-json — адрес/порт/ключи подставляет сама панель.

Поля routing-правила — только документированные xtls RuleObject (по Role.txt):
domain, ip, port, network, protocol, inboundTag, outboundTag; плюс наш
служебный ruleTag (документирован) для идемпотентного удаления. Поле `type`
(легаси-формат) в актуальной документации НЕ упомянуто → не используем.

Хранилище — `accounts/<id>/bridges.json` (список мостов с описанием применения).
"""
from __future__ import annotations

import copy
import json
import threading
import uuid
from pathlib import Path
from typing import Any, Optional

import httpx

from app.services import accounts
from app.services.remnawave_client import RemnavaveClient, RemnavaveError

SERVICE_USERNAME = "nai-bridge"
SERVICE_EXPIRE = "2099-12-31T23:59:59Z"

_LOCK = threading.Lock()


# ── хранилище ──────────────────────────────────────────────────
def _path(account_id: Optional[str]) -> Path:
    aid = account_id or accounts.current_account.get()
    if not aid:
        raise RuntimeError("No active account in context")
    return accounts.data_dir(aid) / "bridges.json"


def list_bridges(account_id: Optional[str] = None) -> list[dict]:
    p = _path(account_id)
    try:
        if p.exists():
            data = json.loads(p.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
    except Exception:
        pass
    return []


def _save(account_id: Optional[str], items: list[dict]) -> None:
    p = _path(account_id)
    with _LOCK:
        p.write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")


def get_bridge(bridge_id: str, account_id: Optional[str] = None) -> Optional[dict]:
    return next((b for b in list_bridges(account_id) if b.get("id") == bridge_id), None)


# ── служебный пользователь ─────────────────────────────────────
async def ensure_service_user(client: RemnavaveClient) -> dict:
    """Пользователь `nai-bridge` — бессрочный (2099), безлимитный (0 байт,
    NO_RESET), во всех внутренних сквадах (чтобы его подписка видела все ноды).
    Создаётся один раз, дальше переиспользуется."""
    try:
        user = await client.get_user_by_username(SERVICE_USERNAME)
        if isinstance(user, dict) and user.get("uuid"):
            return user
    except RemnavaveError as e:
        if e.status != 404:
            raise
    squads = await client.list_internal_squads()
    return await client.create_user({
        "username": SERVICE_USERNAME,
        "status": "ACTIVE",
        "expireAt": SERVICE_EXPIRE,
        "trafficLimitBytes": 0,
        "trafficLimitStrategy": "NO_RESET",
        "activeInternalSquads": [s["uuid"] for s in squads if s.get("uuid")],
        "description": "Служебный пользователь node-assistant для мостов. Не удалять.",
    })


# ── outbound ноды-выхода из подписки ───────────────────────────
async def fetch_subscription_outbounds(panel_url: str, short_uuid: str) -> list[dict]:
    """GET {panel}/api/sub/{shortUuid}/v2ray-json → все outbound'ы Xray-конфига.
    Подписка публична по секретному shortUuid — токен не нужен."""
    url = f"{panel_url.rstrip('/')}/api/sub/{short_uuid}/v2ray-json"
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.get(url)
        r.raise_for_status()
        data = r.json()
    configs = data if isinstance(data, list) else [data]
    out: list[dict] = []
    for cfg in configs:
        if isinstance(cfg, dict):
            out.extend(o for o in (cfg.get("outbounds") or []) if isinstance(o, dict))
    return out


def _outbound_addr(ob: dict) -> tuple[str, int]:
    vnext = ((ob.get("settings") or {}).get("vnext") or [])
    if vnext and isinstance(vnext[0], dict):
        v = vnext[0]
        try:
            return str(v.get("address") or "").strip().lower(), int(v.get("port") or 0)
        except (TypeError, ValueError):
            return str(v.get("address") or "").strip().lower(), 0
    return "", 0


def pick_exit_outbound(outbounds: list[dict], address: str, name: str = "") -> tuple[dict, bool]:
    """Outbound ноды-выхода: точный матч по адресу, иначе — первый проксёвый
    (matched=False, в ответе API это помечается)."""
    addr = address.strip().lower()
    for ob in outbounds:
        a, _p = _outbound_addr(ob)
        if a and a == addr:
            return ob, True
    if name:
        low = name.strip().lower()
        for ob in outbounds:
            if low and low in str(ob.get("tag") or "").lower():
                return ob, True
    for ob in outbounds:
        if ob.get("protocol") not in ("freedom", "blackhole", "dns"):
            return ob, False
    raise ValueError("В подписке служебного пользователя нет ни одного outbound'а")


# ── сборка outbound'а и правила (документированные поля xtls) ──
def bridge_tag(bridge_id: str) -> str:
    return f"bridge-{bridge_id}"


def rule_tag(bridge_id: str) -> str:
    return f"nai-bridge-{bridge_id}"


def build_outbound(base: dict, bridge_id: str) -> dict:
    ob = copy.deepcopy(base)
    ob["tag"] = bridge_tag(bridge_id)
    return ob


def build_rule(bridge_id: str, inbound_tags: list[str], matchers: dict) -> dict:
    """Routing-правило моста. Только документированные поля RuleObject:
    domain[], ip[], protocol[] (массивы), port, network (строки). Несколько
    matcher-полей работают по AND, значения внутри массива — по OR."""
    rule: dict[str, Any] = {
        "outboundTag": bridge_tag(bridge_id),
        "ruleTag": rule_tag(bridge_id),
    }
    tags = [t for t in (inbound_tags or []) if t]
    if tags:
        rule["inboundTag"] = tags
    for key in ("domain", "ip", "protocol"):
        vals = [str(v).strip() for v in (matchers.get(key) or []) if str(v).strip()]
        if vals:
            rule[key] = vals
    for key in ("port", "network"):
        v = str(matchers.get(key) or "").strip()
        if v:
            rule[key] = v
    return rule


# ── применение/очистка конфиг-профиля (чистые функции — тесты) ──
def apply_bridge_to_config(config: dict, outbound: dict, rule: dict) -> dict:
    """Добавить outbound моста и правило В НАЧАЛО rules. Идемпотентно по тегам:
    повторное применение заменяет прежние. Первый outbound НЕ трогаем — он
    остаётся дефолтным для непроматченного трафика."""
    cfg = copy.deepcopy(config or {})
    obs = [o for o in (cfg.get("outbounds") or []) if o.get("tag") != outbound["tag"]]
    obs.append(outbound)
    cfg["outbounds"] = obs
    routing = cfg.setdefault("routing", {})
    rules = [r for r in (routing.get("rules") or []) if r.get("ruleTag") != rule["ruleTag"]]
    rules.insert(0, rule)
    routing["rules"] = rules
    return cfg


def strip_bridge_from_config(config: dict, bridge_id: str) -> dict:
    """Убрать outbound и правила моста по служебным тегам (нет → конфиг не меняется)."""
    cfg = copy.deepcopy(config or {})
    cfg["outbounds"] = [o for o in (cfg.get("outbounds") or [])
                        if o.get("tag") != bridge_tag(bridge_id)]
    routing = cfg.get("routing")
    if isinstance(routing, dict):
        routing["rules"] = [r for r in (routing.get("rules") or [])
                            if r.get("ruleTag") != rule_tag(bridge_id)]
    return cfg


# ── оркестрация ────────────────────────────────────────────────
async def create_bridge(client: RemnavaveClient, *, name: str, exit_node: dict,
                        inbound_tags: list[str], profile_uuids: list[str],
                        matchers: dict, account_id: Optional[str] = None) -> dict:
    """Полный цикл: служебный юзер → outbound из подписки → запись в профили →
    сохранение записи моста."""
    user = await ensure_service_user(client)
    short_uuid = user.get("shortUuid") or ""
    if not short_uuid:
        raise ValueError("Служебный пользователь без shortUuid — подписку не получить")

    outbounds = await fetch_subscription_outbounds(client.base_url, short_uuid)
    base, matched = pick_exit_outbound(
        outbounds, str(exit_node.get("address") or ""), str(exit_node.get("name") or ""))

    bridge_id = uuid.uuid4().hex[:8]
    outbound = build_outbound(base, bridge_id)
    rule = build_rule(bridge_id, inbound_tags, matchers)

    applied, errors = [], []
    for puuid in profile_uuids:
        try:
            profile = await client.get_config_profile(puuid)
            config = (profile or {}).get("config") or {}
            new_config = apply_bridge_to_config(config, outbound, rule)
            await client.update_config_profile(puuid, new_config)
            applied.append(puuid)
        except Exception as exc:
            errors.append({"profile": puuid, "error": str(exc)})

    record = {
        "id": bridge_id,
        "name": name.strip() or f"Мост {bridge_id}",
        "exit_node": {"uuid": exit_node.get("uuid"), "name": exit_node.get("name"),
                      "address": exit_node.get("address")},
        "outbound_matched": matched,
        "inbound_tags": inbound_tags,
        "profile_uuids": profile_uuids,
        "applied_profiles": applied,
        "profile_errors": errors,
        "matchers": matchers,
        "rule": rule,
        "service_user": SERVICE_USERNAME,
    }
    items = list_bridges(account_id)
    items.append(record)
    _save(account_id, items)
    return record


async def delete_bridge(client: RemnavaveClient, bridge_id: str,
                        account_id: Optional[str] = None) -> dict:
    """Убрать outbound и правило из всех профилей моста + удалить запись."""
    record = get_bridge(bridge_id, account_id)
    if not record:
        raise KeyError(bridge_id)
    errors = []
    for puuid in record.get("applied_profiles") or record.get("profile_uuids") or []:
        try:
            profile = await client.get_config_profile(puuid)
            config = (profile or {}).get("config") or {}
            await client.update_config_profile(puuid, strip_bridge_from_config(config, bridge_id))
        except Exception as exc:
            errors.append({"profile": puuid, "error": str(exc)})
    items = [b for b in list_bridges(account_id) if b.get("id") != bridge_id]
    _save(account_id, items)
    return {"ok": True, "profile_errors": errors}
