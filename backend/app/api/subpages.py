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

from app.services import accounts, subpage_baseline, subpage_store
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
    # Captured HERE (request context). The background task can't read the
    # ContextVar, and the baseline cache is now per-account.
    account_id = accounts.current_account.get() or ""
    background_tasks.add_task(_pull_baseline, body, task.task_id, account_id)
    return {"task_id": task.task_id, "task_type": "subpage-baseline"}


async def _pull_baseline(req: BaselinePull, task_id: str, account_id: str = "") -> None:
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
        if subpage_baseline.has_baseline(digest, account_id):
            task.add_log("Эта база уже скачана — пропускаем.")
        else:
            local = Path(tmp.name) / "frontend.tgz"
            await ssh.download_file(f"{subpage_baseline._REMOTE_DIR}/frontend.tgz",
                                    str(local))
            meta = subpage_baseline.save_baseline(digest, req.image, local, account_id)
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


# ══════════════════════════════════════════════════════════════
# Редактор РАЗВЁРНУТОЙ страницы (Wave-4 PR-10)
#
# Реально развёрнутая подписочная страница живёт на сервере панели: контейнер
# `remnawave-subscription-page` с bind-mount'ом (директория ./frontend или
# одиночный index.html) либо страницей из образа (builtin — нечего править,
# предлагаем задеплоить вариант). Эти эндпоинты читают и ПИШУТ файлы по SSH
# (креды per-request, не хранятся). Запись — атомарная (tmp+mv), с бэкапом
# прежней версии в .nai-backup/<timestamp>/ и опциональным рестартом контейнера.
# ══════════════════════════════════════════════════════════════

class DeployedCreds(BaseModel):
    ip: str
    ssh_port: int = 22
    ssh_user: str = "root"
    ssh_password: str = Field(..., min_length=1)


class DeployedRead(DeployedCreds):
    path: str


class DeployedWrite(DeployedRead):
    content: str = ""
    restart: bool = True


# Путь внутри mount-директории: относительный, без обхода и абсолютов.
_REL_OK = re.compile(r"^[A-Za-z0-9._\-/]+$")


def _rel_ok(p: str) -> bool:
    return bool(p) and not p.startswith("/") and ".." not in p.split("/") and bool(_REL_OK.match(p))


_INSPECT_SCRIPT = r"""
set -u
CONT=remnawave-subscription-page
docker inspect "$CONT" >/dev/null 2>&1 || { echo "MODE=missing"; exit 0; }
# mount с Destination /opt/app/frontend (директория) или .../index.html (файл)
LINE=$(docker inspect -f '{{range .Mounts}}{{.Source}}|{{.Destination}}{{"\n"}}{{end}}' "$CONT" \
  | grep -E '\|/opt/app/frontend(/index\.html)?$' | head -1 || true)
if [ -z "$LINE" ]; then
  echo "MODE=builtin"
  exit 0
fi
SRC="${LINE%%|*}"; DST="${LINE##*|}"
if [ "$DST" = "/opt/app/frontend/index.html" ]; then
  echo "MODE=file"
  echo "MOUNT=$SRC"
  if [ -f "$SRC" ]; then
    BYTES=$(stat -c %s "$SRC" 2>/dev/null || echo 0)
    echo "FILE=index.html|$BYTES"
  fi
else
  echo "MODE=dir"
  echo "MOUNT=$SRC"
  if [ -d "$SRC" ]; then
    find "$SRC" -type f -printf '%P|%s\n' | head -200
  fi
fi
"""


def _parse_inspect(out: str) -> dict:
    lines = [l for l in out.splitlines() if l.strip()]
    mode = "builtin"
    mount = ""
    files = []
    for l in lines:
        if l.startswith("MODE="):
            mode = l[5:].strip()
        elif l.startswith("MOUNT="):
            mount = l[6:].strip()
        elif l.startswith("FILE=") or ("|" in l and not l.startswith(("MODE", "MOUNT"))):
            rel, _, size = (l[5:] if l.startswith("FILE=") else l).rpartition("|")
            if _rel_ok(rel):
                try:
                    files.append({"path": rel, "size": int(size)})
                except ValueError:
                    pass
    return {"mode": mode, "mount": mount, "files": files}


