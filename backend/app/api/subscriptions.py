"""Per-account subscription tracking + the aggregator source endpoint.

Each account owns a set of subscriptions (JSON store). The shared subs-aggregator
container merges the ACTIVE set (background subs + Ф9's transient selection) into
one combined subscription the shared xray-checker probes.

Two routers:
- `router` (/api/subscriptions, session-gated) — per-account CRUD + refresh.
- `internal_router` (/internal/agg-subs, NOT gated) — the aggregator polls it for
  the cross-account active set. Only reachable on node-assistant-net (compose
  `expose`, not `ports`; nginx does not proxy /internal), so it stays internal.
"""
import os
import threading
import uuid
import urllib.request

from fastapi import APIRouter, Header, HTTPException

from app.models.subscriptions import SubscriptionCreate, SubscriptionUpdate
from app.services import storage, accounts

router = APIRouter(prefix="/api/subscriptions")
internal_router = APIRouter(prefix="/internal")

_AGG_REFRESH_URL = os.getenv("SUBS_AGGREGATOR_REFRESH_URL", "http://subs-aggregator:8080/refresh")
_AGG_TOKEN = os.getenv("AGG_TOKEN", "").strip()  # shared secret with the aggregator


# ── per-account CRUD (session-gated) ──────────────────────────

@router.get("")
async def list_subscriptions():
    return storage.load_subscriptions()


@router.post("", status_code=201)
async def create_subscription(body: SubscriptionCreate):
    subs = storage.load_subscriptions()
    sub = {
        "id": uuid.uuid4().hex[:12],
        "url": body.url.strip(),
        "background": body.background,
        "enabled": True,
        "last_error": None,
    }
    subs.append(sub)
    storage.save_subscriptions(subs)
    # No notify: a new sub isn't cached yet — the aggregator re-reads the source
    # list on the next /sub and fetches it fresh.
    return sub


@router.patch("/{sub_id}")
async def update_subscription(sub_id: str, body: SubscriptionUpdate):
    subs = storage.load_subscriptions()
    found = next((s for s in subs if s["id"] == sub_id), None)
    if not found:
        raise HTTPException(404, "Подписка не найдена")
    url_changed = body.url is not None and body.url.strip() != found["url"]
    if body.url is not None:
        found["url"] = body.url.strip()
    if body.background is not None:
        found["background"] = body.background
    if body.enabled is not None:
        found["enabled"] = body.enabled
    storage.save_subscriptions(subs)
    # Only a URL change makes the per-sub CONFIG cache stale; background/enabled
    # toggles just change the source set (re-read every /sub), no notify needed.
    if url_changed:
        _notify_aggregator(_sub_key(sub_id))
    return found


@router.delete("/{sub_id}", status_code=204)
async def delete_subscription(sub_id: str):
    subs = storage.load_subscriptions()
    storage.save_subscriptions([s for s in subs if s["id"] != sub_id])
    # No notify: the sub drops out of the source set → not aggregated; its stale
    # cache entry is simply never used again.


@router.post("/{sub_id}/refresh")
async def refresh_subscription(sub_id: str):
    """Force the aggregator to re-fetch this subscription (clears its no-retry
    error state). This is the ONLY way a failed upstream gets retried."""
    _notify_aggregator(_sub_key(sub_id))
    return {"ok": True}


# ── aggregator source (internal, NOT session-gated) ───────────

@internal_router.get("/agg-subs")
async def agg_subs(x_agg_token: str = Header(default="")):
    """The active subscription set across ALL accounts, for the aggregator:
    [{account_id, sub_id, url}]. Includes background+enabled subs (transient
    selection is layered on in Ф9). Not account-gated (the aggregator has no
    account token) — reachable only on node-assistant-net, and hardened with a
    shared AGG_TOKEN header when set (defense-in-depth vs. other containers on
    the net, e.g. the pulled-latest checker image)."""
    if _AGG_TOKEN and x_agg_token != _AGG_TOKEN:
        raise HTTPException(403, "forbidden")
    out = []
    for acc in accounts.list_accounts():
        aid = acc["id"]
        try:
            subs = storage.load_subscriptions(aid)
        except Exception:
            continue
        for s in subs:
            if s.get("enabled", True) and s.get("background") and s.get("url"):
                out.append({"account_id": aid, "sub_id": s["id"], "url": s["url"]})
    return out


# ── helpers ───────────────────────────────────────────────────

def _sub_key(sub_id: str) -> str:
    """The aggregator caches by `account:sub`; the active account is in context."""
    aid = accounts.current_account.get() or ""
    return f"{aid}:{sub_id}"


def _post_refresh(sub_key) -> None:
    try:
        data = b"{}" if sub_key is None else ('{"sub_key": "%s"}' % sub_key).encode()
        headers = {"Content-Type": "application/json"}
        if _AGG_TOKEN:
            headers["X-Agg-Token"] = _AGG_TOKEN
        req = urllib.request.Request(_AGG_REFRESH_URL, data=data, headers=headers, method="POST")
        urllib.request.urlopen(req, timeout=3).read()
    except Exception:
        pass


def _notify_aggregator(sub_key) -> None:
    """Best-effort, FIRE-AND-FORGET (detached daemon thread): tell the aggregator
    to drop a cached upstream so its next /sub re-fetches. Fully decoupled from
    the request loop — the CRUD response never waits on the aggregator being
    reachable. Non-fatal on any error."""
    threading.Thread(target=_post_refresh, args=(sub_key,), daemon=True).start()
