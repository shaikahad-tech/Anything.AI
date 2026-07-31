"""
Article Crawler Task.

Uses trafilatura for clean text extraction from news/blog articles.
Playwright is used as fallback for JS-heavy pages.
Always checks robots.txt via protego before fetching.
Raw HTML is stored in S3/MinIO for audit trail and re-processing.
"""

from __future__ import annotations

import hashlib
import os
import uuid
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlparse

import httpx
import structlog
import trafilatura
from protego import Protego

from workers.celery_app import app
from workers.utils.pubsub import publish_ws
from workers.utils.s3 import upload_blob
from workers.utils.db import update_job_status, insert_source

log = structlog.get_logger(__name__)

SEARCH_API_KEY = os.environ.get("SERPER_API_KEY", "")  # or use SerpAPI / DuckDuckGo
USER_AGENT = "RabbitHoleMapper/1.0 (research tool; respects robots.txt)"


@app.task(
    bind=True,
    name="workers.tasks.crawl_article.crawl_article_task",
    max_retries=3,
    default_retry_delay=30,
    rate_limit="30/m",  # 30 requests per minute max
)
def crawl_article_task(
    self,
    rabbit_hole_id: str,
    query: str,
    depth: int = 1,
) -> dict:
    import asyncio
    return asyncio.run(_crawl_article_async(self, rabbit_hole_id, query, depth))


async def _crawl_article_async(task, rabbit_hole_id: str, query: str, depth: int) -> dict:
    from workers.tasks.extraction import extraction_task

    log.info("crawl_article.start", rabbit_hole_id=rabbit_hole_id, query=query)

    try:
        urls = await _search_urls(query, max_results=5)
        sources_created = 0

        for url in urls:
            try:
                if not await _robots_allowed(url):
                    log.info("crawl_article.robots_blocked", url=url)
                    continue

                raw_html, text, metadata = await _fetch_and_extract(url)
                if not text or len(text) < 100:
                    continue

                # Store raw HTML in S3
                blob_key = f"blobs/{rabbit_hole_id}/{hashlib.sha256(url.encode()).hexdigest()}.html"
                await upload_blob_async(blob_key, raw_html.encode())

                source_id = str(uuid.uuid4())
                await insert_source_async({
                    "id": source_id,
                    "rabbit_hole_id": rabbit_hole_id,
                    "url": url,
                    "source_type": "article",
                    "title": metadata.get("title"),
                    "author": metadata.get("author"),
                    "published_at": metadata.get("date"),
                    "s3_blob_key": blob_key,
                })

                publish_ws(rabbit_hole_id, {
                    "type": "node_added",
                    "rabbit_hole_id": rabbit_hole_id,
                    "payload": {
                        "id": source_id,
                        "type": "sourceNode",
                        "label": metadata.get("title") or url,
                        "url": url,
                        "source_type": "article",
                    },
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })

                # Dispatch extraction
                extraction_task.apply_async(
                    kwargs={
                        "rabbit_hole_id": rabbit_hole_id,
                        "source_id": source_id,
                        "url": url,
                        "text": text,
                        "source_type": "article",
                        "published_at": metadata.get("date"),
                    },
                    queue="extraction",
                )
                sources_created += 1

            except Exception as exc:
                log.warning("crawl_article.url_error", url=url, error=str(exc))
                continue

        log.info("crawl_article.done", rabbit_hole_id=rabbit_hole_id, sources=sources_created)
        return {"rabbit_hole_id": rabbit_hole_id, "sources_created": sources_created}

    except Exception as exc:
        log.error("crawl_article.error", error=str(exc))
        raise task.retry(exc=exc)


async def _search_urls(query: str, max_results: int = 5) -> list[str]:
    """
    Search for article URLs. Uses Serper.dev if API key is set,
    falls back to DuckDuckGo HTML scrape.
    """
    if SEARCH_API_KEY:
        return await _serper_search(query, max_results)
    return await _ddg_search(query, max_results)


async def _serper_search(query: str, max_results: int) -> list[str]:
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(
            "https://google.serper.dev/search",
            headers={"X-API-KEY": SEARCH_API_KEY, "Content-Type": "application/json"},
            json={"q": query, "num": max_results},
        )
        resp.raise_for_status()
        data = resp.json()
        return [r["link"] for r in data.get("organic", [])[:max_results] if "link" in r]


async def _ddg_search(query: str, max_results: int) -> list[str]:
    """DuckDuckGo HTML fallback (no official API needed)."""
    from urllib.parse import quote_plus
    async with httpx.AsyncClient(
        headers={"User-Agent": USER_AGENT},
        follow_redirects=True,
        timeout=10.0,
    ) as client:
        try:
            resp = await client.get(
                f"https://html.duckduckgo.com/html/?q={quote_plus(query)}"
            )
            # Very basic extraction — real impl should use BeautifulSoup
            import re
            urls = re.findall(r'href="(https?://[^"&]+)"', resp.text)
            seen: set[str] = set()
            results = []
            for u in urls:
                parsed = urlparse(u)
                if parsed.netloc and "duckduckgo" not in parsed.netloc:
                    if u not in seen:
                        seen.add(u)
                        results.append(u)
                if len(results) >= max_results:
                    break
            return results
        except Exception:
            return []


async def _robots_allowed(url: str) -> bool:
    try:
        parsed = urlparse(url)
        robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(robots_url, follow_redirects=True)
            if resp.status_code != 200:
                return True  # no robots.txt = allowed
            robot = Protego.parse(resp.text)
            return robot.can_fetch(url, USER_AGENT)
    except Exception:
        return True  # on error, assume allowed


async def _fetch_and_extract(url: str) -> tuple[str, str, dict]:
    """Fetch URL and extract clean text via trafilatura. Falls back to Playwright."""
    try:
        async with httpx.AsyncClient(
            headers={"User-Agent": USER_AGENT},
            follow_redirects=True,
            timeout=20.0,
        ) as client:
            resp = await client.get(url)
            raw_html = resp.text

        text = trafilatura.extract(
            raw_html,
            include_links=False,
            include_images=False,
            include_tables=False,
            no_fallback=False,
        ) or ""
        metadata = trafilatura.extract_metadata(raw_html)
        meta_dict = {}
        if metadata:
            meta_dict = {
                "title": metadata.title,
                "author": metadata.author,
                "date": metadata.date,
            }
        return raw_html, text, meta_dict
    except Exception:
        # Playwright fallback for JS-heavy pages
        return await _playwright_fetch(url)


async def _playwright_fetch(url: str) -> tuple[str, str, dict]:
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        try:
            page = await browser.new_page(user_agent=USER_AGENT)
            await page.goto(url, wait_until="networkidle", timeout=30000)
            html = await page.content()
            text = await page.inner_text("body")
            title = await page.title()
            return html, text[:50000], {"title": title}
        finally:
            await browser.close()


# ---------------------------------------------------------------------------
# Async wrappers for sync utils (crawl_article runs in asyncio.run)
# ---------------------------------------------------------------------------


async def upload_blob_async(key: str, data: bytes) -> str:
    """Run sync S3 upload in a thread to avoid blocking the event loop."""
    import asyncio
    return await asyncio.get_event_loop().run_in_executor(None, upload_blob, key, data)


async def insert_source_async(data: dict) -> None:
    """Run sync DB insert in a thread to avoid blocking the event loop."""
    import asyncio
    await asyncio.get_event_loop().run_in_executor(None, insert_source, data)