async def _ssh_run(creds: DeployedCreds, script: str, timeout: float = 60) -> str:
    ssh = SSHSession(creds.ip, creds.ssh_port, creds.ssh_user, creds.ssh_password)
    try:
        await ssh.connect()
        return await ssh.get_script_output(script, timeout=timeout)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(502, f"SSH-операция не удалась: {exc}")
    finally:
        await ssh.close()


@router.post("/deployed/inspect")
async def deployed_inspect(body: DeployedCreds) -> dict:
    """Режим развёртывания + список файлов развёрнутой страницы."""
    out = await _ssh_run(body, _INSPECT_SCRIPT)
    return _parse_inspect(out)


@router.post("/deployed/read")
async def deployed_read(body: DeployedRead) -> dict:
    """Содержимое файла из mount-директории (mode=dir или единственный index.html)."""
    if not _rel_ok(body.path):
        raise HTTPException(422, "Недопустимый путь")
    # Читаем только под mount'ом; base64 чтобы не ломать бинарные/юникод.
    script = rf"""\
LINE=$(docker inspect -f '{{{{range .Mounts}}}}{{{{.Source}}}}|{{{{.Destination}}}}{{{{"\n"}}}}{{{{end}}}}' remnawave-subscription-page \
  | grep -E '\|/opt/app/frontend(/index\.html)?$' | head -1 || true)
[ -n "$LINE" ] || {{ echo "__NAI_ERR__страница встроена в образ — править нечего"; exit 0; }}
SRC="${{LINE%%|*}}"
if [ -d "$SRC" ]; then
  [ -f "$SRC/{body.path}" ] && base64 "$SRC/{body.path}" || echo "__NAI_ERR__файл не найден"
elif [ -f "$SRC" ] && [ "{body.path}" = "index.html" ]; then
  base64 "$SRC"
else
  echo "__NAI_ERR__файл не найден"
fi
"""
    out = await _ssh_run(body, script)
    if out.startswith("__NAI_ERR__"):
        raise HTTPException(404, out[len("__NAI_ERR__"):])
    import base64 as _b64
    try:
        data = _b64.b64decode(out.strip(), validate=True)
    except Exception:
        raise HTTPException(502, "Не удалось прочитать файл (повреждён ответ)")
    if len(data) > 2 * 1024 * 1024:
        raise HTTPException(413, "Файл больше 2 МиБ — правьте на сервере")
    return {"path": body.path, "content": data.decode("utf-8", "replace")}


@router.post("/deployed/write")
async def deployed_write(body: DeployedWrite) -> dict:
    """Атомарная запись файла + бэкап прежней версии + опциональный рестарт."""
    if not _rel_ok(body.path):
        raise HTTPException(422, "Недопустимый путь")
    import base64 as _b64
    b64 = _b64.b64encode(body.content.encode("utf-8")).decode()
    script = rf"""\
set -e
LINE=$(docker inspect -f '{{{{range .Mounts}}}}{{{{.Source}}}}|{{{{.Destination}}}}{{{{"\n"}}}}{{{{end}}}}' remnawave-subscription-page \
  | grep -E '\|/opt/app/frontend(/index\.html)?$' | head -1 || true)
[ -n "$LINE" ] || {{ echo "__NAI_ERR__страница встроена в образ — править нечего"; exit 1; }}
SRC="${{LINE%%|*}}"
DST="$SRC/{body.path}"
[ -d "$SRC" ] || DST="$SRC"
[ -f "$SRC" ] && [ "{body.path}" != "index.html" ] && {{ echo "__NAI_ERR__режим одиночного файла: правится только index.html"; exit 1; }}
mkdir -p "$(dirname "$DST")" "$SRC/.nai-backup/$(date +%s)"
if [ -f "$DST" ]; then
  cp "$DST" "$SRC/.nai-backup/$(date +%s)/{body.path.replace('/', '_')}.bak"
fi
TMP="$DST.nai-tmp"
base64 -d > "$TMP" <<'NAI_B64'
{b64}
NAI_B64
mv "$TMP" "$DST"
echo "WROTE=$DST"
{"docker restart remnawave-subscription-page >/dev/null 2>&1 && echo RESTARTED=1 || echo RESTARTED=0" if body.restart else "echo RESTARTED=skipped"}
"""
    out = await _ssh_run(body, script, timeout=90)
    if "__NAI_ERR__" in out:
        raise HTTPException(502, out.split("__NAI_ERR__")[-1].strip())
    return {
        "ok": "WROTE=" in out,
        "restarted": "RESTARTED=1" in out,
        "output": out[-500:],
    }
