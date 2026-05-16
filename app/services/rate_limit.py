import asyncio
import time
from collections import defaultdict, deque


class InMemoryRateLimiter:
    def __init__(self):
        self._buckets = defaultdict(deque)
        self._lock = asyncio.Lock()

    async def allow(self, key: str, max_requests: int, window_seconds: int) -> tuple[bool, int]:
        now = time.monotonic()
        async with self._lock:
            bucket = self._buckets[key]
            cutoff = now - window_seconds
            while bucket and bucket[0] < cutoff:
                bucket.popleft()
            if len(bucket) >= max_requests:
                retry_after = max(1, int(window_seconds - (now - bucket[0])))
                return False, retry_after
            bucket.append(now)
            return True, 0


telegram_rate_limiter = InMemoryRateLimiter()
vk_rate_limiter = InMemoryRateLimiter()