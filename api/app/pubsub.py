"""
Redis pub/sub abstraction for WebSocket fanout.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import AsyncGenerator

import redis.asyncio as redis

from app.config import settings

_redis_client: redis.Redis | None = None


def get_redis() -> redis.Redis:
    global _redis_client
    if _redis_client is None:
        _redis_client = redis.from_url(settings.REDIS_URL, decode_responses=False)
    return _redis_client


class PubSub:
    def __init__(self) -> None:
        self._redis = get_redis()

    async def publish(self, channel: str, message: str | bytes) -> None:
        await self._redis.publish(channel, message)

    @asynccontextmanager
    async def subscribe(self, channel: str) -> AsyncGenerator[asyncio.Queue, None]:  # type: ignore[misc]
        queue: asyncio.Queue[str | bytes] = asyncio.Queue(maxsize=512)
        ps = self._redis.pubsub()
        await ps.subscribe(channel)

        async def _reader() -> None:
            async for message in ps.listen():
                if message["type"] == "message":
                    try:
                        queue.put_nowait(message["data"])
                    except asyncio.QueueFull:
                        pass  # drop oldest — client will catch up via REST on reconnect

        task = asyncio.ensure_future(_reader())
        try:
            yield queue
        finally:
            task.cancel()
            await ps.unsubscribe(channel)
            await ps.close()
