"""
Marzban → Remnawave migration wrapper (Ф7).

Orchestrates the official `remnawave/migrate` binary (users) + our own preview
(counts + loss report). We do NOT vendor the AGPL tool — it runs as a separate
Docker container.

- `marzban_login` / `marzban_counts` — talk to the Marzban admin API for the
  preview (user/inbound counts + "what won't migrate" report). SSRF-guarded.
- `migrate_docker_args` — PURE builder of the `docker run remnawave/migrate` argv
  (unit-testable: asserts the required flags without a real binary).
- `parse_migrate_output` — PURE parser of the tool's stdout → {created, skipped,
  updated, failed}. Tolerant of format drift.
- `run_migrate` — stream the container's stdout into a Task (creds redacted from
  the echoed command; the tool's own output is streamed as-is).

⚠️ Marzban admin creds + the Remnawave token are per-request and NEVER logged.
The migrate binary's exact flags/output are best-effort (not verifiable offline);
the parser degrades gracefully.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any

import httpx

from app.services import net_guard
from app.services.task_store import TaskStatus


class MarzbanApiError(Exception):
    pass


def _redact(text: str, *secrets: str) -> str:
    out = text
    for s in secrets:
        if s:
            out = out.replace(s, "[redacted]")
    return out


async def marzban_login(base_url: str, username: str, password: str) -> str:
    """POST /api/admin/token (OAuth2 password form) → access_token. Raises
    MarzbanApiError (401 on bad creds)."""
    if not net_guard.is_safe_url(base_url):
        raise MarzbanApiError("URL Marzban не разрешён (нужен публичный http(s)).")
    url = f"{base_url.rstrip('/')}/api/admin/token"
    try:
        async with httpx.AsyncClient(timeout=15, verify=False) as c:
            r = await c.post(url, data={"username": username, "password": password})
    except Exception as exc:
        raise MarzbanApiError(f"Marzban недоступен: {_redact(str(exc), password)}")
    if r.status_code == 401:
        raise MarzbanApiError("Неверные admin-креды Marzban (401).")
    if r.status_code >= 400:
        raise MarzbanApiError(f"Marzban API {r.status_code}.")
    token = (r.json() or {}).get("access_token")
    if not token:
        raise MarzbanApiError("Marzban не вернул access_token.")
    return token


async def _get(base_url: str, token: str, path: str) -> Any:
    # Re-guard every fetch (net_guard contract): a base_url that resolved public at
    # login time can rebind to an internal IP before these calls.
    if not net_guard.is_safe_url(base_url):
        raise MarzbanApiError("URL Marzban не разрешён (нужен публичный http(s)).")
    async with httpx.AsyncClient(timeout=20, verify=False) as c:
        r = await c.get(
            f"{base_url.rstrip('/')}{path}",
            headers={"Authorization": f"Bearer {token}"},
        )
    if r.status_code >= 400:
        raise MarzbanApiError(f"Marzban API {r.status_code} на {path}.")
    return r.json()


async def marzban_counts(base_url: str, token: str) -> dict[str, Any]:
    """User total + inbound tags for the preview / loss report."""
    users = await _get(base_url, token, "/api/users?offset=0&limit=1")
    total_users = users.get("total", 0) if isinstance(users, dict) else 0
    try:
        inbounds_raw = await _get(base_url, token, "/api/inbounds")
    except MarzbanApiError:
        inbounds_raw = {}
    inbound_tags: list[str] = []
    # Marzban groups inbounds by protocol: {vless: [{tag}], vmess: [...]}.
    if isinstance(inbounds_raw, dict):
        for arr in inbounds_raw.values():
            for inb in arr if isinstance(arr, list) else []:
                if isinstance(inb, dict) and inb.get("tag"):
                    inbound_tags.append(inb["tag"])
    return {"total_users": total_users, "inbound_tags": inbound_tags}


async def marzban_core_config(base_url: str, token: str) -> dict:
    """GET /api/core/config → the live Xray config (source of Reality settings)."""
    data = await _get(base_url, token, "/api/core/config")
    return data if isinstance(data, dict) else {}


def migrate_docker_args(cfg: dict) -> list[str]:
    """Build `docker run --rm <image> <flags>`. PURE. `preserve_status` /
    `preserve_subhash` default ON (documented safety)."""
    image = cfg.get("image") or "remnawave/migrate:latest"
    args = ["run", "--rm", image, "--panel-type=marzban"]
    args.append(f"--panel-url={cfg['marzban_url']}")
    args.append(f"--username={cfg['marzban_username']}")
    args.append(f"--password={cfg['marzban_password']}")
    args.append(f"--remnawave-url={cfg['remnawave_url']}")
    args.append(f"--remnawave-token={cfg['remnawave_token']}")
    if cfg.get("preserve_status", True):
        args.append("--preserve-status")
    if cfg.get("preserve_subhash", True):
        args.append("--preserve-subhash")
    squads = cfg.get("internal_squad_uuids") or []
    if squads:
        args.append(f"--internal-squad={','.join(squads)}")
    if cfg.get("batch_size"):
        args.append(f"--batch-size={cfg['batch_size']}")
    return args


_NUM = r"(\d+)"
_PATTERNS = {
    "created": re.compile(rf"(?:created|создано|migrated)\D*{_NUM}", re.I),
    "updated": re.compile(rf"(?:updated|обновлено)\D*{_NUM}", re.I),
    "skipped": re.compile(rf"(?:skipped|пропущено)\D*{_NUM}", re.I),
    "failed": re.compile(rf"(?:failed|errors?|ошиб\w*)\D*{_NUM}", re.I),
}


def parse_migrate_output(text: str) -> dict[str, int]:
    """Best-effort extract {created, updated, skipped, failed} from the tool's
    stdout (last match of each wins — the summary line)."""
    out = {"created": 0, "updated": 0, "skipped": 0, "failed": 0}
    for key, pat in _PATTERNS.items():
        matches = pat.findall(text or "")
        if matches:
            out[key] = int(matches[-1])
    return out


async def run_migrate(task, cfg: dict) -> None:
    """Run the migrate container, streaming stdout into `task`. Never raises."""
    args = migrate_docker_args(cfg)
    secrets = (cfg.get("marzban_password", ""), cfg.get("remnawave_token", ""))
    try:
        task.set_step(1, TaskStatus.RUNNING)
        task.add_log("\x1b[36m[migrate] запуск remnawave/migrate...\x1b[0m")
        try:
            proc = await asyncio.create_subprocess_exec(
                "docker",
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
        except (FileNotFoundError, NotImplementedError, OSError):
            raise MarzbanApiError("Docker недоступен на хосте — миграция не запущена.")
        captured: list[str] = []
        assert proc.stdout is not None
        async for raw in proc.stdout:
            line = _redact(raw.decode(errors="replace").rstrip(), *secrets)
            captured.append(line)
            task.add_log(line)
        rc = await proc.wait()
        summary = parse_migrate_output("\n".join(captured))
        task.add_log(
            f"\x1b[36m[migrate] итог: создано {summary['created']}, "
            f"обновлено {summary['updated']}, пропущено {summary['skipped']}, "
            f"ошибок {summary['failed']}.\x1b[0m"
        )
        if rc != 0:
            raise MarzbanApiError(f"Миграция завершилась с кодом {rc} (см. лог).")
        task.finish(TaskStatus.SUCCESS)
        task.add_log("\n\x1b[1;32m✓ Миграция завершена.\x1b[0m")
    except Exception as exc:
        task.add_log(
            f"\n\x1b[1;31m✗ Ошибка миграции: {_redact(str(exc), *secrets)}\x1b[0m"
        )
        task.finish(TaskStatus.FAILED, _redact(str(exc), *secrets))
