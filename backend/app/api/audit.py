"""Журнал действий (Настройки → Пользователи). Требует `admin.users`.

Отдельный роутер, а не часть `/api/users`, потому что журнал — это про установку
целиком, а не про конкретного пользователя, и права на него могут разойтись.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.api.auth import require_identity
from app.services import audit

router = APIRouter(prefix="/api/audit")


@router.get("")
async def read_audit(limit: int = Query(200, ge=1, le=2000),
                     only_denied: bool = False,
                     _actor: str = Depends(require_identity)) -> dict:
    return {"events": audit.tail(limit, only_denied)}
