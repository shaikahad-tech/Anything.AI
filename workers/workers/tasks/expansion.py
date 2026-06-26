"""
Topic Expansion Task.

1. Calls Claude with QUERY_EXPANSION_V1 prompt.
2. Fans out one crawl task per (search_angle, source_type).
3. Publishes job_progress events to Redis pub/sub.
"""

from __future__ import annotations

import json
import os
import uuid

import anthropic
import structlog
from celery import group

from workers.celery_app import app
from workers.utils.pubsub import publish_ws

log = structlog.get_logger(__name__)

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")


@app.task(
    bind=True,
    name="workers.tasks.expansion.expand_topic_task",
    max_retries=3,
    default_retry_delay=10,
)
def expand_topic_task(
    self,
    rabbit_hole_id: str,
    topic: str,
    depth: int,
    source_types: list[str],
) -> dict:
    """Expand a topic into search angles and dispatch crawl jobs."""
    try:
        log.info("expansion.start", rabbit_hole_id=rabbit_hole_id, topic=topic)
        publish_ws(rabbit_hole_id, {"type": "job_progress", "rabbit_hole_id": rabbit_hole_id, "payload": {"stage": "expansion", "status": "running"}, "timestamp": ""})

        prompt = _build_expansion_prompt(topic)
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        response = client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=2048,
            messages=[{"role": "user", "content": prompt}],
        )
        content = response.content[0].text.strip()

        # Extract JSON — handle markdown code blocks
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0].strip()
        elif "```" in content:
            content = content.split("```")[1].split("```")[0].strip()

        plan = json.loads(content)
        search_angles = plan.get("search_angles", [])

        # Fan out crawl tasks
        crawl_tasks = []
        for angle in search_angles:
            src_type = angle.get("source_type", "article")
            if src_type not in source_types and source_types:
                continue
            task_name = _task_for_source_type(src_type)
            if task_name:
                crawl_tasks.append(
                    app.signature(
                        task_name,
                        kwargs={
                            "rabbit_hole_id": rabbit_hole_id,
                            "query": angle["query"],
                            "depth": depth,
                        },
                    )
                )

        if crawl_tasks:
            group(crawl_tasks).apply_async()

        publish_ws(rabbit_hole_id, {
            "type": "job_progress",
            "rabbit_hole_id": rabbit_hole_id,
            "payload": {
                "stage": "expansion",
                "status": "completed",
                "search_angles": len(search_angles),
                "crawl_jobs_dispatched": len(crawl_tasks),
            },
            "timestamp": "",
        })

        log.info("expansion.done", rabbit_hole_id=rabbit_hole_id, jobs=len(crawl_tasks))
        return {"rabbit_hole_id": rabbit_hole_id, "jobs_dispatched": len(crawl_tasks)}

    except Exception as exc:
        log.error("expansion.error", rabbit_hole_id=rabbit_hole_id, error=str(exc))
        publish_ws(rabbit_hole_id, {"type": "job_failed", "rabbit_hole_id": rabbit_hole_id, "payload": {"stage": "expansion", "error": str(exc)}, "timestamp": ""})
        raise self.retry(exc=exc)


@app.task(
    bind=True,
    name="workers.tasks.expansion.expand_entity_task",
    max_retries=3,
    default_retry_delay=10,
)
def expand_entity_task(
    self,
    rabbit_hole_id: str,
    node_id: str,
    depth: int,
) -> dict:
    """Expand a specific entity node into additional crawl jobs."""
    try:
        # For entity expansion, dispatch article + reddit crawls for the entity name
        from workers.tasks.crawl_article import crawl_article_task
        from workers.tasks.crawl_reddit import crawl_reddit_task

        group([
            crawl_article_task.s(rabbit_hole_id=rabbit_hole_id, query=node_id, depth=depth),
            crawl_reddit_task.s(rabbit_hole_id=rabbit_hole_id, query=node_id, depth=depth),
        ]).apply_async()

        return {"rabbit_hole_id": rabbit_hole_id, "node_id": node_id}
    except Exception as exc:
        raise self.retry(exc=exc)


def _build_expansion_prompt(topic: str) -> str:
    from rhm_llm_prompts.prompts import QUERY_EXPANSION_V1
    return QUERY_EXPANSION_V1.format(topic=topic)


def _task_for_source_type(src_type: str) -> str | None:
    return {
        "article": "workers.tasks.crawl_article.crawl_article_task",
        "reddit": "workers.tasks.crawl_reddit.crawl_reddit_task",
        "youtube": "workers.tasks.crawl_youtube.crawl_youtube_task",
        "paper": "workers.tasks.crawl_papers.crawl_papers_task",
    }.get(src_type)
