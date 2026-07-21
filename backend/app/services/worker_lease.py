"""
Cooperative single-owner leases for background duties.

This is what makes the Plan-M service split OPTIONAL and REVERSIBLE. Every
background duty (the monitoring loops, the deploy job runner) is wrapped in a
lease: whichever process holds it does the work, everyone else idles. So:

  * `docker compose up` (monolith)          → the one backend holds every lease,
                                              behaviour identical to before.
  * `--profile split`                        → the dedicated worker containers grab
                                              the leases first and keep renewing
                                              them; the gateway's copies idle.
  * a split worker dies                      → its lease expires within TTL and the
                                              gateway silently resumes the duty.

That last line is the plan's rollback criterion: no extracted service is ever a
hard dependency.

FAIL-OPEN by design: if the lease table cannot be reached the caller is told it
holds the lease. A monolith with a broken DB must keep monitoring, not go quiet.
"""
from __future__ import annotations

import logging
import os
import socket
import time

from app.services import shared_task_store as sts

log = logging.getLogger(__name__)

# Duty names
MONITORING = "monitoring"
DEPLOY_WORKER = "deploy-worker"

DEFAULT_TTL = 180           # a duty is up for grabs this long after the last renew

_HOLDER = f"{socket.gethostname()}:{os.getpid()}"


def holder_id() -> str:
    return _HOLDER


def role() -> str:
    """This process's declared role — `gateway` (default) or a duty name. Read
    live rather than at import so `app/worker.py` can set it before the loops
    start."""
    return os.getenv("SERVICE_ROLE", "gateway").strip().lower()


def is_dedicated(name: str) -> bool:
    """True when this process was started specifically to perform `name`."""
    return role() == name


def acquire(name: str, ttl: int = DEFAULT_TTL) -> bool:
    """Take or renew `name`. True when this process owns the duty for `ttl` more
    seconds. Renewing is just calling this again on every tick.

    A process DEDICATED to the duty (`SERVICE_ROLE=<name>`, i.e. the split worker
    container) takes the lease unconditionally, so it wins even when the gateway
    booted first and grabbed it. Everyone else only takes a free or expired one.
    That is a deliberate steal, not a bug — there is exactly one container per
    duty, and it makes the split converge without any boot-order timing games.
    """
    now = int(time.time())
    try:
        if is_dedicated(name):
            sts._exec(
                "INSERT INTO leases (name, holder, expires_at) VALUES (?,?,?) "
                "ON CONFLICT(name) DO UPDATE SET holder=excluded.holder, "
                "expires_at=excluded.expires_at",
                (name, _HOLDER, now + ttl),
            )
            return True
        cur = sts._exec(
            "INSERT INTO leases (name, holder, expires_at) VALUES (?,?,?) "
            "ON CONFLICT(name) DO UPDATE SET holder=excluded.holder, expires_at=excluded.expires_at "
            "WHERE leases.holder=excluded.holder OR leases.expires_at < ?",
            (name, _HOLDER, now + ttl, now),
        )
        return cur.rowcount == 1
    except Exception:
        log.exception("worker_lease.acquire_failed name=%s", name)
        return True          # fail-open: better to double-run than to go silent


def release(name: str) -> None:
    try:
        sts._exec("DELETE FROM leases WHERE name=? AND holder=?", (name, _HOLDER))
    except Exception:
        log.exception("worker_lease.release_failed name=%s", name)


def status(name: str) -> dict:
    """Who holds `name` and is it fresh — for `/api/health`."""
    now = int(time.time())
    try:
        r = sts._one("SELECT holder, expires_at FROM leases WHERE name=?", (name,))
    except Exception:
        return {"name": name, "holder": None, "fresh": False, "self": False}
    if r is None:
        return {"name": name, "holder": None, "fresh": False, "self": False}
    return {
        "name": name,
        "holder": r["holder"],
        "fresh": int(r["expires_at"]) > now,
        "self": r["holder"] == _HOLDER,
    }


def held_elsewhere(name: str) -> bool:
    """True when ANOTHER live process currently owns the duty — the gateway uses
    this to decide whether to hand work off instead of doing it itself."""
    st = status(name)
    return bool(st["fresh"] and not st["self"])
