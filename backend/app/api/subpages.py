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

import re
import tempfile
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field, field_validator

from app.services import subpage_baseline, subpage_store
from app.services.ssh_manager import SSHSession
from app.services.task_store import TaskStatus, task_store

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


# ── Baseline of the vendor frontend (Wave-7 Plan G Ф4) ────────
#
# Literal paths, declared ABOVE the `/{page_id}`-shaped routes below on purpose.

_IMAGE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/:-]{0,180}$")

_BASELINE_STEPS = ["Подключение", "Извлечение из образа", "Скачивание и распаковка"]


class BaselinePull(BaseModel):
    ip: str
    ssh_user: str = "root"
    ssh_password: str = ""
    ssh_port: int = Field(default=22, ge=1, le=65535)
    image: str = "remnawave/subscription-page:7.2.6"

    @field_validator("image")
    @classmethod
    def _image(cls, v: str) -> str:
        v = (v or "").strip()
        # The image name is interpolated into a root shell on the node. It is
        # shlex-quoted there too, but a charset gate here keeps a typo from
        # becoming a support ticket instead of a 422.
        if not _IMAGE_RE.match(v):
            raise ValueError("Недопустимое имя образа")
        return v


@router.get("/baselines")
async def list_baselines() -> dict:
    return {"baselines": subpage_baseline.list_baselines()}


@router.get("/baselines/{digest}/files")
async def baseline_files(digest: str) -> dict:
    meta = subpage_baseline.get_manifest(digest)
    if not meta:
        raise HTTPException(404, "База не найдена")
    return {"digest": meta["digest"], "image": meta.get("image", ""),
            "files": meta.get("files", [])}


@router.get("/baselines/{digest}/files/{relpath:path}")
async def baseline_file(digest: str, relpath: str) -> Response:
    data = subpage_baseline.read_file(digest, relpath)
    if data is None:
        raise HTTPException(404, "Файл не найден")
    # Same reasoning as overlay members: vendor assets are never rendered here.
    return Response(
        content=data, media_type="application/octet-stream", headers=_MEMBER_HEADERS,
    )


@router.post("/baselines/pull")
async def pull_baseline(body: BaselinePull, background_tasks: BackgroundTasks) -> dict:
    """SSH into a node, copy the frontend out of the image, cache it by digest.

    SSH creds are per-request and never persisted (project rule); `account_id` is
    captured HERE rather than read from the ContextVar in the background task —
    the ContextVar's survival across BackgroundTask is version-dependent and the
    pinned fastapi differs from the one it was measured on."""
    task = task_store.create(total_steps=len(_BASELINE_STEPS))
    background_tasks.add_task(_pull_baseline, body, task.task_id)
    return {"task_id": task.task_id, "task_type": "subpage-baseline"}


async def _pull_baseline(req: BaselinePull, task_id: str) -> None:
    task = task_store.get(task_id)
    if not task:
        return
    ssh = SSHSession(req.ip, req.ssh_port, req.ssh_user, req.ssh_password)
    tmp = tempfile.TemporaryDirectory(prefix="na-baseline-")
    try:
        task.set_step(1, TaskStatus.RUNNING)
        task.add_log(f"Подключение к {req.ip}:{req.ssh_port}...")
        await ssh.connect()
        task.add_log("Подключено.")

        task.set_step(2, TaskStatus.RUNNING)
        task.add_log(f"Извлечение {subpage_baseline.IMAGE_PATH} из {req.image}...")
        out = await ssh.get_script_output(
            subpage_baseline.extract_tree_script(req.image), timeout=600,
        )
        probe = subpage_baseline.parse_probe(out)
        digest = probe.get("DIGEST", "")
        if not digest:
            raise RuntimeError("Не удалось определить digest образа")
        task.add_log(f"digest: {digest} ({probe.get('BYTES', '?')} байт архива)")

        task.set_step(3, TaskStatus.RUNNING)
        if subpage_baseline.has_baseline(digest):
            task.add_log("Эта база уже скачана — пропускаем.")
        else:
            local = Path(tmp.name) / "frontend.tgz"
            await ssh.download_file(f"{subpage_baseline._REMOTE_DIR}/frontend.tgz",
                                    str(local))
            meta = subpage_baseline.save_baseline(digest, req.image, local)
            task.add_log(f"Сохранено файлов: {meta['files_count']} "
                         f"({meta['bytes']} байт).")
        task.finish(TaskStatus.SUCCESS)
    except Exception as exc:
        task.add_log(f"Ошибка: {exc}")
        task.finish(TaskStatus.FAILED)
    finally:
        # Always try to clear the node's temp dir, even on failure.
        try:
            await ssh.get_output(subpage_baseline.cleanup_script())
        except Exception:
            pass
        await ssh.close()
        tmp.cleanup()


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
