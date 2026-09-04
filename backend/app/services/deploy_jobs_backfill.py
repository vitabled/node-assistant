"""Idempotent recovery of server-side deployment cards from shared task rows."""
from __future__ import annotations

import sqlite3
from typing import Any

from app.api import deploy_jobs
from app.models.deploy import DeployRequest
from app.services import accounts, shared_task_store


def create_deploy_job(req: DeployRequest, task_id: str, started_at: float | None = None):
    """Compatibility wrapper used by the backfill and its focused tests."""
    return deploy_jobs.create_server_job(req, task_id, started_at)


def list_deploy_jobs(account_id: str) -> list[dict[str, Any]]:
    return deploy_jobs.list_server_jobs(account_id)


def _final_status(status: str) -> str:
    return status if status in {"success", "failed", "canceled"} else "running"


def backfill_deploy_jobs() -> int:
    """Create missing cards for decryptable deploy tasks, grouped by account.

    SharedTask.finish intentionally clears ``payload_enc`` at terminal state, so
    rows whose payload has already been wiped cannot be reconstructed safely and
    are skipped. The operation is safe to run repeatedly: task_id is the key.
    """
    path = shared_task_store.db_path()
    if not path.exists():
        return 0
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT task_id, account_id, status, created_at, payload_enc "
            "FROM tasks WHERE kind='deploy' AND payload_enc IS NOT NULL"
        ).fetchall()
    finally:
        conn.close()

    created = 0
    for row in rows:
        account_id = str(row["account_id"] or "")
        if not account_id:
            continue
        existing_ids = {job["taskId"] for job in list_deploy_jobs(account_id)}
        if row["task_id"] in existing_ids:
            continue
        payload: Any = shared_task_store._decrypt(row["payload_enc"])
        if not isinstance(payload, dict):
            continue
        try:
            req = DeployRequest(**payload)
        except Exception:
            continue
        token = accounts.current_account.set(account_id)
        try:
            job = create_deploy_job(req, row["task_id"], float(row["created_at"]) * 1000)
            if _final_status(str(row["status"])) != "running":
                deploy_jobs.update_server_job_status(job.taskId, _final_status(str(row["status"])))
        finally:
            accounts.current_account.reset(token)
        created += 1
    return created


def main() -> None:
    print(f"Created {backfill_deploy_jobs()} deployment job cards.")


if __name__ == "__main__":
    main()