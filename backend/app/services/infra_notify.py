"""
Low-balance notification hook (STUB).

This is the single integration point for future alerting (e.g. a Telegram bot):
`notify_low_balance()` currently only logs a warning. To wire a real bot later,
implement the send here — no callers need to change.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("infra_billing.notify")


async def notify_low_balance(provider: dict[str, Any], balance: float,
                             threshold: float, currency: str) -> None:
    """Called when a provider's balance drops below its threshold.

    STUB: logs a warning. Replace the body with a bot send (Telegram, etc.)
    to enable real notifications.
    """
    logger.warning(
        "[infra-billing] LOW BALANCE: provider '%s' balance=%.2f %s < threshold=%.2f %s",
        provider.get("name", "?"), balance, currency, threshold, currency,
    )
    # TODO(bot): await bot.send(chat_id, f"⚠️ Баланс {provider['name']}: {balance} {currency}")


async def check_low_balances(providers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Check merged provider dicts (with balance/threshold/currency) and fire the
    hook for each breach. Returns the list of providers currently in alert."""
    alerts: list[dict[str, Any]] = []
    for p in providers:
        threshold = p.get("lowBalanceThreshold") or 0
        balance = p.get("balance") or 0
        if threshold > 0 and balance < threshold:
            await notify_low_balance(p, balance, threshold, p.get("currency", ""))
            alerts.append(p)
    return alerts
