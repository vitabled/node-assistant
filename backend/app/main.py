import asyncio
import contextlib
import sys

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.config import settings
from app.api import (
    auth,
    deploy,
    certs,
    ws,
    stats,
    settings as settings_router,
    traffic_rules,
    xray_checker,
    infra_billing,
    node_ops,
    subscriptions,
    domains,
    hosts,
    user_stats,
    testservers,
    panel_deploy,
    panel_metrics,
    backup,
    subpages,
    speedtest,
    rules,
    mcp,
    ai,
    panel_sync,
    migrate,
    server_monitor,
    hostings,
    replace_domain,
    certwarden,
    netbird,
    api_tokens,
    config_templates,
    ai_prompts,
    export_io,
    library,
)
from app.api.auth import require_account


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    # Background workers:
    #  - poller: samples xray-checker into the SQLite metrics store (24h graphs).
    #  - collector: snapshots Remnawave per-node usersOnline into the user-stats
    #    store (node-load history + best-effort migrations).
    # Both skip work when their source is unconfigured/off.
    #  - rules: evaluates xray_down/cron rules per-account and runs their actions
    #    (telegram / hide-hosts / disable node|user); webhook rules run in the
    #    receiver, not here.
    #  - autostart: on boot, start the shared xray-checker if any account has it
    #    enabled (monitoring is on by default now) and Docker is available.
    poller = asyncio.create_task(xray_checker.poller_loop())
    collector = asyncio.create_task(user_stats.collector_loop())
    rules_task = asyncio.create_task(rules.rules_loop())
    autostart = asyncio.create_task(xray_checker.autostart_checker())
    #  - server monitor: probes each account's tracked servers by IP (TCP/ICMP)
    #    for the «Server uptime» dashboard tab.
    srv_monitor = asyncio.create_task(server_monitor.monitor_loop())
    tasks = (poller, collector, rules_task, autostart, srv_monitor)
    try:
        yield
    finally:
        for t in tasks:
            t.cancel()
        for t in tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await t


# The encryption key signs session JWTs AND derives the infra-billing vault key.
# On the shipped default, anyone can forge a token for any account — refuse to be
# silent about it.
if settings.encryption_key == "dev_key_change_in_production_000":
    print(
        "\033[1;31m[SECURITY] ENCRYPTION_KEY is the insecure default — set a strong "
        "ENCRYPTION_KEY in .env before exposing this panel (it signs auth tokens).\033[0m",
        file=sys.stderr,
    )

app = FastAPI(title="Node Installer", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.cors_origin, "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Auth routes are public (register/login); everything else requires a valid
# account token. `require_account` also publishes the active account on the
# `current_account` ContextVar, which the storage layers read for isolation.
app.include_router(auth.router)

_auth = [Depends(require_account)]
app.include_router(deploy.router, dependencies=_auth)
app.include_router(certs.router, dependencies=_auth)
app.include_router(stats.router, dependencies=_auth)
app.include_router(node_ops.router, dependencies=_auth)
app.include_router(settings_router.router, dependencies=_auth)
app.include_router(traffic_rules.router, dependencies=_auth)
app.include_router(xray_checker.router, dependencies=_auth)
app.include_router(infra_billing.router, dependencies=_auth)
app.include_router(subscriptions.router, dependencies=_auth)
app.include_router(domains.router, dependencies=_auth)
app.include_router(hosts.router, dependencies=_auth)
app.include_router(user_stats.router, dependencies=_auth)
app.include_router(testservers.router, dependencies=_auth)
app.include_router(panel_deploy.router, dependencies=_auth)
app.include_router(panel_metrics.router, dependencies=_auth)
app.include_router(backup.router, dependencies=_auth)
app.include_router(subpages.router, dependencies=_auth)
app.include_router(speedtest.router, dependencies=_auth)
app.include_router(rules.router, dependencies=_auth)
app.include_router(mcp.router, dependencies=_auth)
app.include_router(ai.router, dependencies=_auth)
app.include_router(panel_sync.router, dependencies=_auth)
app.include_router(migrate.router, dependencies=_auth)
app.include_router(server_monitor.router, dependencies=_auth)
app.include_router(hostings.router, dependencies=_auth)
app.include_router(replace_domain.router, dependencies=_auth)
app.include_router(certwarden.router, dependencies=_auth)
app.include_router(netbird.router, dependencies=_auth)
app.include_router(api_tokens.router, dependencies=_auth)
app.include_router(config_templates.router, dependencies=_auth)
app.include_router(ai_prompts.router, dependencies=_auth)
app.include_router(export_io.router, dependencies=_auth)
app.include_router(library.router, dependencies=_auth)

# WebSocket log stream is capability-based (unguessable task_id) — headers can't
# be set on the WS handshake from the browser, so it stays outside the gate.
app.include_router(ws.router)
# Internal aggregator source — NOT account-gated (the subs-aggregator container
# has no account token). Only reachable on node-assistant-net: compose `expose`s
# it without a host port and nginx does not proxy /internal. Same ungated posture
# as ws.router, justified by network isolation.
app.include_router(subscriptions.internal_router)
# Remnawave webhook receiver — NOT account-gated. Its capability is a valid
# HMAC-SHA256 signature (shared WEBHOOK_SECRET_HEADER secret); a browser can't
# forge one. Same ungated posture as ws.router, justified by the signature gate.
app.include_router(rules.webhook_router)


@app.get("/api/health")
async def health():
    return {"ok": True}
