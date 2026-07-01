"""
Local metadata store for infra-billing (SQLite, stdlib — no new pip dep).

Remnawave's InfraBillingController stores only identity/links/history amounts. It
has NO provider balance, currency, low-balance threshold, or per-node monthly
cost — but the UI needs those (and burn-rate depends on them). We keep that
metadata here, keyed by the Remnawave provider/billing-node uuid, and merge it
into API responses.

SECURITY: we deliberately do NOT persist provider API tokens here — this project
forbids storing third-party secrets at rest (same rule as SSH/Cloudflare creds).
Only non-secret financial metadata is stored.
"""
from __future__ import annotations

import asyncio
import os
import sqlite3
from pathlib import Path
from typing import Any, Optional

DATA_DIR = Path(os.getenv("DATA_DIR", "/app/data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
_DB = DATA_DIR / "infra_billing.db"


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_DB, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def _init() -> None:
    with _connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS provider_meta (
                provider_uuid        TEXT PRIMARY KEY,
                balance              REAL DEFAULT 0,
                currency             TEXT DEFAULT 'RUB',
                low_balance_threshold REAL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS node_meta (
                billing_node_uuid TEXT PRIMARY KEY,
                monthly_cost      REAL DEFAULT 0
            );
            """
        )


_init()  # idempotent migration on import


# ── Provider metadata ─────────────────────────────────────────

def _get_provider_meta_all() -> dict[str, dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute("SELECT * FROM provider_meta").fetchall()
    return {
        r["provider_uuid"]: {
            "balance": r["balance"],
            "currency": r["currency"],
            "low_balance_threshold": r["low_balance_threshold"],
        } for r in rows
    }


def _upsert_provider_meta(uuid: str, *, balance: Optional[float],
                          currency: Optional[str], threshold: Optional[float]) -> None:
    with _connect() as conn:
        cur = conn.execute("SELECT 1 FROM provider_meta WHERE provider_uuid = ?", (uuid,))
        exists = cur.fetchone() is not None
        if not exists:
            conn.execute(
                "INSERT INTO provider_meta (provider_uuid, balance, currency, low_balance_threshold) "
                "VALUES (?, ?, ?, ?)",
                (uuid, balance or 0, currency or "RUB", threshold or 0),
            )
        else:
            sets, args = [], []
            if balance is not None:   sets.append("balance = ?");               args.append(balance)
            if currency is not None:  sets.append("currency = ?");              args.append(currency)
            if threshold is not None: sets.append("low_balance_threshold = ?"); args.append(threshold)
            if sets:
                args.append(uuid)
                conn.execute(f"UPDATE provider_meta SET {', '.join(sets)} WHERE provider_uuid = ?", args)


def _delete_provider_meta(uuid: str) -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM provider_meta WHERE provider_uuid = ?", (uuid,))


# ── Billing-node metadata (monthly cost) ──────────────────────

def _get_node_meta_all() -> dict[str, float]:
    with _connect() as conn:
        rows = conn.execute("SELECT * FROM node_meta").fetchall()
    return {r["billing_node_uuid"]: r["monthly_cost"] for r in rows}


def _set_node_cost(uuid: str, monthly_cost: float) -> None:
    with _connect() as conn:
        conn.execute(
            "INSERT INTO node_meta (billing_node_uuid, monthly_cost) VALUES (?, ?) "
            "ON CONFLICT(billing_node_uuid) DO UPDATE SET monthly_cost = excluded.monthly_cost",
            (uuid, monthly_cost),
        )


def _delete_node_meta(uuid: str) -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM node_meta WHERE billing_node_uuid = ?", (uuid,))


# ── Async wrappers ────────────────────────────────────────────

async def provider_meta_all() -> dict[str, dict[str, Any]]:
    return await asyncio.to_thread(_get_provider_meta_all)

async def upsert_provider_meta(uuid: str, *, balance=None, currency=None, threshold=None) -> None:
    await asyncio.to_thread(_upsert_provider_meta, uuid, balance=balance, currency=currency, threshold=threshold)

async def delete_provider_meta(uuid: str) -> None:
    await asyncio.to_thread(_delete_provider_meta, uuid)

async def node_meta_all() -> dict[str, float]:
    return await asyncio.to_thread(_get_node_meta_all)

async def set_node_cost(uuid: str, monthly_cost: float) -> None:
    await asyncio.to_thread(_set_node_cost, uuid, monthly_cost)

async def delete_node_meta(uuid: str) -> None:
    await asyncio.to_thread(_delete_node_meta, uuid)
