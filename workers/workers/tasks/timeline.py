"""
Timeline Extraction Task.

1. Retrieves all claims for a rabbit hole
2. LLM extracts and normalizes temporal expressions
3. Clusters into Event nodes in Neo4j
4. Links events with PRECEDES edges
"""

from __future__ import annotations

import json
import os
import uuid
from collections import defaultdict

import anthropic
import structlog
from neo4j import GraphDatabase
from tenacity import retry, stop_after_attempt, wait_exponential

from workers.celery_app import app

log = structlog.get_logger(__name__)

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
NEO4J_URI = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.environ.get("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.environ.get("NEO4J_PASSWORD", "rabbitholepass")


@app.task(
    bind=True,
    name="workers.tasks.timeline.timeline_task",
    max_retries=3,
    default_retry_delay=30,
)
def timeline_task(self, rabbit_hole_id: str) -> dict:
    try:
        log.info("timeline.start", rabbit_hole_id=rabbit_hole_id)
        driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))

        # Get claims with temporal expressions
        with driver.session() as session:
            result = session.run(
                """
                MATCH (c:Claim {rabbit_hole_id: $rabbit_hole_id})
                WHERE c.text CONTAINS 'in 20' OR c.text CONTAINS 'in 19'
                   OR c.text =~ '.*(January|February|March|April|May|June|July|August|September|October|November|December).*'
                   OR c.text =~ '.*(last year|this year|last month|yesterday|recently|since|before|after|during|when).*'
                RETURN c.id AS id, c.text AS text
                LIMIT 100
                """,
                {"rabbit_hole_id": rabbit_hole_id},
            )
            claims = [{"id": r["id"], "text": r["text"]} for r in result]

        if not claims:
            driver.close()
            return {"rabbit_hole_id": rabbit_hole_id, "events_created": 0}

        # Batch claims for LLM temporal extraction
        batch_size = 20
        all_temporal = []
        for i in range(0, len(claims), batch_size):
            batch = claims[i:i + batch_size]
            temporal = _extract_temporal(batch)
            all_temporal.extend(temporal)

        # Cluster by date and create Event nodes
        date_clusters: dict[str, list[dict]] = defaultdict(list)
        for tc in all_temporal:
            if tc.get("date_start"):
                date_key = tc["date_start"][:7]  # group by year-month
                date_clusters[date_key].append(tc)

        events_created = 0
        event_ids_ordered = []

        with driver.session() as session:
            for date_key, temporal_claims in sorted(date_clusters.items()):
                event_id = str(uuid.uuid4())
                event_title = temporal_claims[0].get("event_title", f"Event ~{date_key}")
                date_start = temporal_claims[0].get("date_start")
                date_end = temporal_claims[-1].get("date_end") or date_start

                session.run(
                    """
                    MERGE (e:Event {id: $id})
                    SET e.title = $title,
                        e.date_start = $date_start,
                        e.date_end = $date_end,
                        e.rabbit_hole_id = $rabbit_hole_id
                    """,
                    {
                        "id": event_id,
                        "title": event_title,
                        "date_start": date_start,
                        "date_end": date_end,
                        "rabbit_hole_id": rabbit_hole_id,
                    },
                )

                # Link claims to this event
                for tc in temporal_claims:
                    session.run(
                        """
                        MATCH (c:Claim {id: $claim_id})
                        MATCH (e:Event {id: $event_id})
                        MERGE (c)-[:MENTIONS]->(e)
                        """,
                        {"claim_id": tc["claim_id"], "event_id": event_id},
                    )

                event_ids_ordered.append(event_id)
                events_created += 1

            # Create PRECEDES edges between chronologically ordered events
            for i in range(len(event_ids_ordered) - 1):
                session.run(
                    """
                    MATCH (e1:Event {id: $id1})
                    MATCH (e2:Event {id: $id2})
                    MERGE (e1)-[:PRECEDES]->(e2)
                    """,
                    {"id1": event_ids_ordered[i], "id2": event_ids_ordered[i + 1]},
                )

        driver.close()
        log.info("timeline.done", rabbit_hole_id=rabbit_hole_id, events=events_created)
        return {"rabbit_hole_id": rabbit_hole_id, "events_created": events_created}

    except Exception as exc:
        log.error("timeline.error", error=str(exc))
        raise self.retry(exc=exc)


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=30))
def _extract_temporal(claims: list[dict]) -> list[dict]:
    from datetime import datetime
    from rhm_llm_prompts.prompts import TEMPORAL_EXTRACTION_V1
    prompt = TEMPORAL_EXTRACTION_V1.format(
        retrieved_at=datetime.utcnow().isoformat(),
        claims_json=json.dumps(claims, indent=2),
    )

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    response = client.messages.create(
        model="claude-haiku-4-5",
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
    )
    content = response.content[0].text.strip()

    if "```json" in content:
        content = content.split("```json")[1].split("```")[0].strip()
    elif "```" in content:
        content = content.split("```")[1].split("```")[0].strip()

    try:
        data = json.loads(content)
        return data.get("temporal_claims", [])
    except json.JSONDecodeError:
        return []
