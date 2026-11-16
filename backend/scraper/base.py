from __future__ import annotations
import asyncio, random, time
from typing import Protocol
from schemas import JobPosting


class RateLimiter:
    """Min interval between actions with random jitter, per source."""
    def __init__(self, min_s: float = 3.0, max_s: float = 8.0):
        self.min_s, self.max_s, self._last = min_s, max_s, 0.0

    async def wait(self):
        gap = random.uniform(self.min_s, self.max_s)
        delta = time.monotonic() - self._last
        if delta < gap: await asyncio.sleep(gap - delta)
        self._last = time.monotonic()


class Scraper(Protocol):
    source: str
    async def search(self, titles: list[str], locations: list[str], limit: int) -> list[JobPosting]: ...
