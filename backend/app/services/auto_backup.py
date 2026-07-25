"""Wave-8 §4 — scheduled per-account export shipped to Telegram.

The bot token is kept Fernet-encrypted in the account's settings (`auto_backup.
bot_token_enc`) and never returned to the client (only `has_token`). `run_once`
builds the FULL per-account export (secrets optional per `include_secrets`) and
ships it via `telegram.send_document`; `loop` fires it every `interval_hours`,
gated on the `monitoring` worker-lease so exactly one process runs it under
`--profile split`.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
import time
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken

from app.config import settings as app_settings
from app.models.settings import AppSettings, AutoBackupConfig
from app.services import accounts, export_service, storage, telegram, worker_lease

log = logging.getLogger("auto_backup")

_LOOP_INTERVAL = 900  # 15 min — how often we check; NOT the backup interval


# ── Fernet vault (shared key = SHA-256 of encryption_key) ─────
def _fernet() -> Fernet:
    digest = hashlib.sha256(app_settings.encryption_key.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_token(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt_token(enc: str) -> Optional[str]:
    if not enc:
        return None
    try:
        return _fernet().decrypt(enc.encode()).decode()
    except InvalidToken:
        return None


def _cfg(account_id: str) -> AutoBackupConfig:
    return AppSettings(**storage.load_settings(account_id)).auto_backup


def _save_state(account_id: str, *, last_run: Optional[int] = None,
                last_error: Optional[str] = None) -> None:
    raw = storage.load_settings(account_id)
    ab = raw.get("auto_backup")
    if not isinstance(ab, dict):
        ab = {}
        raw["auto_backup"] = ab
    if last_run is not None:
        ab["last_run"] = last_run
    if last_error is not None:
        ab["last_error"] = last_error
    storage.save_settings(raw, account_id)


async def run_once(account_id: str) -> dict:
    """Build the archive and ship it to Telegram. Records last_run/last_error.
    Returns {ok, error?}. Never raises; never logs the token."""
    cfg = _cfg(account_id)
    token = decrypt_token(cfg.bot_token_enc)
    if not token or not cfg.chat_id:
        _save_state(account_id, last_error="Не задан bot_token или chat_id")
        return {"ok": False, "error": "Не задан bot_token или chat_id"}
    now = int(time.time())
    try:
        blob = export_service.build_archive(account_id, include_secrets=cfg.include_secrets)
        fname = f"node-assistant-backup-{now}.tar.gz"
        caption = "Node Assistant backup" + (" (с секретами)" if cfg.include_secrets else "")
        res = await telegram.send_document(token, cfg.chat_id, fname, blob, caption)
        if res.get("ok"):
            _save_state(account_id, last_run=now, last_error="")
            return {"ok": True}
        _save_state(account_id, last_error=str(res.get("error") or "send failed"))
        return {"ok": False, "error": res.get("error")}
    except Exception as exc:
        msg = telegram.redact(str(exc), token)[:200]
        _save_state(account_id, last_error=msg)
        return {"ok": False, "error": msg}


async def loop() -> None:
    """Fires due backups every _LOOP_INTERVAL, gated on the monitoring lease.
    Per-account explicit account_id (no request context); one account's failure
    never kills the loop (mirrors user_stats.collector_loop)."""
    while True:
        try:
            if not worker_lease.acquire(worker_lease.MONITORING):
                await asyncio.sleep(_LOOP_INTERVAL)
                continue
            now = int(time.time())
            for acc in accounts.list_accounts():
                try:
                    cfg = _cfg(acc["id"])
                    if not cfg.enabled:
                        continue
                    if now >= cfg.last_run + cfg.interval_hours * 3600:
                        await run_once(acc["id"])
                except Exception as exc:
                    log.warning("auto_backup.account_failed: %s", str(exc)[:200])
        except Exception:
            pass  # never let the loop die
        await asyncio.sleep(_LOOP_INTERVAL)
