"""Wave-4 Plan C (E5) — POST /api/panel/metrics.

Scrapes the Remnawave panel's Prometheus metrics on the panel box over SSH
(loopback :3001, basic-auth read from the box's .env) and returns a curated
summary. SSH creds are per-request (never persisted); the basic-auth secret is
used on the box and never returned or logged (silent channel). See
`services/panel_metrics.py` for R1/R2.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from app.api.panel_deploy import EnvReadRequest
from app.services import panel_metrics
from app.services.ssh_manager import SSHSession

router = APIRouter(prefix="/api/panel")

_BEGIN = "__METRICS_BEGIN__"


@router.post("/metrics")
async def panel_metrics_scrape(req: EnvReadRequest) -> dict[str, Any]:
    """Read the panel's Prometheus metrics and return a curated summary
    (online users, users-by-status, nodes online/total, raw metric count).
    Missing .env → 404; unreachable/failed scrape → 502."""
    ssh = SSHSession(req.ip, req.ssh_port, req.ssh_user, req.ssh_password)
    try:
        try:
            await ssh.connect()
        except Exception as exc:
            raise HTTPException(
                status_code=502,
                detail=f"Не удалось подключиться к серверу {req.ip}:{req.ssh_port}: {exc}",
            )
        out = await ssh.get_script_output(panel_metrics.metrics_scrape_script(), timeout=30)

        # No separator → partition returns (out, "", "") so `head` holds it all.
        head, _, body = out.partition(_BEGIN)
        if "__NO_ENV__" in head:
            raise HTTPException(
                status_code=404,
                detail="Файл /opt/remnawave/.env не найден (панель не установлена?).",
            )
        if "__CURL_FAIL__" in body:
            raise HTTPException(
                status_code=502,
                detail="Метрики недоступны: проверьте METRICS_PORT (3001) и basic-auth панели.",
            )
        parsed = panel_metrics.parse_prometheus(body)
        if not parsed:
            raise HTTPException(status_code=502, detail="Пустой ответ метрик панели.")
        return panel_metrics.summarize(parsed)
    finally:
        await ssh.close()
