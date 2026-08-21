"""Durable, per-workspace storefront deployment correlation store."""
from __future__ import annotations

import json
import sqlite3
import time
import uuid
from contextlib import closing
from typing import Any

from app.services import accounts


class IdempotencyConflict(Exception):
    pass


class ExternalOrderConflict(Exception):
    pass


_SCHEMA = """
CREATE TABLE IF NOT EXISTS integration_deployments (
    deployment_id TEXT PRIMARY KEY,
    external_order_id TEXT NOT NULL UNIQUE,
    idempotency_key TEXT NOT NULL UNIQUE,
    request_hash TEXT NOT NULL,
    request_json TEXT NOT NULL,
    task_id TEXT,
    state TEXT NOT NULL,
    progress_step INTEGER NOT NULL DEFAULT 0,
    progress_total INTEGER NOT NULL DEFAULT 14,
    progress_label TEXT NOT NULL DEFAULT '',
    error_code TEXT,
    error_message TEXT,
    result_json TEXT,
    warnings_json TEXT NOT NULL DEFAULT '[]',
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    completed_at INTEGER
);
CREATE INDEX IF NOT EXISTS idx_integration_deployments_task
ON integration_deployments(task_id);
"""


def _path():
    account_id = accounts.current_account.get()
    if not account_id:
        raise RuntimeError("No active account")
    return accounts.data_dir(account_id) / "integrations.db"


def _connect() -> sqlite3.Connection:
    connection = sqlite3.connect(_path(), timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA busy_timeout=5000")
    connection.executescript(_SCHEMA)
    return connection


def _decode(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    value = dict(row)
    value["request"] = json.loads(value.pop("request_json"))
    value["result"] = json.loads(value.pop("result_json")) if value.get("result_json") else None
    value.pop("result_json", None)
    value["warnings"] = json.loads(value.pop("warnings_json") or "[]")
    value["progress"] = {
        "step": value.pop("progress_step"),
        "total": value.pop("progress_total"),
        "label": value.pop("progress_label"),
    }
    return value


def create_or_replay(*, idempotency_key: str, request_hash: str, request: dict) -> tuple[dict, bool]:
    now = int(time.time())
    deployment_id = str(uuid.uuid4())
    with closing(_connect()) as connection:
        try:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT * FROM integration_deployments WHERE idempotency_key=?",
                (idempotency_key,),
            ).fetchone()
            if existing:
                if existing["request_hash"] != request_hash:
                    raise IdempotencyConflict
                connection.commit()
                decoded = _decode(existing)
                assert decoded is not None
                return decoded, True
            if connection.execute(
                "SELECT 1 FROM integration_deployments WHERE external_order_id=?",
                (request["external_order_id"],),
            ).fetchone():
                raise ExternalOrderConflict
            connection.execute(
                "INSERT INTO integration_deployments "
                "(deployment_id, external_order_id, idempotency_key, request_hash, request_json, "
                "state, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
                (deployment_id, request["external_order_id"], idempotency_key, request_hash,
                 json.dumps(request, sort_keys=True, separators=(",", ":")), "submitting", now, now),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
    created = get(deployment_id)
    assert created is not None
    return created, False


def get(deployment_id: str) -> dict[str, Any] | None:
    with closing(_connect()) as connection:
        return _decode(connection.execute(
            "SELECT * FROM integration_deployments WHERE deployment_id=?", (deployment_id,)
        ).fetchone())


def update(deployment_id: str, **fields: Any) -> dict[str, Any]:
    allowed = {
        "task_id", "state", "progress_step", "progress_total", "progress_label",
        "error_code", "error_message", "completed_at",
    }
    values: dict[str, Any] = {key: value for key, value in fields.items() if key in allowed}
    if "result" in fields:
        values["result_json"] = json.dumps(fields["result"], sort_keys=True)
    if "warnings" in fields:
        values["warnings_json"] = json.dumps(fields["warnings"])
    values["updated_at"] = int(time.time())
    assignments = ", ".join(f"{key}=?" for key in values)
    with closing(_connect()) as connection:
        connection.execute(
            f"UPDATE integration_deployments SET {assignments} WHERE deployment_id=?",
            (*values.values(), deployment_id),
        )
        connection.commit()
    updated = get(deployment_id)
    assert updated is not None
    return updated
