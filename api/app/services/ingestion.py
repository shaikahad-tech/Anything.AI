"""
Ingestion Orchestrator Service.

Receives a topic → calls LLM for query expansion → dispatches Celery crawl jobs.
Tracks job DAG in PostgreSQL. Emits WebSocket events via Redis pub/sub.
"""

from __future__ import annotations

import json
import uuid
from typing import TYPE_CHECKING

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models import Job, RabbitHole
from app.pubsub import PubSub

if TYPE_CHECKING:
    from rhm_shared_types.models import SourceType

log = structlog.get_logger(__name__)


class IngestionService:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db
        self._pubsub = PubSub()

    async def start(
        self,
        rabbit_hole_id: uuid.UUID,
        topic: str,
        depth: int,
        source_types: list[SourceType],
    ) -> None:
        """
        1. Expand the topic into search angles via LLM.
        2. Dispatch one Celery crawl job per (search_angle, source_type) pair.
        3. Record each job in PostgreSQL.
        """
        from workers.tasks.expansion import expand_topic_task

        # Dispatch the expansion task — it will fan out crawl tasks itself
        task = expand_topic_task.apply_async(
            kwargs={
                "rabbit_hole_id": str(rabbit_hole_id),
                "topic": topic,
                "depth": depth,
                "source_types": [st.value for st in source_types],
            },
            queue="crawl",
        )

        job = Job(
            rabbit_hole_id=rabbit_hole_id,
            celery_task_id=task.id,
            job_type="expansion",
            status="running",
            query=topic,
        )
        self._db.add(job)

        await self._pubsub.publish(
            f"rhm:graph:{rabbit_hole_id}",
            json.dumps({
                "type": "job_progress",
                "rabbit_hole_id": str(rabbit_hole_id),
                "payload": {"stage": "expansion", "status": "running"},
                "timestamp": "",
            }),
        )

        log.info("ingestion.started", rabbit_hole_id=str(rabbit_hole_id), topic=topic)

    async def expand_node(
        self, rh: RabbitHole, node_id: str, depth: int
    ) -> None:
        """Trigger a deeper crawl on a specific entity node."""
        from workers.tasks.expansion import expand_entity_task

        task = expand_entity_task.apply_async(
            kwargs={
                "rabbit_hole_id": str(rh.id),
                "node_id": node_id,
                "depth": depth,
            },
            queue="crawl",
        )

        job = Job(
            rabbit_hole_id=rh.id,
            celery_task_id=task.id,
            job_type="entity_expansion",
            status="running",
            query=node_id,
        )
        self._db.add(job)
        await self._db.flush()
        log.info("node.expanded", rabbit_hole_id=str(rh.id), node_id=node_id)
