"""Per-address sliding window for POST /ask and POST /ask/stream.

The map lives in this process. Another process has its own map.
The address is the TCP peer, request.client.host. X-Forwarded-For is ignored.
"""

from __future__ import annotations

import asyncio
import math
import time
from collections import deque
from dataclasses import dataclass

from fastapi import HTTPException, Request


@dataclass(frozen=True, slots=True)
class RateDecision:
    allowed: bool
    retry_after_s: int


class AskLimiter:
    """Sliding window of monotonic timestamps, guarded by one lock."""

    def __init__(self, *, limit: int, window_s: float) -> None:
        self._limit = limit
        self._window_s = window_s
        self._hits: dict[str, deque[float]] = {}
        self._lock = asyncio.Lock()

    async def hit(self, ip: str, now: float) -> RateDecision:
        if self._limit <= 0:
            return RateDecision(allowed=True, retry_after_s=0)
        async with self._lock:
            self._evict(now)
            bucket = self._hits.setdefault(ip, deque())
            if len(bucket) >= self._limit:
                retry_after_s = max(1, math.ceil(bucket[0] + self._window_s - now))
                return RateDecision(allowed=False, retry_after_s=retry_after_s)
            bucket.append(now)
            return RateDecision(allowed=True, retry_after_s=0)

    def _evict(self, now: float) -> None:
        cutoff = now - self._window_s
        empty: list[str] = []
        for key, bucket in self._hits.items():
            while bucket and bucket[0] <= cutoff:
                bucket.popleft()
            if not bucket:
                empty.append(key)
        for key in empty:
            del self._hits[key]


async def enforce_ask_limit(request: Request) -> None:
    """Reject the ask when this process has already seen the address enough times."""
    limiter: AskLimiter = request.app.state.ask_limiter
    peer = request.client
    ip = peer.host if peer is not None else "unknown"
    decision = await limiter.hit(ip, time.monotonic())
    if decision.allowed:
        return
    raise HTTPException(
        status_code=429,
        detail="rate limit exceeded",
        headers={"Retry-After": str(decision.retry_after_s)},
    )
