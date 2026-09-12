"""The model worker: today's `model_server.py`, spawned and spoken to over its Unix socket.

The worker is a separate process on purpose. It holds the models, recycles itself (a re-exec
in place) when it crosses its RSS ceiling, and can die of a CUDA fault — none of which should
take the HTTP listener or the tool ops with it. This module spawns it, restarts it when it
exits, and translates its `{ok, result|error}` line protocol into `McsError`s.

Configuration crosses the boundary by prefix: every `MCS_<NAME>` in this process's environment
reaches the worker as `CFG_<NAME>`, which is the namespace the moved-unchanged worker code reads
(`CFG_VLM_MODEL`, `CFG_WHISPER_MODEL`, `CFG_MODEL_PINNED`, …). `CFG_*` already set are passed
through too, so a container that still carries the old names keeps working.
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import sys
import time
from typing import Any

from .errors import (
    DEADLINE_EXCEEDED,
    INTERNAL,
    INVALID_REQUEST,
    MODEL_UNAVAILABLE,
    NOT_FOUND,
    TOOL_FAILED,
    UNSUPPORTED,
    McsError,
)

RESTART_BACKOFF_SECONDS = (1, 2, 5, 10, 30)
CARD_BUSY_RETRY_AFTER_SECONDS = 60
STOP_GRACE_SECONDS = 20

# Ops whose failure means the worker is unusable rather than the request wrong. `str(exc)` is
# all the wire carried until `error_type` was added beside it; both are consulted, the type
# first, so a worker from before that change still classifies by message.
_TYPE_TO_CODE = {
    "CardBusy": MODEL_UNAVAILABLE,
    "TimeoutError": DEADLINE_EXCEEDED,
    "FileNotFoundError": NOT_FOUND,
    "ImportError": INTERNAL,
    "ModuleNotFoundError": INTERNAL,
}

_MESSAGE_TO_CODE = (
    ("cuda oom", MODEL_UNAVAILABLE),
    ("card is busy", MODEL_UNAVAILABLE),
    ("gpu unavailable", MODEL_UNAVAILABLE),
    ("request expired", DEADLINE_EXCEEDED),
    ("could not load image", UNSUPPORTED),
    ("cannot identify image file", UNSUPPORTED),
    ("not found", NOT_FOUND),
    ("unknown op", INVALID_REQUEST),
    ("missing", INVALID_REQUEST),
)


def classify(error: str, error_type: str | None) -> McsError:
    code = _TYPE_TO_CODE.get(error_type or "")
    if code is None:
        lowered = error.lower()
        for needle, candidate in _MESSAGE_TO_CODE:
            if needle in lowered:
                code = candidate
                break
    if code is None and error_type == "ValueError":
        code = INVALID_REQUEST
    if code is None:
        code = TOOL_FAILED
    retry_after = CARD_BUSY_RETRY_AFTER_SECONDS if code is MODEL_UNAVAILABLE else None
    return McsError(code, error, retry_after=retry_after)


# Thread caps the front sets for its tools that must not reach the models: torch on the CPU
# (the instruct model runs there in the 32 GiB profile) wants every core it can get.
TOOL_ONLY_ENV = ("OMP_THREAD_LIMIT",)


def worker_env(env: dict[str, str]) -> dict[str, str]:
    child = {name: value for name, value in env.items() if name not in TOOL_ONLY_ENV}
    for name, value in env.items():
        if name.startswith("MCS_"):
            child.setdefault("CFG_" + name[4:], value)
    return child


class Worker:
    def __init__(self, script: str, socket_path: str, *, env: dict[str, str] | None = None, log=print):
        self.script = script
        self.socket_path = socket_path
        self.env = worker_env(dict(os.environ) if env is None else env)
        self.env["CFG_MODEL_SOCKET"] = socket_path
        self.log = log
        self.process: asyncio.subprocess.Process | None = None
        self._supervisor: asyncio.Task | None = None
        self._stopping = False
        self.started_at: float | None = None
        self.restarts = 0

    # ---- lifecycle ----

    async def start(self) -> None:
        if not os.path.isfile(self.script):
            self.log(f"worker: no script at {self.script}; model ops will answer model_unavailable")
            return
        self._stopping = False
        self._supervisor = asyncio.create_task(self._supervise())

    async def _supervise(self) -> None:
        attempt = 0
        while not self._stopping:
            self.process = await asyncio.create_subprocess_exec(
                sys.executable, self.script, env=self.env, stdin=asyncio.subprocess.DEVNULL,
            )
            self.started_at = time.time()
            self.log(f"worker: started pid {self.process.pid}")
            code = await self.process.wait()
            if self._stopping:
                return
            # A re-exec on the RSS ceiling keeps the pid, so reaching here is a real exit.
            self.restarts += 1
            delay = RESTART_BACKOFF_SECONDS[min(attempt, len(RESTART_BACKOFF_SECONDS) - 1)]
            attempt += 1
            self.log(f"worker: exited with {code}; restarting in {delay}s")
            await asyncio.sleep(delay)
            if self.started_at and time.time() - self.started_at > 300:
                attempt = 0  # it ran for a while; the next failure starts the ladder over

    async def stop(self) -> None:
        self._stopping = True
        if self._supervisor:
            self._supervisor.cancel()
            await asyncio.gather(self._supervisor, return_exceptions=True)
        proc = self.process
        if proc and proc.returncode is None:
            proc.send_signal(signal.SIGTERM)
            try:
                await asyncio.wait_for(proc.wait(), STOP_GRACE_SECONDS)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()

    # ---- requests ----

    async def call(self, op: str, payload: dict[str, Any], *, deadline: float | None = None) -> Any:
        """One request, one connection. Raises McsError on every failure."""
        body = {"op": op, **payload}
        if deadline is not None:
            body["expires_at"] = deadline
        try:
            reader, writer = await asyncio.open_unix_connection(self.socket_path)
        except (FileNotFoundError, ConnectionRefusedError, OSError) as exc:
            raise McsError(MODEL_UNAVAILABLE, f"model worker is not answering ({exc.__class__.__name__})", retry_after=10) from exc
        try:
            writer.write((json.dumps(body) + "\n").encode())
            await writer.drain()
            if writer.can_write_eof():
                writer.write_eof()
            raw = await reader.read()
        finally:
            # On cancellation this is what tells the worker the caller is gone; queued work is
            # discarded by its own `expires_at`, running work finishes and is dropped.
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:  # noqa: BLE001 - the peer may have closed first
                pass
        text = raw.decode(errors="replace").strip()
        if not text:
            raise McsError(MODEL_UNAVAILABLE, "model worker closed the connection without answering", retry_after=10)
        try:
            reply = json.loads(text)
        except json.JSONDecodeError as exc:
            raise McsError(INTERNAL, f"model worker returned unparseable output: {text[:200]}") from exc
        if not reply.get("ok"):
            raise classify(str(reply.get("error") or "model worker returned an error"), reply.get("error_type"))
        return reply.get("result")

    async def health(self) -> dict | None:
        try:
            return await self.call("health", {})
        except McsError:
            return None
