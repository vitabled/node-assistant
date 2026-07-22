"""Orion subscription-page catalogue routes (Ф5) — /api/subpages.

Per-account store of static subscription-page HTML files (one index.html each).
Consumed later (Ф6) by the panel-deploy form to pick which page to volume-mount
into the `remnawave/subscription-page` container. Session-gated per-account
(mounted under `require_account` in main.py).

  GET    /api/subpages            — page metadata list (no HTML)
  POST   /api/subpages            — add a page {name, html}; 413 over limit, 422 empty
  GET    /api/subpages/{id}/raw   — raw HTML (for iframe preview / deploy mount)
  DELETE /api/subpages/{id}       — remove a page
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel

from app.services import subpage_store

router = APIRouter(prefix="/api/subpages")


class PageCreate(BaseModel):
    name: str
    html: str


@router.get("")
async def list_pages() -> dict:
    return {"pages": subpage_store.list_pages()}


@router.post("", status_code=201)
async def create_page(body: PageCreate) -> dict:
    # Size limit → 413 Payload Too Large; every other validation error (empty
    # name) → 422. Checked here against the store constant so the mapping is
    # explicit rather than pattern-matching the exception message.
    if len(body.html.encode("utf-8")) > subpage_store.MAX_HTML_BYTES:
        raise HTTPException(
            413, f"HTML превышает лимит {subpage_store.MAX_HTML_BYTES} байт"
        )
    try:
        return subpage_store.add_page(body.name, body.html)
    except ValueError as exc:
        raise HTTPException(422, str(exc))


# Defence-in-depth headers for the raw user HTML: `CSP: sandbox` makes the
# browser render it in a scriptless opaque origin even on a direct top-level
# navigation (the iframe preview already uses sandbox="", but this protects the
# case where someone opens /raw in a new tab), and `nosniff` stops MIME-sniffing.
_RAW_HEADERS = {
    "Content-Security-Policy": "sandbox",
    "X-Content-Type-Options": "nosniff",
}


@router.get("/{page_id}/raw")
async def get_page_raw(page_id: str) -> Response:
    html = subpage_store.get_page_html(page_id)
    if html is None:
        raise HTTPException(404, "Страница не найдена")
    return Response(
        content=html,
        media_type="text/html; charset=utf-8",
        headers=_RAW_HEADERS,
    )


@router.delete("/{page_id}", status_code=204)
async def delete_page(page_id: str):
    if not subpage_store.delete_page(page_id):
        raise HTTPException(404, "Страница не найдена")


# ══════════════════════════════════════════════════════════════
# Overlay variants (Wave-7 Plan G Ф5)
#
#   POST   /api/subpages/overlay              — create an empty variant
#   GET    /api/subpages/{id}/files           — manifest
#   GET    /api/subpages/{id}/files/{path}    — one member (opaque download)
#   PUT    /api/subpages/{id}/files/{path}    — write one member (raw body)
#   DELETE /api/subpages/{id}/files/{path}    — drop one member
#   GET    /api/subpages/{id}/download        — zip of the variant's own files
#
# ⚠️ Route order: literal single-segment paths MUST be declared before
# `/{page_id}`-shaped ones of the same method, or the parameterised route
# swallows them. `POST /overlay` is safe today only because the collection POST
# is unparameterised — keep new literals up here regardless.
#
# ── Headers for member responses: a DECISION, not a default ──
# The plan asked for `CSP: sandbox` (copied from /raw), which is wrong here:
# these are the real assets of a working SPA, and a sandbox header would break
# any future attempt to preview them. Serving them as renderable documents is
# equally wrong — they are attacker-influenced content on our own origin.
# Resolution: overlay members are NEVER renderable. They go out as opaque
# downloads (`application/octet-stream` + `attachment` + `nosniff`), which the
# editor still reads fine via fetch(). Preview, if it is ever added, must happen
# on the node that actually serves the page, not on ours.
_MEMBER_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Content-Disposition": "attachment",
}

_MAX_MEMBER_UPLOAD = subpage_store.MAX_FILE_BYTES


class OverlayCreate(BaseModel):
    name: str
    base_image: str = ""
    base_digest: str = ""


@router.post("/overlay", status_code=201)
async def create_overlay(body: OverlayCreate) -> dict:
    try:
        return subpage_store.add_overlay(body.name, body.base_image, body.base_digest)
    except ValueError as exc:
        raise HTTPException(422, str(exc))


@router.get("/{page_id}/files")
async def list_overlay_files(page_id: str) -> dict:
    files = subpage_store.list_files(page_id)
    if files is None:
        raise HTTPException(404, "Вариант не найден")
    return {"files": files}


@router.get("/{page_id}/download")
async def download_overlay(page_id: str) -> Response:
    blob = subpage_store.overlay_zip(page_id)
    if blob is None:
        raise HTTPException(404, "Вариант не найден")
    return Response(
        content=blob,
        media_type="application/zip",
        headers={
            "X-Content-Type-Options": "nosniff",
            "Content-Disposition": f'attachment; filename="{page_id}-overlay.zip"',
        },
    )


@router.get("/{page_id}/files/{relpath:path}")
async def get_overlay_file(page_id: str, relpath: str) -> Response:
    try:
        data = subpage_store.get_file(page_id, relpath)
    except subpage_store.RelPathError as exc:
        raise HTTPException(422, str(exc))
    if data is None:
        raise HTTPException(404, "Файл не найден")
    return Response(
        content=data, media_type="application/octet-stream", headers=_MEMBER_HEADERS,
    )


@router.put("/{page_id}/files/{relpath:path}")
async def put_overlay_file(page_id: str, relpath: str, request: Request) -> dict:
    data = await request.body()
    # Checked against the store constant BEFORE writing so an oversized body is
    # a 413 rather than a ValueError mapped to 422.
    if len(data) > _MAX_MEMBER_UPLOAD:
        raise HTTPException(413, f"Файл больше лимита {_MAX_MEMBER_UPLOAD} байт")
    try:
        meta = subpage_store.put_file(page_id, relpath, data)
    except subpage_store.RelPathError as exc:
        raise HTTPException(422, str(exc))
    except ValueError as exc:
        raise HTTPException(404 if "не найден" in str(exc) else 422, str(exc))

    out: dict = dict(meta)
    # A soft warning, never a refusal: the placeholder is upstream's and may be
    # renamed. Mounting an index.html without it is exactly the failure Wave 6
    # Ф1 had to fix — silently serving a page with no subscription data.
    if meta["path"].lower().endswith("index.html") and b"<%- panelData %>" not in data:
        out["warning"] = (
            "index.html без <%- panelData %> — страница не получит данные подписки"
        )
    return out


@router.delete("/{page_id}/files/{relpath:path}", status_code=204)
async def delete_overlay_file(page_id: str, relpath: str):
    try:
        ok = subpage_store.delete_file(page_id, relpath)
    except subpage_store.RelPathError as exc:
        raise HTTPException(422, str(exc))
    if not ok:
        raise HTTPException(404, "Файл не найден")
