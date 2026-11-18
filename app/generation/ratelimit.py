"""Small in-process sliding-window limiter, so a shared free tier is not hammered."""
from __future__ import annotations

import threading
import time
from collections import deque

from app.generation.provider import RateLimitedError


class RateLimiter:
    """At most `per_minute` acquisitions in any 60 s window. Waits up to `max_wait` s, else raises."""

    def __init__(self, per_minute: int = 30, max_wait: float = 20.0, *, clock=time.monotonic, sleep=time.sleep):
        self.per_minute = max(1, int(per_minute))
        self.max_wait = max_wait
        self._clock, self._sleep = clock, sleep
        self._hits: deque[float] = deque()
        self._lock = threading.Lock()

    def _prune(self, now: float) -> None:
        while self._hits and now - self._hits[0] >= 60.0:
            self._hits.popleft()

    def remaining(self) -> int:
        with self._lock:
            self._prune(self._clock())
            return self.per_minute - len(self._hits)

    def acquire(self) -> None:
        while True:
            with self._lock:
                now = self._clock()
                self._prune(now)
                if len(self._hits) < self.per_minute:
                    self._hits.append(now)
                    return
                wait = 60.0 - (now - self._hits[0])
            if wait > self.max_wait:
                raise RateLimitedError(f"rate limited, retrying in {wait:.0f}s", retry_after=wait)
            self._sleep(max(wait, 0.0) + 0.01)
