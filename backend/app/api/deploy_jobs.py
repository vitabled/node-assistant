"""Per-account encrypted persistence for deployment dashboard cards.

The card's ``savedForm`` carries SSH and panel credentials.  It is serialised as
one JSON blob and encrypted with the existing Fernet vault helper before this
module writes the account's ``deploy_jobs.json`` file.
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator

from app.services import accounts, vault_store

router = APIRouter(prefix="/api/deploy-jobs")

_LOCK = threading.Lock()


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
    for record in _read():
        if isinstance(record, dict):
            job = _decrypt_job(record)
            if job is not None:
                jobs.append(job.model_dump(exclude_none=True))
    return jobs


@router.get("")
async def list_jobs() -> dict[str, list[dict[str, Any]]]:
    """Return this account's cards; malformed/encryption-broken rows are skipped."""
    with _LOCK:
        return {"jobs": _jobs_for_response()}


@router.put("")
async def replace_jobs(body: DeployJobsBody) -> dict[str, list[dict[str, Any]]]:
    """Replace all cards, including migration/synchronisation from local storage."""
    with _LOCK:
        _write([_encrypt_job(job) for job in body.jobs])
    return {"jobs": [job.model_dump(exclude_none=True) for job in body.jobs]}


@router.post("")
async def upsert_job(job: DeployJob) -> dict[str, dict[str, Any]]:
    """Create or replace the card identified by its deployment task id."""
    with _LOCK:
        records = _read()
        records = [record for record in records if record.get("taskId") != job.taskId]
        records.append(_encrypt_job(job))
        _write(records)
    return {"job": job.model_dump(exclude_none=True)}


@router.delete("/{task_id}")
async def delete_job(task_id: str) -> dict[str, bool]:
    with _LOCK:
        records = _read()
        kept = [record for record in records if record.get("taskId") != task_id]
        if len(kept) == len(records):
            raise HTTPException(404, "Карточка деплоя не найдена")
        _write(kept)
    return {"ok": True}
