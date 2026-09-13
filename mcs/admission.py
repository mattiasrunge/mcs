"""Bounded concurrency, and a queue that is allowed to say no.

Three lanes: `models` (requests forwarded to the model worker, which serialises per model
family behind this), `tools` (CPU and subprocess work in this process) and `encodes` (the
transcodes, which hold a slot for minutes and must not sit ahead of every probe and rendition
queued behind them). Each lane runs at most
`limit` requests at once; an interactive request may also use `reserve` extra slots, so a crawl
that keeps the lane full never makes a person wait behind it. Past `queue_depth` waiters across
both lanes a new request is answered `busy` with a `Retry-After` — the caller's own queue is
the right place for that work to sit, not a socket held open here.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from .errors import BUSY, McsError

BUSY_RETRY_AFTER_SECONDS = 5


class Lane:
    def __init__(self, name: str, limit: int, reserve: int):
        self.name = name
        self.limit = max(1, limit)
        self.reserve = max(0, reserve)
        self.running = 0
        self.waiting = 0
        self._cond = asyncio.Condition()

    def _may_run(self, interactive: bool) -> bool:
        cap = self.limit + (self.reserve if interactive else 0)
        return self.running < cap

    def snapshot(self) -> dict:
        return {"running": self.running, "waiting": self.waiting, "limit": self.limit, "reserve": self.reserve}


class Admission:
    def __init__(self, *, limit_models: int, limit_tools: int, queue_depth: int, interactive_reserve: int, limit_encodes: int = 2):
        self.lanes = {
            "models": Lane("models", limit_models, interactive_reserve),
            "tools": Lane("tools", limit_tools, interactive_reserve),
            "encodes": Lane("encodes", limit_encodes, 0),
        }
        self.queue_depth = max(0, queue_depth)

    def snapshot(self) -> dict:
        return {name: lane.snapshot() for name, lane in self.lanes.items()}

    def _waiting_total(self) -> int:
        return sum(lane.waiting for lane in self.lanes.values())

    @asynccontextmanager
    async def slot(self, lane_name: str, *, interactive: bool = False):
        lane = self.lanes[lane_name]
        async with lane._cond:
            if not lane._may_run(interactive):
                if self._waiting_total() >= self.queue_depth:
                    raise McsError(BUSY, f"{lane.name}: queue is full", retry_after=BUSY_RETRY_AFTER_SECONDS)
                lane.waiting += 1
                try:
                    await lane._cond.wait_for(lambda: lane._may_run(interactive))
                finally:
                    lane.waiting -= 1
            lane.running += 1
        try:
            yield
        finally:
            async with lane._cond:
                lane.running -= 1
                lane._cond.notify_all()
