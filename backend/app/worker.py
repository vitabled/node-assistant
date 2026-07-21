"""
Entrypoint for the split worker containers (Plan M, `docker compose --profile split`).

    python -m app.worker monitoring     # the 5 background loops
    python -m app.worker deploy         # the SSH job runner

Both run the SAME code as the monolith — this module only decides which duties
this process performs. It serves NO HTTP: the gateway and the workers share the
`node-data` volume, so every read the UI needs is already answerable from the
gateway's own process. That is why there is no proxy layer and no
service-to-service auth anywhere in the split.

Duties are claimed through `services.worker_lease`, so:
  * starting a worker takes the duty away from the gateway, and
  * stopping one hands it straight back (lease TTL), with no config change.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import signal
import sys

ROLES = ("monitoring", "deploy")


def _usage() -> None:
    print(f"usage: python -m app.worker [{'|'.join(ROLES)}]", file=sys.stderr)


def _shutdown_event() -> asyncio.Event:
    """Event set on SIGTERM/SIGINT.

    `docker compose down/stop` sends SIGTERM to PID 1. Without a handler the
    default disposition for PID 1 is to IGNORE it, so docker waits out the grace
    period and SIGKILLs — leaving the lease held (the gateway then can't take
    over for a full TTL) and any in-flight job claimed and RUNNING forever."""
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop.set)
        except (NotImplementedError, AttributeError, ValueError):
            # Windows dev boxes have no add_signal_handler for SIGTERM.
            with contextlib.suppress(Exception):
                signal.signal(sig, lambda *_: stop.set())
    return stop


async def _until_shutdown(work: list[asyncio.Task]) -> None:
    """Run `work` until a shutdown signal arrives (or one of the tasks exits),
    then cancel it and let each task's own `finally` clean up.

    Pass ONLY never-returning tasks: this returns as soon as any of them does,
    so a one-shot in the list would tear the whole process down the moment it
    finished."""
    stop = _shutdown_event()
    waiter = asyncio.create_task(stop.wait())
    try:
        await asyncio.wait([*work, waiter], return_when=asyncio.FIRST_COMPLETED)
    finally:
        waiter.cancel()
        for t in work:
            t.cancel()
        for t in work:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await t


async def _run_monitoring() -> None:
    # Imported here so the process only pulls in what its role needs.
    from app.api import rules, server_monitor, user_stats, xray_checker
    from app.services import worker_lease

    # autostart_checker is a ONE-SHOT boot hook, so it is deliberately kept out
    # of the wait set — `_until_shutdown` returns when any task in that set does,
    # and this one returns within seconds.
    autostart = asyncio.create_task(xray_checker.autostart_checker())
    try:
        await _until_shutdown([
            asyncio.create_task(xray_checker.poller_loop()),
            asyncio.create_task(user_stats.collector_loop()),
            asyncio.create_task(rules.rules_loop()),
            asyncio.create_task(server_monitor.monitor_loop()),
        ])
    finally:
        autostart.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await autostart
        # Hand the duty straight back instead of making the gateway wait out the
        # lease TTL before it resumes monitoring.
        worker_lease.release(worker_lease.MONITORING)


async def _run_deploy() -> None:
    # Importing the routers is what REGISTERS their job handlers with job_runner.
    from app.api import deploy, node_ops  # noqa: F401
    from app.services import job_runner, shared_task_store

    if not shared_task_store.enabled():
        raise SystemExit(
            "deploy worker requires TASK_STORE=shared (the gateway and the worker "
            "must share the task store to exchange jobs and logs)"
        )
    # run_forever's own `finally` fails the in-flight job and releases the lease.
    await _until_shutdown([asyncio.create_task(job_runner.run_forever())])


def main(argv: list[str]) -> int:
    if len(argv) != 2 or argv[1] not in ROLES:
        _usage()
        return 2
    role = argv[1]

    # Declare the role BEFORE anything imports worker_lease-driven code: a
    # dedicated worker claims its duty unconditionally (see worker_lease.acquire).
    os.environ.setdefault(
        "SERVICE_ROLE", "monitoring" if role == "monitoring" else "deploy-worker"
    )
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format=f"%(asctime)s [{role}] %(levelname)s %(name)s: %(message)s",
    )
    log = logging.getLogger("app.worker")
    log.info("worker starting role=%s task_store=%s",
             role, os.getenv("TASK_STORE", "memory"))

    runner = _run_monitoring if role == "monitoring" else _run_deploy
    try:
        asyncio.run(runner())
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
