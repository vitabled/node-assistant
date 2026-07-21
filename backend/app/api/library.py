"""Wave-5 Plan C (scoped) — knowledge library: files + markdown notes."""
from __future__ import annotations

import io

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.services import library_store as store

router = APIRouter(prefix="/api/library")


class NoteBody(BaseModel):
    name: str = Field("Заметка", max_length=200)
    text: str = Field("", max_length=200000)


@router.get("")
async def list_items():
    return store.list_items()


@router.post("/upload", status_code=201)
async def upload(file: UploadFile = File(...)):
    content = await file.read()
    try:
        return store.add_file(file.filename or "file", content, file.content_type or "application/octet-stream")
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/notes", status_code=201)
async def create_note(body: NoteBody):
    try:
        return store.add_note(body.name.strip(), body.text)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/notes/{item_id}")
async def get_note(item_id: str):
    it = store.get_note(item_id)
    if not it:
        raise HTTPException(404, "Заметка не найдена")
    return it


@router.put("/notes/{item_id}")
async def update_note(item_id: str, body: NoteBody):
    updated = store.update_note(item_id, body.name.strip(), body.text)
    if not updated:
        raise HTTPException(404, "Заметка не найдена")
    return updated


@router.get("/files/{item_id}")
async def download(item_id: str):
    got = store.get_file(item_id)
    if not got:
        raise HTTPException(404, "Файл не найден")
    content, filename, mime = got
    return StreamingResponse(io.BytesIO(content), media_type=mime,
                             headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.delete("/{item_id}", status_code=204)
async def delete_item(item_id: str):
    if not store.delete_item(item_id):
        raise HTTPException(404, "Не найдено")
