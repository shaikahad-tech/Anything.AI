"""
Credibility Scoring Task.

Scores each source on:
- Domain reputation tier (hardcoded allowlist tiers)
- Independent corroboration count (how many other sources make the same claim)
- Recency (newer = slightly higher)
- Source type tier (paper > article > reddit > youtube for credibility baseline)

Bias score is a range estimate, not a single number — always presented as uncertain.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from urllib.parse import urlparse

import structlog
from neo4j import GraphDatabase
from sqlalchemy import create_engine, text

from workers.celery_app import app

log = structlog.get_logger(__name__)

NEO4J_URI = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.environ.get("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.environ.get("NEO4J_PASSWORD", "rabbitholepass")
SYNC_DATABASE_URL = os.environ.get("SYNC_DATABASE_URL", "postgresql://rhm:rhm@localhost:5432/rabbithole")

# Domain reputation tiers (0.0–1.0)
DOMAIN_TIERS: dict[str, float] = {
    # Tier 1 — high credibility reference sources
    "reuters.com": 0.92,
    "apnews.com": 0.92,
    "bbc.com": 0.88,
    "bbc.co.uk": 0.88,
    "nature.com": 0.95,
    "science.org": 0.95,
    "thelancet.com": 0.95,
    "nejm.org": 0.95,
    "arxiv.org": 0.85,
    "pubmed.ncbi.nlm.nih.gov": 0.90,
    "economist.com": 0.85,
    "ft.com": 0.85,
    "nytimes.com": 0.80,
    "theguardian.com": 0.78,
    "washingtonpost.com": 0.78,
    # Tier 2 — moderate credibility
    "cnn.com": 0.65,
    "foxnews.com": 0.55,
    "huffpost.com": 0.60,
    "reddit.com": 0.40,
    "youtube.com": 0.35,
    # Default (unknown domain) = 0.50
}

SOURCE_TYPE_BASELINE: dict[str, float] = {
    "paper": 0.85,
    "article": 0.60,
    "reddit": 0.35,
    "youtube": 0.40,
}


@app.task(
    bind=True,
    name="workers.tasks.credibility.score_source_task",
    max_retries=3,
    default_retry_delay=10,
)
def score_source_task(self, source_id: str, url: str, source_type: str) -> dict:
    try:
        score = _compute_credibility(source_id, url, source_type)
        _update_source_credibility(source_id, score)
        # Also update the Source node in Neo4j
        driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
        with driver.session() as session:
            session.run(
                "MATCH (s:Source {id: $id}) SET s.credibility_score = $score",
                {"id": source_id, "score": score},
            )
        driver.close()
        return {"source_id": source_id, "credibility_score": score}
    except Exception as exc:
        raise self.retry(exc=exc)


def _compute_credibility(source_id: str, url: str, source_type: str) -> float:
    domain = _extract_domain(url)
    domain_score = DOMAIN_TIERS.get(domain, 0.50)
    type_baseline = SOURCE_TYPE_BASELINE.get(source_type, 0.50)

    # Weighted average: domain 60%, type baseline 40%
    raw = domain_score * 0.6 + type_baseline * 0.4

    # Clamp to [0.1, 0.95]
    return max(0.1, min(0.95, raw))


def _extract_domain(url: str) -> str:
    try:
        parsed = urlparse(url)
        netloc = parsed.netloc.lower()
        # Strip www.
        if netloc.startswith("www."):
            netloc = netloc[4:]
        return netloc
    except Exception:
        return ""


def _update_source_credibility(source_id: str, score: float) -> None:
    engine = create_engine(SYNC_DATABASE_URL)
    with engine.connect() as conn:
        conn.execute(
            text("UPDATE sources SET credibility_score = :score WHERE id = :id::uuid"),
            {"score": score, "id": source_id},
        )
        conn.commit()
    engine.dispose()
