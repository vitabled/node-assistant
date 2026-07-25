"""Wave-8 §3 — global self-update (git pull + `docker compose build/up` via a
DooD sidecar).

The backend runs from an IMAGE (the code is COPIED in, `.git` is not present), so
it can't run git against the host repo directly. Every git/compose action runs in
a short-lived sidecar that bind-mounts the HOST project dir — resolved from THIS
container's compose label `com.docker.compose.project.working_dir` — plus the
docker socket. `apply()` runs the sidecar DETACHED so it survives `compose up -d`
recreating the backend mid-update; progress is written to the node-data volume
(`/data/updater_status.json`), which the recreated backend reads back.

Global/host-level, NOT per-account (one repo, one running stack). Any authenticated
account may trigger it — like the other DooD singletons (nodeflow/mcp). "Version"
= the tracked branch commit (local HEAD vs remote HEAD).
"""
from __future__ import annotations

import asyncio
import json
import re
import time
from typing import Optional

from app.services import accounts

_NO_DOCKER = "__no_docker__"
UPDATER_CONTAINER = "node-installer-updater"
DEFAULT_IMAGE = "docker:cli"

_CONFIG_FILE = accounts.DATA_DIR / "updater.json"
_STATUS_FILE = accounts.DATA_DIR / "updater_status.json"

_BRANCH_RE = re.compile(r"^[A-Za-z0-9._/-]{1,120}$")

# 60s check cache (module-level; time.time is fine here — this is not a workflow).
_check_cache: dict = {"ts": 0.0, "data": None}


# ── config / status persistence (global) ──────────────────────
def load_config() -> dict:
    try:
        d = json.loads(_CONFIG_FILE.read_text(encoding="utf-8"))
        if isinstance(d, dict):
            return {"auto_update": bool(d.get("auto_update")),
                    "branch": str(d.get("branch") or ""),
                    "image": str(d.get("image") or DEFAULT_IMAGE)}
    except Exception:
        pass
    return {"auto_update": False, "branch": "", "image": DEFAULT_IMAGE}


def save_config(auto_update: bool, branch: str, image: str = "") -> dict:
    cfg = {
        "auto_update": bool(auto_update),
        "branch": _safe_branch(branch),
        "image": (image or DEFAULT_IMAGE).strip(),
    }
    _CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    return cfg


