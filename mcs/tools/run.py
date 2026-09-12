"""Spawning a tool: bounded, niced, and killed when the caller is gone.

Every tool runs under `nice -n 10` so the HTTP process and the model worker keep their scheduler
priority while a transcode grinds. A timeout or a cancelled request kills the process group
rather than the process alone: ffmpeg and ImageMagick fork helpers, and an orphaned helper
holding a GPU context is exactly the leak a killed request must not leave behind.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import signal
from dataclasses import dataclass

from ..errors import McsError, TOOL_FAILED

NICE = 10


@dataclass
class Completed:
    args: list[str]
    code: int
    stdout: bytes
    stderr: bytes

    @property
    def stderr_text(self) -> str:
        return self.stderr.decode(errors="replace")

    def tail(self, lines: int = 3, limit: int = 400) -> str:
        text = self.stderr_text.strip().splitlines()
        return "\n".join(text[-lines:])[-limit:]


def which(name: str) -> str | None:
    return shutil.which(name)


async def run(args: list[str], *, timeout: float, stdin: bytes | None = None, cwd: str | None = None, env: dict | None = None) -> Completed:
    """Run to completion. Never raises for a non-zero exit — the caller reads `code`."""
    nice = which("nice")
    argv = [nice, "-n", str(NICE), *args] if nice else list(args)
    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdin=asyncio.subprocess.PIPE if stdin is not None else asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd,
        env=env,
        start_new_session=True,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(stdin), timeout)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        _kill_group(proc)
        await proc.wait()
        raise
    return Completed(list(args), proc.returncode or 0, stdout, stderr)


def _kill_group(proc: asyncio.subprocess.Process) -> None:
    if proc.returncode is not None:
        return
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    except PermissionError:
        proc.kill()


def require(name: str) -> str:
    path = which(name)
    if path is None:
        raise McsError(TOOL_FAILED, f"{name} is not installed in this MCS", permanent=True)
    return path
