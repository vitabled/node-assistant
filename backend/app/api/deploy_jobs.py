"""Per-account encrypted persistence for deployment dashboard cards.

The card's ``savedForm`` carries SSH and panel credentials.  It is serialised as
one JSON blob and encrypted with the existing Fernet vault helper before this
module writes the account's ``deploy_jobs.json`` file.
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, Field, field_validator

from app.services import accounts, vault_store

router = APIRouter(prefix="/api/deploy-jobs")


def _no_store(response: Response) -> None:
    """Never let a browser/proxy cache hand out a stale card list.

    The server is the only source of truth for the cards, so every reply here is
    marked uncacheable — otherwise an old cached response could still decide what
    the UI shows.
    """
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"

# RLock: the tombstone helpers are called from inside `upsert_server_job`/
# `delete_server_job` (which already hold the lock) — reentrancy keeps the
# read-modify-write whole instead of two unsynchronised passes.
_LOCK = threading.RLock()

# Deleted taskIds, remembered so a browser cache cannot resurrect a card:
# the GET list hides them, POST upsert refuses to recreate them, and the client
# purges them from localStorage. Stored next to the cards.
_TOMBSTONES_FILE_NAME = "deploy_jobs_tombstones.json"


class DeployJob(BaseModel):
    taskId: str = Field(..., min_length=1)
    domain: str
    ip: str
    newSshPort: int = Field(..., ge=1, le=65535)
    startedAt: float
    savedForm: dict[str, Any]
    finalStatus: str | None = None
    color: str | None = None

    @field_validator("taskId")
    @classmethod
    def task_id_must_not_be_whitespace(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("taskId must not be blank")
        return value


class DeployJobsBody(BaseModel):
    jobs: list[DeployJob]


def _path() -> Path:
    account_id = accounts.current_account.get()
    if not account_id:
        raise RuntimeError("No active account in context")
    return accounts.account_dir(account_id) / "deploy_jobs.json"


def _read() -> list[dict[str, Any]]:
    try:
        path = _path()
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("jobs"), list):
                return data["jobs"]
    except Exception:
        pass
    return []


def _write(jobs: list[dict[str, Any]]) -> None:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps({"jobs": jobs}, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    os.replace(tmp, path)


# ── Tombstones ────────────────────────────────────────────────────────────────
# A card deleted on the server but still present in a browser cache would be
# re-uploaded by the additive sync (deployJobsSync) and "resurrect". The tombstone
# list — the set of taskIds deleted on purpose — closes that loop. These helpers
# do NOT take the lock themselves; callers hold `_LOCK` (RLock).

def _tombstones_path() -> Path:
    account_id = accounts.current_account.get()
    if not account_id:
        raise RuntimeError("No active account in context")
    return accounts.account_dir(account_id) / _TOMBSTONES_FILE_NAME


def _read_tombstones() -> list[str]:
    try:
        path = _tombstones_path()
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("taskIds"), list):
                return [t for t in data["taskIds"] if isinstance(t, str)]
    except Exception:
        pass
    return []


def _write_tombstones(task_ids: list[str]) -> None:
    path = _tombstones_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps({"taskIds": task_ids}, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    os.replace(tmp, path)


def _add_tombstone(task_id: str) -> None:
    with _LOCK:
        task_ids = _read_tombstones()
        if task_id not in task_ids:
            task_ids.append(task_id)
            _write_tombstones(task_ids)


def clear_tombstone(task_id: str) -> None:
    """Forget a deletion so the card may be created again (explicit re-add)."""
    with _LOCK:
        _write_tombstones([t for t in _read_tombstones() if t != task_id])


def list_tombstones() -> list[str]:
    with _LOCK:
        return _read_tombstones()


def is_tombstoned(task_id: str) -> bool:
    with _LOCK:
        return task_id in set(_read_tombstones())


def _encrypt_job(job: DeployJob) -> dict[str, Any]:
    record = job.model_dump(exclude_none=True)
    record["savedForm_enc"] = vault_store._encrypt(
        json.dumps(record.pop("savedForm"), ensure_ascii=False)
    )
    return record


def _decrypt_job(record: dict[str, Any]) -> DeployJob | None:
    ciphertext = record.get("savedForm_enc")
    if not isinstance(ciphertext, str):
        return None
    plaintext = vault_store._decrypt(ciphertext)
    if plaintext is None:
        return None
    try:
        saved_form = json.loads(plaintext)
        if not isinstance(saved_form, dict):
            return None
        return DeployJob.model_validate({**record, "savedForm": saved_form})
    except (TypeError, ValueError):
        return None


def _jobs_for_response() -> list[dict[str, Any]]:
    jobs: list[dict[str, Any]] = []
    tombstones = set(_read_tombstones())
    for record in _read():
        if isinstance(record, dict):
            if record.get("taskId") in tombstones:
                continue  # deleted on purpose — never handed back to a cache
            job = _decrypt_job(record)
            if job is not None:
                jobs.append(job.model_dump(exclude_none=True))
    return jobs


def create_server_job(req: Any, task_id: str, started_at: float | None = None) -> DeployJob:
    """Persist the server-owned card before a deployment is handed to a worker."""
    saved_form = req.model_dump(mode="json")
    job = DeployJob(
        taskId=task_id,
        domain=req.domain or "",
        ip=req.ip,
        newSshPort=req.new_ssh_port or req.current_ssh_port or 22,
        startedAt=started_at if started_at is not None else time.time() * 1000,
        savedForm=saved_form,
        finalStatus="running",
    )
    upsert_server_job(job)
    return job


def upsert_server_job(job: DeployJob, *, respect_tombstone: bool = True) -> bool:
    """Synchronous storage primitive shared by HTTP, deploy and backfill paths.

    Returns False when the write was refused because the taskId is tombstoned
    (a card deleted on purpose must not be resurrected by a stale client). The
    synchronisation path clears the tombstone first and writes with
    `respect_tombstone=False` — an explicit re-add is allowed.
    """
    with _LOCK:
        if respect_tombstone and job.taskId in set(_read_tombstones()):
            return False
        records = _read()
        records = [record for record in records if record.get("taskId") != job.taskId]
        records.append(_encrypt_job(job))
        _write(records)
        return True


def delete_server_job(task_id: str) -> bool:
    """Delete a card AND remember the taskId as a tombstone. Shared by the
    explicit UI delete and the Remnawave sync removal."""
    with _LOCK:
        records = _read()
        kept = [record for record in records if record.get("taskId") != task_id]
        if len(kept) == len(records):
            return False
        _write(kept)
        _add_tombstone(task_id)
        return True


def ensure_server_job(req: Any, task_id: str, started_at: float | None = None) -> DeployJob | None:
    """Create a card only when a worker receives a task from a legacy path."""
    with _LOCK:
        for record in _read():
            if isinstance(record, dict) and record.get("taskId") == task_id:
                return _decrypt_job(record)
    return create_server_job(req, task_id, started_at)


def update_server_job_status(task_id: str, final_status: str) -> None:
    """Set a terminal verdict without changing the card's start time or form."""
    with _LOCK:
        records = _read()
        for index, record in enumerate(records):
            if not isinstance(record, dict) or record.get("taskId") != task_id:
                continue
            job = _decrypt_job(record)
            if job is None:
                return
            records[index] = _encrypt_job(job.model_copy(update={"finalStatus": final_status}))
            _write(records)
            return


