"""Wave-5 Plan L (slice 1) — export/import per-account node-assistant data."""
from __future__ import annotations

import io
from typing import Optional

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.services import accounts, export_service

router = APIRouter(prefix="/api")


class ExportBody(BaseModel):
    stores: Optional[list[str]] = None
    include_secrets: bool = False


@router.get("/export/stores")
async def export_stores():
    return {"stores": export_service.available_stores(),
            "settings_sections": export_service.available_sections()}


@router.post("/import/peek")
async def import_peek(file: UploadFile = File(...)):
    """Что внутри архива — до записи на диск, чтобы выбирать осознанно."""
    blob = await file.read()
    try:
        return export_service.peek_archive(blob)
    except ValueError as e:
        raise HTTPException(422, str(e))
    except Exception:
        raise HTTPException(422, "Некорректный архив.")


@router.post("/export")
async def export_data(body: ExportBody):
    if body.include_secrets:
        # Password-encrypted secret export is deferred (Plan L Ф1 follow-up).
        raise HTTPException(400, "Экспорт с секретами пока не поддержан — секреты исключаются.")
    aid = accounts.current_account.get() or ""
    blob = export_service.build_archive(aid, body.stores, include_secrets=False)
    return StreamingResponse(
        io.BytesIO(blob),
        media_type="application/gzip",
        headers={"Content-Disposition": 'attachment; filename="node-assistant-export.tar.gz"'},
    )


@router.post("/import")
async def import_data(file: UploadFile = File(...), confirm: bool = Form(False),
                      stores: str = Form("")):
    """`stores` — список через запятую (в т.ч. `settings:<секция>`); пусто = всё."""
    if not confirm:
        raise HTTPException(400, "Импорт перезаписывает данные аккаунта — требуется confirm=true.")
    blob = await file.read()
    picked = [x.strip() for x in stores.split(",") if x.strip()] or None
    try:
        return export_service.restore_archive(
            accounts.current_account.get() or "", blob, picked)
    except ValueError as e:
        raise HTTPException(422, str(e))
    except Exception:
        # Untrusted upload — a corrupt/non-archive file must not 500.
        raise HTTPException(422, "Некорректный архив.")
