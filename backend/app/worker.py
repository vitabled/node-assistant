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
import sys

ROLES = ("monitoring", "deploy")


def _usage() -> None:
    print(f"usage: python -m app.worker [{'|'.join(ROLES)}]", file=sys.stderr)


async def _run_monitoring() -> None:
    # Imported here so the process only pulls in what its role needs.
    from app.api import rules, server_monitor, user_stats, xray_checker

    tasks = [
        asyncio.create_task(xray_checker.poller_loop()),
        asyncio.create_task(user_stats.collector_loop()),
        asyncio.create_task(rules.rules_loop()),
        asyncio.create_task(xray_checker.autostart_checker()),
        asyncio.create_task(server_monitor.monitor_loop()),
    ]
    try:
        await asyncio.gather(*tasks)
    finally:
        for t in tasks:
            t.cancel()
        for t in tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await t


async def _run_deploy() -> None:
    # Importing the routers is what REGISTERS their job handlers with job_runner.
    from app.api import deploy, node_ops  # noqa: F401
    from app.services import job_runner, shared_task_store

    if not shared_task_store.enabled():
        raise SystemExit(
            "deploy worker requires TASK_STORE=shared (the gateway and the worker "
            "must share the task store to exchange jobs and logs)"
        )
    await job_runner.run_forever()


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