def list_server_jobs(account_id: str) -> list[dict[str, Any]]:
    """Read decrypted cards for one account for maintenance/backfill callers."""
    token = accounts.current_account.set(account_id)
    try:
        with _LOCK:
            return _jobs_for_response()
    finally:
        accounts.current_account.reset(token)


@router.get("")
async def list_jobs(response: Response) -> dict[str, list[dict[str, Any]]]:
    """Return this account's cards; malformed/encryption-broken rows are skipped.

    Ответ помечен no-store: список карточек — единственный источник правды на
    сервере, поэтому ни HTTP-кеш браузера, ни промежуточный прокси не должны
    отдавать его старую копию.
    """
    _no_store(response)
    with _LOCK:
        return {"jobs": _jobs_for_response()}


@router.get("/tombstones")
async def get_tombstones(response: Response) -> dict[str, list[str]]:
    """taskIds deleted on purpose. The client drops these from its local cache so
    a deleted card cannot be uploaded back (the additive-sync resurrection)."""
    _no_store(response)
    return {"taskIds": list_tombstones()}


@router.put("")
async def replace_jobs(body: DeployJobsBody, response: Response) -> dict[str, list[dict[str, Any]]]:
    """Replace all cards, including migration/synchronisation from local storage."""
    _no_store(response)
    with _LOCK:
        _write([_encrypt_job(job) for job in body.jobs])
    return {"jobs": [job.model_dump(exclude_none=True) for job in body.jobs]}


@router.post("")
async def upsert_job(job: DeployJob, response: Response) -> dict[str, Any]:
    """Create or replace the card identified by its deployment task id.

    A tombstoned taskId is NOT recreated: the response carries `ignored: true`
    so the client knows the write was a no-op (it is the stale-cache case)."""
    _no_store(response)
    if not upsert_server_job(job):
        return {"job": job.model_dump(exclude_none=True), "ignored": True}
    return {"job": job.model_dump(exclude_none=True)}


@router.delete("/{task_id}")
async def delete_job(task_id: str, response: Response) -> dict[str, bool]:
    _no_store(response)
    if not delete_server_job(task_id):
        raise HTTPException(404, "Карточка деплоя не найдена")
    return {"ok": True}
