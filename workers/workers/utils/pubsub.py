"""Worker-side Redis pub/sub — synchronous (used from Celery tasks)."""

from __future__ import annotations

import json
import os
from typing import Any

import redis

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")

_redis: redis.Redis | None = None


def _get_redis() -> redis.Redis:
    global _redis
    if _redis is None:
        _redis = redis.from_url(REDIS_URL, decode_responses=True)
    return _redis


def publish_ws(rabbit_hole_id: str, message: dict[str, Any]) -> None:
    """Publish a graph event to the WebSocket channel for this rabbit hole."""
    channel = f"rhm:graph:{rabbit_hole_id}"
    try:
        _get_redis().publish(channel, json.dumps(message))
    except Exception:
        pass  # WebSocket events are best-effort — never block a pipeline task
