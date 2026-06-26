"""Synchronous database utilities for workers (Celery tasks run sync)."""

from __future__ import annotations

import os
from typing import Any

from sqlalchemy import create_engine, text

SYNC_DATABASE_URL = os.environ.get(
    "SYNC_DATABASE_URL", "postgresql://rhm:rhm@localhost:5432/rabbithole"
)

_engine = None


def _get_engine():
    global _engine
    if _engine is None:
        _engine = create_engine(SYNC_DATABASE_URL, pool_pre_ping=True, pool_size=5)
    return _engine


def update_job_status(celery_task_id: str, status: str, error: str | None = None) -> None:
    engine = _get_engine()
    with engine.connect() as conn:
        conn.execute(
            text(
                """
                UPDATE jobs SET status = :status, error_message = :error,
                  completed_at = CASE WHEN :status IN ('completed','failed') THEN NOW() ELSE completed_at END
                WHERE celery_task_id = :task_id
                """
            ),
            {"status": status, "error": error, "task_id": celery_task_id},
        )
        conn.commit()


def insert_source(data: dict[str, Any]) -> None:
    """Synchronously insert a source row and increment rabbit hole source count."""
    engine = _get_engine()
    with engine.connect() as conn:
        conn.execute(
            text(
                """
                INSERT INTO sources (id, rabbit_hole_id, url, source_type, title, author, published_at, s3_blob_key)
                VALUES (:id::uuid, :rabbit_hole_id::uuid, :url, :source_type, :title, :author,
                        :published_at::timestamptz, :s3_blob_key)
                ON CONFLICT (id) DO NOTHING
                """
            ),
            data,
        )
        # Increment source_count on the rabbit_hole
        conn.execute(
            text(
                "UPDATE rabbit_holes SET source_count = source_count + 1 WHERE id = :rhid::uuid"
            ),
            {"rhid": data["rabbit_hole_id"]},
        )
        conn.commit()

    # Fire credibility scoring
    from workers.tasks.credibility import score_source_task
    score_source_task.apply_async(
        kwargs={
            "source_id": data["id"],
            "url": data["url"],
            "source_type": data["source_type"],
        },
        queue="contradiction",
    )
