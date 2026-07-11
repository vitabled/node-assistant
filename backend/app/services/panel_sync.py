"""
Panel sync orchestrator (Ф5): keep a standby panel in sync by restoring the
freshest backup of its nearest-higher primary.

Flow (reuses backup_service Wave-1 scripts over SSH):
  1. resolve the nearest-higher primary for the standby (sync_store);
  2. GUARD: the restore target MUST be a `standby` — never restore onto a primary
     (that would destroy the live primary's DB);
  3. run a backup on the primary (pushes to the shared backup remote);
  4. run a DESTRUCTIVE restore on the standby (pulls the freshest backup).

SSH creds for both panels are supplied per-request from the client's `panel_jobs`
(never stored server-side). Streamed through a Task like the backup endpoints.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
import time
from typing import Optional

from app.services import backup_service, sync_store
from app.services.ssh_manager import SSHSession
from app.services.task_store import TaskStatus


class SyncError(Exception):
    pass


# Guard against concurrent destructive syncs onto the SAME standby box.
_inflight: set[str] = set()
_inflight_lock = asyncio.Lock()


def plan_sync(group: dict, standby_key: str) -> tuple[dict, dict]:
    """Pure planning: (primary_member, standby_member). Raises SyncError if the
    target isn't a standby or there's no higher primary. No side effects."""
    members = group.get("members") or []
    standby = next((m for m in members if m.get("panel_key") == standby_key), None)
    if not standby:
        raise SyncError("Указанный узел не входит в группу.")
    if standby.get("role") != "standby":
        raise SyncError(
            "Цель синхронизации — не standby. Восстановление ДЕСТРУКТИВНО и "
            "запрещено на primary."
        )
    primary = sync_store.nearest_higher_primary(members, standby_key)
    if primary is None:
        raise SyncError("Нет вышестоящего primary для этого standby.")
    return primary, standby


def _sess(creds: dict) -> SSHSession:
    return SSHSession(
        creds["ip"], creds["ssh_port"], creds["ssh_user"], creds.get("ssh_password", "")
    )


async def run_sync(
    task,
    group: dict,
    standby_key: str,
    primary_creds: dict,
    standby_creds: dict,
    account_id: Optional[str] = None,
) -> None:
    """Execute a standby sync, streaming into `task`. Never raises — records
    SUCCESS/FAILED on the task and updates the group's last-sync fields.

    Real transfer: back up the primary, RELAY the freshly-created bundle
    primary → backend → standby over SFTP, then restore THAT specific bundle on
    the standby (the distillium wrapper otherwise restores the standby's own
    newest LOCAL bundle — wrong data / silent no-op)."""
    standby_ip = standby_creds.get("ip", "")
    async with _inflight_lock:
        if standby_ip in _inflight:
            task.set_step(1, TaskStatus.RUNNING)
            task.add_log(
                "\x1b[1;31m✗ Синхронизация этого standby уже выполняется.\x1b[0m"
            )
            task.finish(
                TaskStatus.FAILED, "Синхронизация уже выполняется для этого узла."
            )
            return
        _inflight.add(standby_ip)

    p_ssh = s_ssh = None
    try:
        task.set_step(1, TaskStatus.RUNNING)
        # Re-read the group fresh (role may have changed since the endpoint check).
        fresh = sync_store.get_group(group["id"], account_id) or group
        primary, standby = plan_sync(fresh, standby_key)
        task.add_log(
            f"\x1b[36m[sync] primary={primary['panel_key']} → "
            f"standby={standby['panel_key']}\x1b[0m"
        )

        p_ssh, s_ssh = _sess(primary_creds), _sess(standby_creds)
        await p_ssh.connect()
        task.add_log("\x1b[32m[primary] подключено.\x1b[0m")
        await s_ssh.connect()
        task.add_log("\x1b[32m[standby] подключено.\x1b[0m")

        # 1. Fresh backup on the primary.
        task.add_log("\x1b[36m[sync] бэкап primary...\x1b[0m")
        rc = await p_ssh.run_script(
            backup_service.run_backup_script(), task, check=False, timeout=1800
        )
        if rc != 0:
            raise SyncError(
                "[primary] бэкап завершился с ошибкой (restore не выполнялся)."
            )

        # 2. Locate the freshly-created bundle on the primary.
        bundle = (await p_ssh.get_output(backup_service.newest_bundle_cmd())).strip()
        if not bundle:
            raise SyncError("[primary] свежий бэкап не найден — restore не выполнялся.")

        # 3. Relay it primary → backend → standby (SFTP).
        with tempfile.TemporaryDirectory() as td:
            local = os.path.join(td, "bundle.tar.gz")
            task.add_log("\x1b[36m[sync] перенос бэкапа primary → standby...\x1b[0m")
            await p_ssh.download_file(bundle, local)
            size = os.path.getsize(local)
            if size < 128:  # sanity: an empty/failed dump bundle
                raise SyncError(
                    f"[sync] перенесённый бэкап подозрительно мал ({size} б)."
                )
            await s_ssh.run(f"mkdir -p {backup_service.BACKUPS_DIR}", task, check=False)
            await s_ssh.upload_file(local, backup_service.SYNC_BUNDLE_PATH)

        # 4. DESTRUCTIVE restore of THAT bundle on the standby.
        task.add_log(
            "\x1b[1;31m[ВНИМАНИЕ] Восстановление на standby ДЕСТРУКТИВНО — том "
            "remnawave-db-data будет очищен.\x1b[0m"
        )
        rc = await s_ssh.run_script(
            backup_service.restore_script(True, backup_service.SYNC_BUNDLE_PATH),
            task,
            check=False,
            timeout=1800,
        )
        if rc != 0:
            raise SyncError("[standby] восстановление завершилось с ошибкой.")

        sync_store.update_group(
            group["id"],
            {"last_sync_at": int(time.time()), "last_sync_status": "success"},
            account_id,
        )
        task.finish(TaskStatus.SUCCESS)
        task.add_log("\n\x1b[1;32m✓ Синхронизация завершена.\x1b[0m")
    except Exception as exc:
        try:
            sync_store.update_group(
                group["id"], {"last_sync_status": "error"}, account_id
            )
        except Exception:
            pass
        task.add_log(f"\n\x1b[1;31m✗ Ошибка синхронизации: {exc}\x1b[0m")
        task.finish(TaskStatus.FAILED, str(exc))
    finally:
        for s in (p_ssh, s_ssh):
            if s is not None:
                try:
                    await s.close()
                except Exception:
                    pass
        async with _inflight_lock:
            _inflight.discard(standby_ip)
