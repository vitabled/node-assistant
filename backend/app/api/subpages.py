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

from fastapi import APIRouter, HTTPException
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
