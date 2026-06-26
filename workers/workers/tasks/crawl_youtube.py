"""
YouTube Crawler Task.

Uses YouTube Data API v3 for metadata (search + video details).
Uses youtube-transcript-api for captions as a supplemental source.
Rate-limit aware: respects YouTube API quota (10,000 units/day by default).
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime

import structlog
from googleapiclient.discovery import build
from youtube_transcript_api import YouTubeTranscriptApi, TranscriptsDisabled, NoTranscriptFound

from workers.celery_app import app
from workers.utils.pubsub import publish_ws
from workers.utils.s3 import upload_blob
from workers.utils.db import insert_source

log = structlog.get_logger(__name__)

YOUTUBE_API_KEY = os.environ.get("YOUTUBE_API_KEY", "")
MAX_RESULTS = 5


@app.task(
    bind=True,
    name="workers.tasks.crawl_youtube.crawl_youtube_task",
    max_retries=3,
    default_retry_delay=60,
    rate_limit="5/m",  # conservative — YouTube quota is precious
)
def crawl_youtube_task(
    self,
    rabbit_hole_id: str,
    query: str,
    depth: int = 1,
) -> dict:
    from workers.tasks.extraction import extraction_task

    if not YOUTUBE_API_KEY:
        log.warning("crawl_youtube.no_api_key")
        return {"rabbit_hole_id": rabbit_hole_id, "sources_created": 0}

    try:
        log.info("crawl_youtube.start", rabbit_hole_id=rabbit_hole_id, query=query)
        youtube = build("youtube", "v3", developerKey=YOUTUBE_API_KEY)

        search_response = youtube.search().list(
            q=query,
            part="id,snippet",
            type="video",
            maxResults=MAX_RESULTS,
            relevanceLanguage="en",
        ).execute()

        sources_created = 0

        for item in search_response.get("items", []):
            video_id = item["id"]["videoId"]
            snippet = item["snippet"]
            url = f"https://www.youtube.com/watch?v={video_id}"

            # Try to get transcript
            transcript_text = ""
            try:
                transcript = YouTubeTranscriptApi.get_transcript(video_id)
                transcript_text = " ".join(t["text"] for t in transcript)
            except (TranscriptsDisabled, NoTranscriptFound):
                log.info("crawl_youtube.no_transcript", video_id=video_id)
                transcript_text = snippet.get("description", "")
            except Exception as exc:
                log.warning("crawl_youtube.transcript_error", video_id=video_id, error=str(exc))
                transcript_text = snippet.get("description", "")

            if not transcript_text or len(transcript_text) < 50:
                continue

            full_text = (
                f"Title: {snippet['title']}\n"
                f"Channel: {snippet['channelTitle']}\n"
                f"Published: {snippet['publishedAt']}\n\n"
                f"Description:\n{snippet.get('description', '')}\n\n"
                f"Transcript:\n{transcript_text}"
            )

            blob_key = f"blobs/{rabbit_hole_id}/youtube_{video_id}.txt"
            upload_blob(blob_key, full_text.encode())

            source_id = str(uuid.uuid4())
            insert_source({
                "id": source_id,
                "rabbit_hole_id": rabbit_hole_id,
                "url": url,
                "source_type": "youtube",
                "title": snippet["title"],
                "author": snippet.get("channelTitle"),
                "published_at": snippet.get("publishedAt"),
                "s3_blob_key": blob_key,
            })

            publish_ws(rabbit_hole_id, {
                "type": "node_added",
                "rabbit_hole_id": rabbit_hole_id,
                "payload": {
                    "id": source_id,
                    "type": "sourceNode",
                    "label": snippet["title"],
                    "url": url,
                    "source_type": "youtube",
                },
                "timestamp": datetime.utcnow().isoformat(),
            })

            extraction_task.apply_async(
                kwargs={
                    "rabbit_hole_id": rabbit_hole_id,
                    "source_id": source_id,
                    "url": url,
                    "text": full_text,
                    "source_type": "youtube",
                    "published_at": snippet.get("publishedAt"),
                },
                queue="extraction",
            )
            sources_created += 1

        log.info("crawl_youtube.done", rabbit_hole_id=rabbit_hole_id, sources=sources_created)
        return {"rabbit_hole_id": rabbit_hole_id, "sources_created": sources_created}

    except Exception as exc:
        log.error("crawl_youtube.error", error=str(exc))
        raise self.retry(exc=exc)
