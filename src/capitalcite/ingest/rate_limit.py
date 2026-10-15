"""Async spacing so EDGAR stays at or under 10 requests per second."""

from __future__ import annotations

import asyncio
import math
import time
from collections.abc import Awaitable, Callable


class RateLimiter:
    """Allow at most ``max_per_second`` acquires, spaced evenly.

    Ten requests per second is the SEC fair-access ceiling. Spacing at
    1/max seconds means we never burst 11 into a rolling one-second window.
    """

    def __init__(
        self,
        max_per_second: float = 10.0,
        *,
        clock: Callable[[], float] | None = None,
        sleeper: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        if max_per_second <= 0:
            raise ValueError("max_per_second must be positive")
        self._min_interval = 1.0 / max_per_second
        self._clock = clock or time.monotonic
        self._sleep = sleeper or asyncio.sleep
        self._last = -math.inf
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            now = self._clock()
            wait = self._min_interval - (now - self._last)
            if wait > 0:
                await self._sleep(wait)
                now = self._clock()
            self._last = now
