"""API раздела «RKNscanner»: центральный список адресов сканеров + сбор с нод.

Контракт (зеркально `/api/f2b-list`, но с сохранением истории наблюдений):

  * `GET    /api/rkn-scanners`           — список + total + updatedAt;
  * `POST   /api/rkn-scanners/save`      — ручная правка центрального списка;
  * `POST   /api/rkn-scanners/collect`   — сбор с ОДНОЙ ноды (список не меняется);
  * `POST   /api/rkn-scanners/sync`      — сбор со списка нод → merge → опц. раздача назад;
  * `DELETE /api/rkn-scanners`           — очистка.

SSH-креды приходят в теле каждого запроса к ноде (`models.ssh_creds.SshCreds`),
на диск не ложатся — как во всех остальных SSH-ручках панели.
"""
from __future__ import annotations

import ipaddress
from typing import Any, Optional, Union

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator

from app.models.ssh_creds import SshCreds
from app.services import rkn_scanners

router = APIRouter(prefix="/api/rkn-scanners")


class NodeRequest(SshCreds):
    """SSH-креды одной ноды для не сохраняемого на диск действия."""

    ssh_port: int = Field(default=22, ge=1, le=65535)
    since_hours: int = Field(default=rkn_scanners.DEFAULT_SINCE_HOURS, ge=1,
                             le=rkn_scanners.MAX_SINCE_HOURS)

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


class SyncNodeRequest(NodeRequest):
    collect: bool = True
    apply: bool = False


class SyncRequest(BaseModel):
    nodes: list[SyncNodeRequest] = Field(min_length=1, max_length=50)
    merge_collected: bool = True
    # Общий флаг раздачи: панель шлёт и его, и `apply` на каждой ноде (в словесном
    # контракте раздела флаг общий) — учитываем оба, чтобы не разойтись с экраном.
    apply: bool = False


class EntryBody(BaseModel):
    """Запись в том виде, в каком её отдаёт GET (лишние поля игнорируются).

    `firstSeen`/`lastSeen` принимаем и числом: экран ставит туда `Date.now()`, а
    сервис приводит метку к своему формату (UTC, ISO, секунды).
    """

    ip: str
    port: Optional[int] = None
    chain: Optional[str] = None
    hits: Optional[int] = None
    firstSeen: Optional[Union[str, int, float]] = None
    lastSeen: Optional[Union[str, int, float]] = None
    nodes: list[str] = Field(default_factory=list)
    source: Optional[str] = None


class SaveBody(BaseModel):
    """Ручная правка: либо голые адреса, либо записи как их отдаёт GET."""

    entries: list[Union[str, EntryBody]] = Field(default_factory=list)


def _node_error(req: NodeRequest, verb: str, exc: Exception) -> HTTPException:
    return HTTPException(
        status_code=502,
        detail=(f"Не удалось выполнить {verb} с ноды {req.ip}:{req.ssh_port}: "
                f"{str(exc)[:200]}"),
    )


@router.get("")
async def get_list():
    document = rkn_scanners.load_document()
    entries = document["entries"]
    return {"entries": entries, "total": len(entries),
            "updatedAt": document["updatedAt"]}


@router.post("/save")
async def save_list(body: SaveBody):
    """Ручная правка центрального списка: голые адреса или записи из GET.

    Известные адреса сохраняют firstSeen/hits/nodes (счётчики наблюдений — не поле
    для правки), новые получают source=manual.
    """
    payload = [entry.model_dump() if isinstance(entry, EntryBody) else entry
               for entry in body.entries]
    try:
        saved = rkn_scanners.save(payload)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    document = rkn_scanners.load_document()
    return {"ok": True, "entries": saved, "total": len(saved),
            "updatedAt": document["updatedAt"]}


@router.delete("")
async def clear_list():
    document = rkn_scanners.clear()
    return {"ok": True, "entries": [], "total": 0, "updatedAt": document["updatedAt"]}


@router.post("/collect")
async def collect_node(body: NodeRequest):
    """Сбор с одной ноды: центральный список здесь НЕ меняется (его правит /sync)."""
    try:
        scanners = await rkn_scanners.collect_node(body, body.since_hours)
    except Exception as exc:
        raise _node_error(body, "сбор сканеров", exc) from None
    return {"ip": body.ip, "sinceHours": body.since_hours,
            "total": len(scanners), "scanners": scanners}


@router.post("/sync")
async def sync_nodes(body: SyncRequest):
    """Сбор со всех переданных нод → merge в центральный список → опц. раздача назад."""
    merger = rkn_scanners.merge if body.merge_collected else None
    totals = {"added": 0, "updated": 0, "hitsAdded": 0}
    results: list[dict] = []

    for node in body.nodes:
        result: dict[str, Any] = {"ip": node.ip, "ok": True}
        if node.collect:
            try:
                scanners = await rkn_scanners.collect_node(node, node.since_hours)
                result["collected"] = len(scanners)
                result["hits"] = sum(int(item.get("hits") or 0) for item in scanners)
                if merger is not None and scanners:
                    merged = merger(scanners, node.ip)
                    result["merged"] = {"added": merged["added"],
                                        "updated": merged["updated"]}
                    for key in totals:
                        totals[key] += merged[key]
            except Exception as exc:
                result.update(ok=False, error=str(exc)[:200])
        results.append(result)

    central = rkn_scanners.load()
    if body.apply or any(node.apply for node in body.nodes):
        for node, result in zip(body.nodes, results):
            if not (body.apply or node.apply):
                continue
            try:
                applied = await rkn_scanners.apply_node(node, [r["ip"] for r in central])
                result["applied"] = applied["applied"]
                result["inSet"] = applied["inSet"]
                if not applied["ok"]:
                    result.update(ok=False, error="нода применила список не полностью "
                                                  "(RKN_SCANNERS_RESULT=CHECK)")
            except Exception as exc:
                result.update(ok=False, error=str(exc)[:200])

    document = rkn_scanners.load_document()
    return {"results": results, "total": len(central),
            "updatedAt": document["updatedAt"], "merged": totals,
            # Плоские итоги для экрана раздела: он умеет считать их и сам (сумма по
            # нодам), но пусть оба пути дают одно число.
            "collected": sum(int(r.get("collected") or 0) for r in results),
            "new": totals["added"]}
