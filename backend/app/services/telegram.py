"""
Minimal Telegram Bot API sender for rule actions.

The bot-token is a secret and MUST NEVER reach the logs: `redact()` scrubs both
the well-known bot-token shape and any explicit secret substrings, and every log
line here routes error text through it. Send failures are swallowed (logged, not
raised) — a dead Telegram must never break the rules loop or a webhook response.
"""

from __future__ import annotations

import logging
import re
from typing import Any

import httpx

log = logging.getLogger("rules.telegram")

# Telegram bot-token shape: <numeric id>:<35-char base64url secret>.
_TOKEN_RE = re.compile(r"\b\d{6,}:[A-Za-z0-9_-]{30,}\b")

_TIMEOUT = 10


def redact(text: str, *extra: str) -> str:
    """Mask bot-tokens (and any explicit `extra` secrets) in a string for logging."""
    out = _TOKEN_RE.sub("«redacted-token»", text or "")
    for secret in extra:
        if secret:
            out = out.replace(secret, "«redacted»")
    return out


async def send_message(bot_token: str, chat_id: str, text: str) -> dict[str, Any]:
    """POST to sendMessage. Returns {ok: bool, ...}; never raises, never logs the
    token. `bot_token` is interpolated into the URL only — nothing that includes
    it is logged."""
    if not bot_token or not chat_id:
        return {"ok": False, "error": "missing bot_token or chat_id"}
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(url, json={"chat_id": chat_id, "text": text})
        if resp.status_code >= 400:
            # Telegram error bodies never echo the token, but redact defensively.
            detail = redact(resp.text[:300], bot_token)
            log.warning("telegram send failed: HTTP %s %s", resp.status_code, detail)
            return {"ok": False, "error": f"HTTP {resp.status_code}"}
        return {"ok": True}
    except Exception as exc:
        log.warning("telegram send error: %s", redact(str(exc), bot_token))
        return {"ok": False, "error": redact(str(exc), bot_token)}
