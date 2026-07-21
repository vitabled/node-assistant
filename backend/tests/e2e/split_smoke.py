"""
End-to-end smoke test for the optional service split (Wave 5 Plan M).

    cd backend && python tests/e2e/split_smoke.py

NOT a pytest test (the filename is deliberately not `test_*.py`, so pytest does
not collect it): it spawns real processes and takes ~1.5 min. Run it after
touching worker_lease / shared_task_store / job_runner / app/worker.py.

Why it exists: the unit tests exercise the split's logic inside ONE interpreter,
which cannot catch the failure modes that only appear across processes. This
harness has already caught two of them — a circular import that only fires in the
worker's import order, and the `current_account` ContextVar not surviving the job
queue. Everything runs against one DATA_DIR in a temp dir; nothing touches the
user's stack or any real server.

Four phases:
  1. gateway alone            → it holds `monitoring` itself (monolith behaviour)
  2. start both workers       → the duties migrate to the dedicated processes
  3. POST a real deploy       → it is QUEUED, the worker runs the 14-step pipeline,
                                and its log lines reach a WS subscriber on the gateway
  4. kill the workers         → the gateway resumes `monitoring` on its own
                                (the plan's rollback criterion)

Phase 3 targets 192.0.2.1 (TEST-NET-1 — reserved for documentation, never routed),
so the deploy fails at step 1 without touching anything real.

NOT covered here: the GRACEFUL shutdown path (SIGTERM → release the lease and fail
the in-flight job). `Popen.terminate()` is a hard kill on Windows, so phase 4
simulates the ungraceful case and expires the lease by hand — which is the harder
of the two anyway. The graceful path is unit-tested
(test_job_runner.test_shutdown_fails_the_in_flight_job_and_releases_the_lease).
"""
import asyncio
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

import websockets  # ships with uvicorn[standard]

BACKEND = Path(__file__).resolve().parents[2]
PORT = sys.argv[1] if len(sys.argv) > 1 else "8097"
DATA = tempfile.mkdtemp(prefix="ni_split_")

ENV = dict(os.environ, DATA_DIR=DATA, TASK_STORE="shared", ENCRYPTION_KEY="s" * 64,
           PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8")

procs: dict[str, tuple] = {}
fail: list[str] = []


def spawn(name, args, extra=None):
    env = dict(ENV)
    env.update(extra or {})
    logf = open(os.path.join(DATA, f"{name}.log"), "w", encoding="utf-8")
    procs[name] = (subprocess.Popen(args, cwd=BACKEND, env=env,
                                    stdout=logf, stderr=subprocess.STDOUT), logf)


def stop(name):
    p, logf = procs.pop(name)
    p.terminate()
    try:
        p.wait(timeout=10)
    except Exception:
        p.kill()
    logf.close()


def api(path, body=None, token=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(
        f"http://127.0.0.1:{PORT}{path}",
        data=json.dumps(body).encode() if body is not None else None,
        headers=headers)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def duty(name):
    return next(d for d in api("/api/health")["duties"] if d["name"] == name)


def show(label):
    h = api("/api/health")
    print(f"\n--- {label}")
    print("  taskStore:", h["taskStore"])
    for d in h["duties"]:
        print(f"  {d['name']:<14} holder={d['holder']} fresh={d['fresh']} self={d['self']}")


async def stream(task_id, seconds=90):
    """Subscribe exactly like the browser's useTaskStream does."""
    frames = []
    async with websockets.connect(f"ws://127.0.0.1:{PORT}/ws/logs/{task_id}") as ws:
        deadline = time.time() + seconds
        while time.time() < deadline:
            try:
                msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=deadline - time.time()))
            except (asyncio.TimeoutError, websockets.ConnectionClosed):
                break
            frames.append(msg)
            if msg.get("type") == "done":
                break
    return frames


