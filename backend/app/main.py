import asyncio
import contextlib
import sys

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.config import settings
from app.api import (
    auth, deploy, certs, ws, stats, settings as settings_router, traffic_rules,
    xray_checker, infra_billing,
)
from app.api.auth import require_account


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    # Background poller: samples xray-checker into the SQLite metrics store so
    # the dashboard's 24h graphs have history. Skips work when the checker is off.
    poller = asyncio.create_task(xray_checker.poller_loop())
    try:
        yield
    finally:
        poller.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await poller


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
app.include_router(settings_router.router, dependencies=_auth)
app.include_router(traffic_rules.router, dependencies=_auth)
app.include_router(xray_checker.router, dependencies=_auth)
app.include_router(infra_billing.router, dependencies=_auth)

# WebSocket log stream is capability-based (unguessable task_id) — headers can't
# be set on the WS handshake from the browser, so it stays outside the gate.
app.include_router(ws.router)


@app.get("/api/health")
async def health():
    return {"ok": True}
