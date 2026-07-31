"""
Contradiction Detection Task.

1. Cluster claims about the same entity/event using Qdrant vector similarity
2. Pairwise LLM NLI-style classification on candidate contradiction pairs
3. Write CONTRADICTS edges to Neo4j with rationale and confidence
4. Confidence = LLM confidence × weighted credibility scores
"""

from __future__ import annotations

import json
import os
import uuid

import anthropic
import structlog
from neo4j import GraphDatabase
from qdrant_client import QdrantClient
from qdrant_client.models import Filter, FieldCondition, MatchValue
from tenacity import retry, stop_after_attempt, wait_exponential

from workers.celery_app import app
from workers.utils.pubsub import publish_ws

log = structlog.get_logger(__name__)

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
NEO4J_URI = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.environ.get("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.environ.get("NEO4J_PASSWORD", "rabbitholepass")
QDRANT_URL = os.environ.get("QDRANT_URL", "http://localhost:6333")

COLLECTION_NAME = "claims"
SIMILARITY_THRESHOLD = 0.82  # cosine similarity floor for candidate pairs
MAX_CANDIDATES_PER_RUN = 50


@app.task(
    bind=True,
    name="workers.tasks.contradiction.contradiction_task",
    max_retries=3,
    default_retry_delay=30,
)
def contradiction_task(self, rabbit_hole_id: str) -> dict:
    try:
        log.info("contradiction.start", rabbit_hole_id=rabbit_hole_id)
        driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
        qdrant = QdrantClient(url=QDRANT_URL)

        # Get all claims for this rabbit hole from Neo4j
        with driver.session() as session:
            result = session.run(
                """
                MATCH (c:Claim {rabbit_hole_id: $rabbit_hole_id})
                MATCH (s:Source)-[:CONTAINS_CLAIM]->(c)
                RETURN c.id AS claim_id, c.text AS claim_text,
                       s.id AS source_id, s.url AS source_url,
                       s.credibility_score AS credibility_score
                LIMIT 500
                """,
                {"rabbit_hole_id": rabbit_hole_id},
            )
            claims = [dict(r) for r in result]

        if len(claims) < 2:
            return {"rabbit_hole_id": rabbit_hole_id, "contradictions": 0}

        # Find candidate pairs via Qdrant similarity search
        candidate_pairs = _find_candidate_pairs(qdrant, claims, rabbit_hole_id)

        if not candidate_pairs:
            return {"rabbit_hole_id": rabbit_hole_id, "contradictions": 0}

        # LLM classification of candidates
        contradictions_written = 0
        with driver.session() as session:
            for pair in candidate_pairs[:MAX_CANDIDATES_PER_RUN]:
                claim_a, claim_b = pair
                result = _classify_pair(claim_a, claim_b)

                if result["relation"] == "contradiction" and result["confidence"] > 0.5:
                    # Weight confidence by source credibility
                    cred_a = claim_a.get("credibility_score") or 0.5
                    cred_b = claim_b.get("credibility_score") or 0.5
                    weighted_confidence = (
                        result["confidence"]
                        * (0.5 + 0.5 * ((cred_a + cred_b) / 2))
                    )

                    session.run(
                        """
                        MATCH (a:Claim {id: $claim_a_id})
                        MATCH (b:Claim {id: $claim_b_id})
                        MERGE (a)-[r:CONTRADICTS]->(b)
                        SET r.confidence = $confidence,
                            r.llm_rationale = $rationale,
                            r.raw_llm_confidence = $raw_confidence
                        """,
                        {
                            "claim_a_id": claim_a["claim_id"],
                            "claim_b_id": claim_b["claim_id"],
                            "confidence": weighted_confidence,
                            "rationale": result["rationale"],
                            "raw_confidence": result["confidence"],
                        },
                    )

                    publish_ws(rabbit_hole_id, {
                        "type": "edge_added",
                        "rabbit_hole_id": rabbit_hole_id,
                        "payload": {
                            "id": str(uuid.uuid4()),
                            "source": claim_a["claim_id"],
                            "target": claim_b["claim_id"],
                            "type": "contradiction",
                            "confidence": weighted_confidence,
                            "rationale": result["rationale"][:120],
                        },
                        "timestamp": "",
                    })
                    contradictions_written += 1

                elif result["relation"] == "entailment" and result["confidence"] > 0.6:
                    session.run(
                        """
                        MATCH (a:Claim {id: $claim_a_id})
                        MATCH (b:Claim {id: $claim_b_id})
                        MERGE (a)-[r:SUPPORTS]->(b)
                        SET r.confidence = $confidence
                        """,
                        {
                            "claim_a_id": claim_a["claim_id"],
                            "claim_b_id": claim_b["claim_id"],
                            "confidence": result["confidence"],
                        },
                    )

        driver.close()
        log.info(
            "contradiction.done",
            rabbit_hole_id=rabbit_hole_id,
            contradictions=contradictions_written,
        )
        return {"rabbit_hole_id": rabbit_hole_id, "contradictions": contradictions_written}

    except Exception as exc:
        log.error("contradiction.error", error=str(exc))
        raise self.retry(exc=exc)


def _find_candidate_pairs(
    qdrant: QdrantClient,
    claims: list[dict],
    rabbit_hole_id: str,
) -> list[tuple[dict, dict]]:
    """
    Find high-similarity claim pairs from different sources.
    High similarity + different sources = candidate contradiction.
    """
    pairs = []
    seen: set[frozenset] = set()

    # For performance, sample up to 30 claims for the pair search
    sample = claims[:30]

    for claim in sample:
        # Find similar claims in the same rabbit hole
        try:
            results = qdrant.search(
                collection_name=COLLECTION_NAME,
                query_filter=Filter(
                    must=[
                        FieldCondition(
                            key="rabbit_hole_id",
                            match=MatchValue(value=rabbit_hole_id),
                        )
                    ]
                ),
                query_vector=_get_claim_vector(qdrant, claim["claim_id"]),
                limit=5,
                score_threshold=SIMILARITY_THRESHOLD,
                with_payload=True,
            )
        except Exception:
            continue

        for hit in results:
            hit_id = hit.payload.get("claim_id") if hit.payload else None
            hit_source = hit.payload.get("source_id") if hit.payload else None
            if not hit_id or hit_id == claim["claim_id"]:
                continue
            # Only consider pairs from different sources
            if hit_source == claim.get("source_id"):
                continue

            pair_key = frozenset([claim["claim_id"], hit_id])
            if pair_key in seen:
                continue
            seen.add(pair_key)

            # Build the counterpart dict
            counterpart = {
                "claim_id": hit_id,
                "claim_text": hit.payload.get("text", "") if hit.payload else "",
                "source_id": hit_source,
                "source_url": hit.payload.get("source_url", "") if hit.payload else "",
                "credibility_score": hit.payload.get("credibility_score") if hit.payload else None,
            }
            pairs.append((claim, counterpart))

    return pairs


def _get_claim_vector(qdrant: QdrantClient, claim_id: str) -> list[float]:
    """Retrieve a stored embedding vector from Qdrant by claim_id."""
    point_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, claim_id))
    results = qdrant.retrieve(
        collection_name=COLLECTION_NAME,
        ids=[point_id],
        with_vectors=True,
    )
    if results and results[0].vector:
        vec = results[0].vector
        if isinstance(vec, list):
            return vec
    raise ValueError(f"No vector found for claim {claim_id}")


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=30))
def _classify_pair(claim_a: dict, claim_b: dict) -> dict:
    """LLM NLI classification of a candidate pair. Uses Claude Sonnet for accuracy."""
    from rhm_llm_prompts.prompts import CONTRADICTION_ADJUDICATION_V1
    prompt = CONTRADICTION_ADJUDICATION_V1.format(
        claim_a_id=claim_a["claim_id"],
        claim_a_text=claim_a.get("claim_text", ""),
        source_a_url=claim_a.get("source_url", ""),
        source_a_credibility=claim_a.get("credibility_score") or 0.5,
        claim_b_id=claim_b["claim_id"],
        claim_b_text=claim_b.get("claim_text", ""),
        source_b_url=claim_b.get("source_url", ""),
        source_b_credibility=claim_b.get("credibility_score") or 0.5,
    )

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    response = client.messages.create(
        model="claude-haiku-4-5",
        max_tokens=512,
        messages=[{"role": "user", "content": prompt}],
    )
    content = response.content[0].text.strip()

    if "```json" in content:
        content = content.split("```json")[1].split("```")[0].strip()
    elif "```" in content:
        content = content.split("```")[1].split("```")[0].strip()

    try:
        return json.loads(content)
    except json.JSONDecodeError:
        return {"relation": "neutral", "confidence": 0.0, "rationale": "Parse error", "minority_view": ""}