try:
    # ── 1. gateway alone ────────────────────────────────────────
    spawn("gateway", [sys.executable, "-m", "uvicorn", "app.main:app",
                      "--host", "127.0.0.1", "--port", PORT, "--log-level", "warning"])
    for _ in range(60):
        try:
            api("/api/health")
            break
        except Exception:
            time.sleep(1)
    else:
        raise SystemExit("gateway never came up")

    time.sleep(3)
    show("1. gateway alone (monolith behaviour)")
    if not duty("monitoring")["self"]:
        fail.append("gateway should hold `monitoring` while alone")

    # ── 2. workers take over ────────────────────────────────────
    spawn("monitoring", [sys.executable, "-m", "app.worker", "monitoring"],
          {"SERVICE_ROLE": "monitoring"})
    spawn("deploy", [sys.executable, "-m", "app.worker", "deploy"],
          {"SERVICE_ROLE": "deploy-worker"})

    for _ in range(40):
        time.sleep(1)
        if not duty("monitoring")["self"] and duty("deploy-worker")["fresh"]:
            break
    show("2. after starting the split workers")
    if duty("monitoring")["self"] or not duty("monitoring")["fresh"]:
        fail.append("`monitoring` should have moved to the dedicated worker")
    if duty("deploy-worker")["self"] or not duty("deploy-worker")["fresh"]:
        fail.append("`deploy-worker` should be held by the dedicated worker")

    # ── 3. a real deploy, run by the worker, streamed by the gateway ──
    token = api("/api/auth/register", {"login": f"e2e{int(time.time())}", "password": "pw"})["token"]
    r = api("/api/deploy", dict(
        mode="haproxy", ip="192.0.2.1", ssh_password="pw", open_ports="443",
        haproxy_dest_ip="10.0.0.5", current_ssh_port=22, change_ssh_port=False,
        update_system=False, optimize=False, install_trafficguard=False,
        install_test_tools=False, install_vnstat=False), token)
    task_id = r["task_id"]
    print(f"\n--- 3. deploy queued: {task_id}")
    print("  taskStore right after POST:", api("/api/health")["taskStore"])

    frames = asyncio.run(stream(task_id))
    logs = [f["line"] for f in frames if f.get("type") == "log"]
    done = [f for f in frames if f.get("type") == "done"]
    print(f"  WS frames: {len(frames)} ({len(logs)} log lines)")
    for ln in logs[:6]:
        print("   |", ln[:100])

    worker_log = Path(DATA, "deploy.log").read_text(encoding="utf-8", errors="replace")
    if f"job_runner.claimed kind=deploy task_id={task_id}" not in worker_log:
        fail.append("the worker never claimed the task (it likely ran in the gateway)")
    if not any("Подключение" in ln for ln in logs):
        fail.append("the pipeline's step-1 log did not cross the process boundary")
    if not done:
        fail.append("no 'done' frame — the stream never terminated")
    elif done[0].get("status") != "failed":
        fail.append(f"expected FAILED for an unroutable IP, got {done[0].get('status')}")
    final = api(f"/api/task/{task_id}", token=token)
    print("  gateway task view:", final)
    if final["status"] != "failed" or final["total"] != 14:
        fail.append(f"gateway task view wrong: {final}")

    # ── 4. rollback criterion ───────────────────────────────────
    # Kill the workers, then expire their lease rows — exactly what the 180 s TTL
    # does, shortcut so the check finishes quickly.
    stop("monitoring")
    stop("deploy")
    con = sqlite3.connect(os.path.join(DATA, "tasks.db"), timeout=10)
    con.execute("UPDATE leases SET expires_at=1")
    con.commit()
    con.close()

    for _ in range(90):
        time.sleep(1)
        if duty("monitoring")["self"]:
            break
    show("4. after killing the workers (fallback)")
    if not duty("monitoring")["self"]:
        fail.append("gateway did not resume `monitoring` after the worker died")
finally:
    for name in list(procs):
        stop(name)

print("\n==== RESULT:", "FAIL: " + "; ".join(fail) if fail else "PASS")
sys.exit(1 if fail else 0)
