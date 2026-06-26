"""
Simple per-user rate limiting middleware using Redis sliding window.
Heavy operations (rabbit hole creation, LLM calls) are rate-limited per user.
"""

from __future__ import annotations

import time

from fastapi import HTTPException, Request, status

from app.pubsub import get_redis


class RateLimiter:
    def __init__(self, key_prefix: str, max_requests: int, window_seconds: int) -> None:
        self._prefix = key_prefix
        self._max = max_requests
        self._window = window_seconds

    async def check(self, identifier: str) -> None:
        redis = get_redis()
        key = f"ratelimit:{self._prefix}:{identifier}"
        now = time.time()
        window_start = now - self._window

        pipe = redis.pipeline()
        # Remove expired entries
        pipe.zremrangebyscore(key, 0, window_start)
        # Add current request
        pipe.zadd(key, {str(now): now})
        # Count requests in window
        pipe.zcard(key)
        # Set expiry
        pipe.expire(key, self._window)
        results = await pipe.execute()
        count = results[2]

        if count > self._max:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Rate limit exceeded. Max {self._max} requests per {self._window}s.",
                headers={"Retry-After": str(self._window)},
            )


# Singleton rate limiters
rabbit_hole_limiter = RateLimiter("rabbit_holes", max_requests=10, window_seconds=3600)
llm_limiter = RateLimiter("llm_calls", max_requests=50, window_seconds=3600)
