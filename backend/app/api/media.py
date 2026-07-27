"""Shared media: upload / fetch / delete (see services/media_store.py).

The fetch route is the security-sensitive one — it hands user-uploaded bytes back
from our own origin. Raster images go out with their real type so they render in
a hosting card or inside a note; EVERYTHING else goes out as an opaque
attachment, so an uploaded SVG or PDF can never execute in the panel's origin.
"""
from __future__ import annotations

from fastapi import APIRouter, File, HTTPException, Response, UploadFile

from app.services import media_store as store

router = APIRouter(prefix="/api/media")


@router.get("")
async def list_media():
    return store.list_items()


@router.post("/upload", status_code=201)
async def upload(file: UploadFile = File(...)):
    content = await file.read()
    try:
        return store.add(file.filename or "file", content,
                         file.content_type or "application/octet-stream")
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.get("/{media_id}")
async def fetch(media_id: str):
    got = store.get(media_id)
    if got is None:
        raise HTTPException(404, "Медиафайл не найден")
    content, mime, name = got
    if store.is_inline(mime):
        return Response(content, media_type=mime, headers={
            "X-Content-Type-Options": "nosniff",
            # `sandbox` alone: it isolates the response into an opaque origin if a
            # browser ever treats it as a document, while leaving the image itself
            # renderable. Adding `default-src 'none'` here would block the img the
            # browser synthesises when the URL is opened in a tab directly.
            "Content-Security-Policy": "sandbox",
            "Cache-Control": "private, max-age=86400",
        })
    ascii_name = name.encode("ascii", "ignore").decode() or "file"
    return Response(content, media_type="application/octet-stream", headers={
        "Content-Disposition": f'attachment; filename="{ascii_name}"',
        "X-Content-Type-Options": "nosniff",
    })


@router.delete("/{media_id}", status_code=204)
async def delete(media_id: str):
    if not store.delete(media_id):
        raise HTTPException(404, "Медиафайл не найден")
    return Response(status_code=204)
