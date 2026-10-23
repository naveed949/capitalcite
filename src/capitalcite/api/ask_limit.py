from __future__ import annotations

import asyncio
import math
import time
from collections import OrderedDict, deque
from dataclasses import dataclass

from fastapi import HTTPException, Request

DEFAULT_MAX_KEYS = 10_000


@dataclass(frozen=True, slots=True)
class RateDecision:
    allowed: bool
    retry_after_s: int


def forwarded_client(peer: str, forwarded: str | None, hops: int) -> str:
    if hops <= 0:
        return peer
    if forwarded is None:
        return peer
    parts = [part.strip() for part in forwarded.split(",") if part.strip()]
    if len(parts) < hops + 1:
        return peer
    return parts[-hops]


class AskLimiter:
    """Sliding window of monotonic timestamps, guarded by one lock."""

    def __init__(
        self,
        *,
        limit: int,
        window_s: float,
        max_keys: int = DEFAULT_MAX_KEYS,
        trusted_proxy_hops: int = 0,
    ) -> None:
        self._limit = limit
        self._window_s = window_s
        self._max_keys = max_keys
        self.trusted_proxy_hops = trusted_proxy_hops
        self._hits: OrderedDict[str, deque[float]] = OrderedDict()
        self._lock = asyncio.Lock()
        self._ops = 0

    async def hit(self, ip: str, now: float) -> RateDecision:
        if self._limit <= 0:
            return RateDecision(allowed=True, retry_after_s=0)
        async with self._lock:
            self._ops += 1
            bucket = self._hits.get(ip)
            if bucket is None:
                while len(self._hits) >= self._max_keys:
                    self._hits.popitem(last=False)
                bucket = deque()
                self._hits[ip] = bucket
            else:
                self._hits.move_to_end(ip)
            cutoff = now - self._window_s
            while bucket and bucket[0] <= cutoff:
                bucket.popleft()
            if self._ops % 32 == 0:
                self._sweep_oldest(cutoff, limit=8)
            if len(bucket) >= self._limit:
                retry_after_s = max(1, math.ceil(bucket[0] + self._window_s - now))
                return RateDecision(allowed=False, retry_after_s=retry_after_s)
            bucket.append(now)
            return RateDecision(allowed=True, retry_after_s=0)

    def _sweep_oldest(self, cutoff: float, *, limit: int) -> None:
        dead: list[str] = []
        for index, (key, bucket) in enumerate(self._hits.items()):
            if index >= limit:
                break
            while bucket and bucket[0] <= cutoff:
                bucket.popleft()
            if not bucket:
                dead.append(key)
        for key in dead:
            self._hits.pop(key, None)


async def enforce_ask_limit(request: Request) -> None:
    """Reject the ask when this process has already seen the address enough times."""
    limiter: AskLimiter = request.app.state.ask_limiter
    peer = request.client.host if request.client is not None else "unknown"
    ip = forwarded_client(
        peer,
        request.headers.get("x-forwarded-for"),
        limiter.trusted_proxy_hops,
    )
    decision = await limiter.hit(ip, time.monotonic())
    if decision.allowed:
        return
    raise HTTPException(
        status_code=429,
        detail="rate limit exceeded",
        headers={"Retry-After": str(decision.retry_after_s)},
    )
