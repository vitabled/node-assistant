import asyncio
import contextlib

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.config import settings
from app.api import (
    deploy, certs, ws, stats, settings as settings_router, traffic_rules,
    xray_checker, infra_billing,
)


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


app = FastAPI(title="Node Installer", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.cors_origin, "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(deploy.router)
app.include_router(certs.router)
app.include_router(ws.router)
app.include_router(stats.router)
app.include_router(settings_router.router)
app.include_router(traffic_rules.router)
app.include_router(xray_checker.router)
app.include_router(infra_billing.router)


@app.get("/api/health")
async def health():
    return {"ok": True}
