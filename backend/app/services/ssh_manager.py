"""
Async SSH manager — true line-by-line streaming via asyncssh.create_process().

Design choices:
- create_process() instead of run() so output appears as it's produced, not after.
- run_script() sends multi-line bash via stdin to "bash -s 2>&1" — no SFTP needed.
- stderr merged into stdout with 2>&1 to preserve interleaved ordering.
- Global semaphore caps concurrent SSH sessions (shared across all tasks).
"""
import asyncio
import asyncssh
from typing import Optional

from app.services.task_store import Task
from app.config import settings

_session_sem: Optional[asyncio.Semaphore] = None


def _get_sem() -> asyncio.Semaphore:
    global _session_sem
    if _session_sem is None:
        _session_sem = asyncio.Semaphore(settings.max_ssh_sessions)
    return _session_sem


class SSHSession:
    def __init__(self, host: str, port: int, username: str, password: str):
        self.host     = host
        self.port     = port
        self.username = username
        self.password = password
        self._conn: Optional[asyncssh.SSHClientConnection] = None

    async def connect(self, timeout: int = 30) -> None:
        self._conn = await asyncio.wait_for(
            asyncssh.connect(
                self.host,
                port=self.port,
                username=self.username,
                password=self.password,
                known_hosts=None,
                server_host_key_algs=["ssh-rsa", "ecdsa-sha2-nistp256", "ssh-ed25519"],
                # Keep-alive so long-running installs don't disconnect
                keepalive_interval=30,
                keepalive_count_max=10,
            ),
            timeout=timeout,
        )

    async def run(
        self,
        command: str,
        task: Task,
        check: bool = True,
        timeout: Optional[int] = None,
    ) -> int:
        """
        Stream command stdout+stderr line-by-line to task logs.
        Returns exit code.
        """
        if self._conn is None:
            raise RuntimeError("SSH session not connected")

        task.add_log(f"\x1b[2m$ {command}\x1b[0m")

        async with _get_sem():
            process = await self._conn.create_process(command + " 2>&1")
            try:
                coro = self._drain(process.stdout, task)
                if timeout:
                    await asyncio.wait_for(coro, timeout=timeout)
                else:
                    await coro
            finally:
                process.close()

        rc = process.exit_status if process.exit_status is not None else 0
        if check and rc != 0:
            raise RuntimeError(
                f"Command failed (exit {rc}): {command[:120]}"
            )
        return rc

    async def run_script(
        self,
        script: str,
        task: Task,
        check: bool = True,
        timeout: Optional[int] = None,
    ) -> int:
        """
        Execute a multi-line bash script by piping it to "bash -s".
        No SFTP subsystem required.
        """
        if self._conn is None:
            raise RuntimeError("SSH session not connected")

        task.add_log("\x1b[2m[script block start]\x1b[0m")

        async with _get_sem():
            process = await self._conn.create_process("bash -s 2>&1")
            try:
                process.stdin.write(script)
                process.stdin.write_eof()

                coro = self._drain(process.stdout, task)
                if timeout:
                    await asyncio.wait_for(coro, timeout=timeout)
                else:
                    await coro
            finally:
                process.close()

        task.add_log("\x1b[2m[script block end]\x1b[0m")

        rc = process.exit_status if process.exit_status is not None else 0
        if check and rc != 0:
            raise RuntimeError(f"Script block failed (exit {rc})")
        return rc

    async def get_output(self, command: str) -> str:
        """Run a command silently and return its stdout (for small one-liner outputs)."""
        if self._conn is None:
            raise RuntimeError("SSH session not connected")
        result = await self._conn.run(command, check=False)
        return (result.stdout or "").strip()

    async def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    # ------------------------------------------------------------------

    @staticmethod
    async def _drain(reader: asyncssh.SSHReader, task: Task) -> None:
        """Read chunks from an SSHReader and emit individual lines to the task."""
        buf = ""
        async for chunk in reader:
            buf += chunk
            while "\n" in buf:
                line, buf = buf.split("\n", 1)
                # Strip carriage returns that some programs emit
                task.add_log(line.rstrip("\r"))
        if buf.strip():
            task.add_log(buf.rstrip("\r"))
