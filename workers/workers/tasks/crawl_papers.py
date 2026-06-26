"""
Academic Papers Crawler Task.

Uses arXiv API and Semantic Scholar API.
Does NOT scrape PDFs — uses abstracts as the text source.
Full text extraction from PDFs is a future enhancement.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime

import arxiv
import httpx
import structlog

from workers.celery_app import app
from workers.utils.pubsub import publish_ws
from workers.utils.db import insert_source

log = structlog.get_logger(__name__)

SEMANTIC_SCHOLAR_API_KEY = os.environ.get("SEMANTIC_SCHOLAR_API_KEY", "")


@app.task(
    bind=True,
    name="workers.tasks.crawl_papers.crawl_papers_task",
    max_retries=3,
    default_retry_delay=30,
    rate_limit="10/m",
)
def crawl_papers_task(
    self,
    rabbit_hole_id: str,
    query: str,
    depth: int = 1,
) -> dict:
    from workers.tasks.extraction import extraction_task

    try:
        log.info("crawl_papers.start", rabbit_hole_id=rabbit_hole_id, query=query)
        sources_created = 0

        # arXiv search
        arxiv_client = arxiv.Client()
        search = arxiv.Search(query=query, max_results=3, sort_by=arxiv.SortCriterion.Relevance)
        for paper in arxiv_client.results(search):
            text = (
                f"Title: {paper.title}\n"
                f"Authors: {', '.join(str(a) for a in paper.authors)}\n"
                f"Published: {paper.published.isoformat() if paper.published else 'unknown'}\n"
                f"Categories: {', '.join(paper.categories)}\n\n"
                f"Abstract:\n{paper.summary}"
            )
            url = str(paper.entry_id)
            source_id = str(uuid.uuid4())
            insert_source({
                "id": source_id,
                "rabbit_hole_id": rabbit_hole_id,
                "url": url,
                "source_type": "paper",
                "title": paper.title,
                "author": ", ".join(str(a) for a in paper.authors[:3]),
                "published_at": paper.published.isoformat() if paper.published else None,
                "s3_blob_key": None,
            })

            publish_ws(rabbit_hole_id, {
                "type": "node_added",
                "rabbit_hole_id": rabbit_hole_id,
                "payload": {"id": source_id, "type": "sourceNode", "label": paper.title, "url": url, "source_type": "paper"},
                "timestamp": datetime.utcnow().isoformat(),
            })

            extraction_task.apply_async(
                kwargs={
                    "rabbit_hole_id": rabbit_hole_id,
                    "source_id": source_id,
                    "url": url,
                    "text": text,
                    "source_type": "paper",
                    "published_at": paper.published.isoformat() if paper.published else None,
                },
                queue="extraction",
            )
            sources_created += 1

        log.info("crawl_papers.done", rabbit_hole_id=rabbit_hole_id, sources=sources_created)
        return {"rabbit_hole_id": rabbit_hole_id, "sources_created": sources_created}

    except Exception as exc:
        log.error("crawl_papers.error", error=str(exc))
        raise self.retry(exc=exc)