def read_status() -> Optional[dict]:
    try:
        return json.loads(_STATUS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return None


def _write_status(step: str, running: bool, ok) -> None:
    _STATUS_FILE.write_text(
        json.dumps({"step": step, "running": running, "ok": ok, "ts": int(time.time())}),
        encoding="utf-8",
    )


# ── pure helpers (unit-tested) ────────────────────────────────
def _safe_branch(branch: str) -> str:
    b = (branch or "").strip()
    return b if _BRANCH_RE.match(b) else ""


def is_behind(local: str, remote: str) -> bool:
    return bool(local and remote and local != remote)


def parse_check_output(raw: str) -> dict:
    """Parse the marker-delimited sidecar stdout into
    {local, remote, subject, branch}."""
    sections = {"LOCAL": "", "REMOTE": "", "SUBJECT": "", "BRANCH": ""}
    current = None
    for line in (raw or "").splitlines():
        m = re.match(r"^===(LOCAL|REMOTE|SUBJECT|BRANCH)===$", line.strip())
        if m:
            current = m.group(1)
            continue
        if current is not None:
            sections[current] += (line + "\n")
    return {
        "local": sections["LOCAL"].strip(),
        "remote": sections["REMOTE"].strip(),
        "subject": sections["SUBJECT"].strip(),
        "branch": sections["BRANCH"].strip(),
    }


def _check_script(branch: str) -> str:
    br = _safe_branch(branch)
    return (
        'apk add --no-cache git >/dev/null 2>&1 || true\n'
        'git config --global --add safe.directory /repo 2>/dev/null || true\n'
        f'BR="{br}"\n'
        'if [ -z "$BR" ]; then BR=$(git -C /repo rev-parse --abbrev-ref HEAD 2>/dev/null); fi\n'
        'git -C /repo fetch --quiet origin "$BR" 2>/dev/null || true\n'
        'echo "===LOCAL==="; git -C /repo rev-parse HEAD 2>/dev/null || true\n'
        'echo "===REMOTE==="; git -C /repo rev-parse FETCH_HEAD 2>/dev/null || true\n'
        'echo "===SUBJECT==="; git -C /repo log -1 --format=%s FETCH_HEAD 2>/dev/null || true\n'
        'echo "===BRANCH==="; echo "$BR"\n'
    )


def _apply_script(branch: str) -> str:
    br = _safe_branch(branch)
    return (
        'SF=/data/updater_status.json\n'
        'w(){ echo "{\\"step\\":\\"$1\\",\\"running\\":$2,\\"ok\\":$3,\\"ts\\":$(date +%s)}" > "$SF"; }\n'
        'w pull true null\n'
        'apk add --no-cache git >/dev/null 2>&1 || true\n'
        'git config --global --add safe.directory /repo 2>/dev/null || true\n'
        f'BR="{br}"\n'
        'if [ -z "$BR" ]; then BR=$(git -C /repo rev-parse --abbrev-ref HEAD); fi\n'
        'if ! git -C /repo pull --ff-only origin "$BR"; then w pull false false; exit 1; fi\n'
        'w build true null\n'
        'if ! docker compose -f /repo/docker-compose.yml build; then w build false false; exit 1; fi\n'
        'w up true null\n'
        'if ! docker compose -f /repo/docker-compose.yml up -d; then w up false false; exit 1; fi\n'
        'w done false true\n'
    )


def check_argv(image: str, projdir: str, branch: str) -> list[str]:
    return ["run", "--rm", "-v", f"{projdir}:/repo", "-w", "/repo",
            (image or DEFAULT_IMAGE), "sh", "-c", _check_script(branch)]


def apply_argv(image: str, projdir: str, data_volume: str, branch: str,
               container: str = UPDATER_CONTAINER) -> list[str]:
    return ["run", "-d", "--name", container,
            "-v", "/var/run/docker.sock:/var/run/docker.sock",
            "-v", f"{projdir}:/repo",
            "-v", f"{data_volume}:/data",
            "-w", "/repo",
            (image or DEFAULT_IMAGE), "sh", "-c", _apply_script(branch)]


# ── docker plumbing (DooD) ────────────────────────────────────
async def _docker(*args: str, timeout: int = 90) -> tuple[int, str]:
    try:
        proc = await asyncio.create_subprocess_exec(
            "docker", *args,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        )
    except FileNotFoundError:
        return 127, _NO_DOCKER
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        return 124, "docker timeout"
    return proc.returncode or 0, (out or b"").decode("utf-8", "replace")


async def _self_label(label: str) -> str:
    import socket
    self_id = socket.gethostname()
    rc, out = await _docker(
        "inspect", "-f", f'{{{{index .Config.Labels "{label}"}}}}', self_id, timeout=10)
    if rc == 127 and out == _NO_DOCKER:
        return _NO_DOCKER
    return out.strip() if rc == 0 else ""


async def project_dir() -> str:
    """Host path of the repo (compose project working_dir label). '' if unknown."""
    return await _self_label("com.docker.compose.project.working_dir")


async def _data_volume() -> str:
    import socket
    self_id = socket.gethostname()
    rc, out = await _docker(
        "inspect", "-f",
        '{{range .Mounts}}{{if eq .Destination "/app/data"}}{{.Name}}{{end}}{{end}}',
        self_id, timeout=10)
    return out.strip() if rc == 0 else ""


# ── orchestration ─────────────────────────────────────────────
async def check(force: bool = False) -> dict:
    """{docker, branch, local, remote, behind, subject, auto_update, error?}.
    60s cache. Docker/git absent → docker=false (never raises)."""
    cfg = load_config()
    now = time.time()
    if not force and _check_cache["data"] and now - _check_cache["ts"] < 60:
        return {**_check_cache["data"], "auto_update": cfg["auto_update"]}

    projdir = await project_dir()
    if projdir == _NO_DOCKER or not projdir:
        data = {"docker": False, "branch": cfg["branch"], "local": "", "remote": "",
                "behind": False, "subject": "",
                "error": "Docker CLI недоступен или контейнер не запущен через compose"}
        return {**data, "auto_update": cfg["auto_update"]}

    rc, out = await _docker(*check_argv(cfg["image"], projdir, cfg["branch"]), timeout=120)
    if rc == 127 and out == _NO_DOCKER:
        data = {"docker": False, "branch": cfg["branch"], "local": "", "remote": "",
                "behind": False, "subject": "", "error": "Docker CLI недоступен"}
        return {**data, "auto_update": cfg["auto_update"]}
    parsed = parse_check_output(out)
    data = {
        "docker": True,
        "branch": parsed["branch"] or cfg["branch"],
        "local": parsed["local"],
        "remote": parsed["remote"],
        "behind": is_behind(parsed["local"], parsed["remote"]),
        "subject": parsed["subject"],
    }
    _check_cache["ts"] = now
    _check_cache["data"] = data
    return {**data, "auto_update": cfg["auto_update"]}


async def apply() -> dict:
    """Launch the detached self-update sidecar. {ok} or {ok:False, warning}."""
    cfg = load_config()
    projdir = await project_dir()
    if projdir == _NO_DOCKER or not projdir:
        return {"ok": False, "warning": "Docker/compose недоступны — самообновление невозможно"}
    data_vol = await _data_volume()
    if not data_vol:
        return {"ok": False, "warning": "Не удалось определить том node-data для статуса обновления"}
    # Clear any previous updater container so --name doesn't collide.
    await _docker("rm", "-f", UPDATER_CONTAINER, timeout=15)
    _write_status("starting", True, None)
    rc, out = await _docker(*apply_argv(cfg["image"], projdir, data_vol, cfg["branch"]), timeout=60)
    if rc == 127 and out == _NO_DOCKER:
        return {"ok": False, "warning": "Docker CLI недоступен"}
    if rc != 0:
        _write_status("failed-start", False, False)
        return {"ok": False, "warning": f"Не удалось запустить обновление: {out.strip()[:200]}"}
    return {"ok": True}


async def auto_loop() -> None:
    """Every ~6h: if auto_update is on and the branch is behind, apply. Gated on
    the monitoring worker-lease (one process runs it), like the other loops."""
    from app.services import worker_lease
    interval = 6 * 3600
    while True:
        try:
            if not worker_lease.acquire(worker_lease.MONITORING):
                await asyncio.sleep(interval)
                continue
            cfg = load_config()
            if cfg["auto_update"]:
                st = await check(force=True)
                if st.get("docker") and st.get("behind"):
                    await apply()
        except Exception:
            pass  # never let the loop die
        await asyncio.sleep(interval)
