"""
Celery application — broker: Redis, backend: Redis.
Queues:
  crawl        — topic expansion, crawl dispatch
  extraction   — NER + claim extraction
  contradiction — contradiction detection + credibility
"""

from __future__ import annotations

import os

from celery import Celery
from celery.signals import worker_ready
import structlog

log = structlog.get_logger(__name__)

BROKER_URL = os.environ.get("CELERY_BROKER_URL", "redis://localhost:6379/1")
RESULT_BACKEND = os.environ.get("CELERY_RESULT_BACKEND", "redis://localhost:6379/2")

app = Celery(
    "rhm",
    broker=BROKER_URL,
    backend=RESULT_BACKEND,
    include=[
        "workers.tasks.expansion",
        "workers.tasks.crawl_article",
        "workers.tasks.crawl_reddit",
        "workers.tasks.crawl_youtube",
        "workers.tasks.crawl_papers",
        "workers.tasks.extraction",
        "workers.tasks.contradiction",
        "workers.tasks.timeline",
        "workers.tasks.credibility",
    ],
)

app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_acks_late=True,           # don't ack until task finishes (survives worker restart)
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,  # one task at a time per worker slot — important for long-running crawls
    task_routes={
        "workers.tasks.expansion.*": {"queue": "crawl"},
        "workers.tasks.crawl_*": {"queue": "crawl"},
        "workers.tasks.extraction.*": {"queue": "extraction"},
        "workers.tasks.contradiction.*": {"queue": "contradiction"},
        "workers.tasks.timeline.*": {"queue": "extraction"},
        "workers.tasks.credibility.*": {"queue": "contradiction"},
    },
    task_default_retry_delay=30,
    task_max_retries=5,
    broker_transport_options={"visibility_timeout": 3600},
)


@worker_ready.connect
def on_ready(sender, **kwargs):  # type: ignore[no-untyped-def]
    log.info("celery.worker.ready", hostname=sender.hostname)
