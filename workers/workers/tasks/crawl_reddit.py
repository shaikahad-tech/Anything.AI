"""
Reddit Crawler Task.

Uses PRAW (official API) — respects Reddit ToS.
Fetches top posts matching a query and their top comments.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

import praw
import structlog

from workers.celery_app import app
from workers.utils.pubsub import publish_ws
from workers.utils.s3 import upload_blob
from workers.utils.db import insert_source

log = structlog.get_logger(__name__)


def _get_reddit() -> praw.Reddit:
    return praw.Reddit(
        client_id=os.environ.get("REDDIT_CLIENT_ID", ""),
        client_secret=os.environ.get("REDDIT_CLIENT_SECRET", ""),
        user_agent=os.environ.get("REDDIT_USER_AGENT", "RabbitHoleMapper/1.0"),
        read_only=True,
    )


@app.task(
    bind=True,
    name="workers.tasks.crawl_reddit.crawl_reddit_task",
    max_retries=3,
    default_retry_delay=60,
    rate_limit="10/m",
)
def crawl_reddit_task(
    self,
    rabbit_hole_id: str,
    query: str,
    depth: int = 1,
) -> dict:
    from workers.tasks.extraction import extraction_task

    try:
        log.info("crawl_reddit.start", rabbit_hole_id=rabbit_hole_id, query=query)
        reddit = _get_reddit()
        sources_created = 0

        # Search across all of Reddit
        for submission in reddit.subreddit("all").search(query, sort="relevance", limit=5):
            try:
                # Build full text: title + selftext + top comments
                submission.comments.replace_more(limit=0)
                top_comments = [c.body for c in submission.comments.list()[:20] if hasattr(c, "body")]
                full_text = f"Title: {submission.title}\n\n"
                if submission.selftext:
                    full_text += f"{submission.selftext}\n\n"
                if top_comments:
                    full_text += "Top comments:\n" + "\n---\n".join(top_comments)

                url = f"https://www.reddit.com{submission.permalink}"
                blob_key = f"blobs/{rabbit_hole_id}/reddit_{submission.id}.txt"

                upload_blob(blob_key, full_text.encode())

                source_id = str(uuid.uuid4())
                insert_source({
                    "id": source_id,
                    "rabbit_hole_id": rabbit_hole_id,
                    "url": url,
                    "source_type": "reddit",
                    "title": submission.title,
                    "author": str(submission.author) if submission.author else None,
                    "published_at": datetime.fromtimestamp(submission.created_utc, tz=timezone.utc).isoformat(),
                    "s3_blob_key": blob_key,
                })

                publish_ws(rabbit_hole_id, {
                    "type": "node_added",
                    "rabbit_hole_id": rabbit_hole_id,
                    "payload": {
                        "id": source_id,
                        "type": "sourceNode",
                        "label": submission.title,
                        "url": url,
                        "source_type": "reddit",
                    },
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })

                extraction_task.apply_async(
                    kwargs={
                        "rabbit_hole_id": rabbit_hole_id,
                        "source_id": source_id,
                        "url": url,
                        "text": full_text,
                        "source_type": "reddit",
                        "published_at": datetime.fromtimestamp(submission.created_utc, tz=timezone.utc).isoformat(),
                    },
                    queue="extraction",
                )
                sources_created += 1

            except Exception as exc:
                log.warning("crawl_reddit.submission_error", error=str(exc))
                continue

        log.info("crawl_reddit.done", rabbit_hole_id=rabbit_hole_id, sources=sources_created)
        return {"rabbit_hole_id": rabbit_hole_id, "sources_created": sources_created}

    except Exception as exc:
        log.error("crawl_reddit.error", error=str(exc))
        raise self.retry(exc=exc)
