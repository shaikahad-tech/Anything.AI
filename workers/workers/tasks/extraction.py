"""
Extraction Task — NER + LLM claim extraction.

Pipeline:
1. Chunk text into ~500 token windows (50 token overlap)
2. spaCy NER pass for cheap entity candidates
3. LLM (Claude Haiku) extracts atomic, paraphrased claims per chunk
4. Each claim validated: must have source_span_text — dropped if missing (hard invariant)
5. Claims embedded via Voyage AI / OpenAI
6. Claims written to Neo4j + Qdrant
7. Contradiction detection triggered after extraction completes
"""

from __future__ import annotations

import json
import os
import uuid
from typing import Any

import anthropic
import spacy
import structlog
from neo4j import GraphDatabase
from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    PointStruct,
    VectorParams,
)
from tenacity import retry, stop_after_attempt, wait_exponential

from workers.celery_app import app
from workers.utils.pubsub import publish_ws

log = structlog.get_logger(__name__)

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
NEO4J_URI = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.environ.get("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.environ.get("NEO4J_PASSWORD", "rabbitholepass")
QDRANT_URL = os.environ.get("QDRANT_URL", "http://localhost:6333")
VOYAGE_API_KEY = os.environ.get("VOYAGE_API_KEY", "")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")

COLLECTION_NAME = "claims"
EMBEDDING_DIM = 1024  # Voyage-3 dimension; change to 1536 if using OpenAI text-embedding-3-small

_nlp = None


def _get_nlp() -> spacy.language.Language:
    global _nlp
    if _nlp is None:
        _nlp = spacy.load("en_core_web_sm")
    return _nlp


def _get_neo4j() -> GraphDatabase:
    return GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))


def _get_qdrant() -> QdrantClient:
    client = QdrantClient(url=QDRANT_URL)
    # Ensure collection exists
    try:
        client.get_collection(COLLECTION_NAME)
    except Exception:
        client.create_collection(
            COLLECTION_NAME,
            vectors_config=VectorParams(size=EMBEDDING_DIM, distance=Distance.COSINE),
        )
    return client


@app.task(
    bind=True,
    name="workers.tasks.extraction.extraction_task",
    max_retries=3,
    default_retry_delay=60,
)
def extraction_task(
    self,
    rabbit_hole_id: str,
    source_id: str,
    url: str,
    text: str,
    source_type: str,
    published_at: str | None = None,
) -> dict:
    try:
        log.info("extraction.start", rabbit_hole_id=rabbit_hole_id, source_id=source_id)

        chunks = _chunk_text(text, chunk_size=500, overlap=50)
        all_claims = []

        for chunk_idx, chunk in enumerate(chunks):
            # 1. Fast NER pass
            entities = _ner_pass(chunk)

            # 2. LLM extraction
            raw_claims = _llm_extract_claims(chunk, url, source_type, published_at)
            if not raw_claims:
                continue

            for claim_data in raw_claims:
                # Hard invariant: claims without source_span_text are dropped
                if not claim_data.get("source_span_text"):
                    log.warning(
                        "extraction.claim_dropped.no_span",
                        source_id=source_id,
                        claim_text=claim_data.get("text", "")[:80],
                    )
                    continue

                claim_id = str(uuid.uuid4())
                claim_data["id"] = claim_id
                claim_data["source_id"] = source_id
                claim_data["rabbit_hole_id"] = rabbit_hole_id
                all_claims.append(claim_data)

        if not all_claims:
            log.info("extraction.no_claims", source_id=source_id)
            return {"rabbit_hole_id": rabbit_hole_id, "claims": 0}

        # 3. Embed all claims in batch
        texts_to_embed = [c["text"] for c in all_claims]
        embeddings = _embed_batch(texts_to_embed)

        # 4. Write to Neo4j + Qdrant
        driver = _get_neo4j()
        qdrant = _get_qdrant()
        points = []

        with driver.session() as session:
            for claim_data, embedding in zip(all_claims, embeddings):
                # Write Claim node
                session.run(
                    """
                    MERGE (c:Claim {id: $id})
                    SET c += {
                      text: $text,
                      confidence: $confidence,
                      sentiment: $sentiment,
                      stance: $stance,
                      source_span_text: $source_span_text,
                      source_span_start: $source_span_start,
                      source_span_end: $source_span_end,
                      rabbit_hole_id: $rabbit_hole_id
                    }
                    """,
                    {
                        "id": claim_data["id"],
                        "text": claim_data["text"],
                        "confidence": claim_data.get("confidence", 0.5),
                        "sentiment": claim_data.get("sentiment"),
                        "stance": claim_data.get("stance"),
                        "source_span_text": claim_data["source_span_text"],
                        "source_span_start": claim_data.get("source_span_start"),
                        "source_span_end": claim_data.get("source_span_end"),
                        "rabbit_hole_id": rabbit_hole_id,
                    },
                )

                # Wire the CONTAINS_CLAIM edge — fulfills the hard provenance invariant
                session.run(
                    """
                    MATCH (s:Source {id: $source_id})
                    MATCH (c:Claim {id: $claim_id})
                    MERGE (s)-[:CONTAINS_CLAIM]->(c)
                    """,
                    {"source_id": source_id, "claim_id": claim_data["id"]},
                )

                # Write entity nodes and MENTIONS edges
                for entity in claim_data.get("entities", []):
                    entity_label = _entity_label(entity.get("type", "concept"))
                    entity_id = f"{entity_label}:{entity['name'].lower().replace(' ', '_')}"
                    session.run(
                        f"MERGE (e:{entity_label} {{id: $id}}) SET e.name = $name, e.rabbit_hole_id = $rabbit_hole_id",
                        {"id": entity_id, "name": entity["name"], "rabbit_hole_id": rabbit_hole_id},
                    )
                    session.run(
                        f"""
                        MATCH (c:Claim {{id: $claim_id}})
                        MATCH (e:{entity_label} {{id: $entity_id}})
                        MERGE (c)-[:MENTIONS]->(e)
                        """,
                        {"claim_id": claim_data["id"], "entity_id": entity_id},
                    )

                # Qdrant point
                points.append(
                    PointStruct(
                        id=str(uuid.uuid5(uuid.NAMESPACE_DNS, claim_data["id"])),
                        vector=embedding,
                        payload={
                            "claim_id": claim_data["id"],
                            "text": claim_data["text"],
                            "source_id": source_id,
                            "source_url": url,
                            "source_type": source_type,
                            "rabbit_hole_id": rabbit_hole_id,
                            "confidence": claim_data.get("confidence", 0.5),
                        },
                    )
                )

                publish_ws(rabbit_hole_id, {
                    "type": "node_added",
                    "rabbit_hole_id": rabbit_hole_id,
                    "payload": {
                        "id": claim_data["id"],
                        "type": "claimNode",
                        "label": claim_data["text"][:80],
                        "confidence": claim_data.get("confidence"),
                    },
                    "timestamp": "",
                })

        driver.close()

        if points:
            qdrant.upsert(collection_name=COLLECTION_NAME, points=points)

        # Trigger contradiction detection
        from workers.tasks.contradiction import contradiction_task
        contradiction_task.apply_async(
            kwargs={"rabbit_hole_id": rabbit_hole_id},
            queue="contradiction",
            countdown=5,
        )

        log.info("extraction.done", rabbit_hole_id=rabbit_hole_id, claims=len(all_claims))
        return {"rabbit_hole_id": rabbit_hole_id, "claims": len(all_claims)}

    except Exception as exc:
        log.error("extraction.error", error=str(exc), source_id=source_id)
        raise self.retry(exc=exc)


# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------


def _chunk_text(text: str, chunk_size: int = 500, overlap: int = 50) -> list[str]:
    """Chunk by approximate token count (1 token ≈ 4 chars)."""
    char_size = chunk_size * 4
    char_overlap = overlap * 4
    chunks = []
    start = 0
    while start < len(text):
        end = start + char_size
        chunks.append(text[start:end])
        start += char_size - char_overlap
    return chunks


# ---------------------------------------------------------------------------
# NER (fast spaCy pass)
# ---------------------------------------------------------------------------


def _ner_pass(text: str) -> list[dict]:
    nlp = _get_nlp()
    doc = nlp(text[:10000])  # cap to avoid spaCy OOM on huge chunks
    return [
        {"name": ent.text, "type": ent.label_.lower()}
        for ent in doc.ents
        if ent.label_ in ("PERSON", "ORG", "GPE", "EVENT", "WORK_OF_ART", "PRODUCT")
    ]


# ---------------------------------------------------------------------------
# LLM extraction
# ---------------------------------------------------------------------------


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=30))
def _llm_extract_claims(
    chunk: str,
    source_url: str,
    source_type: str,
    published_at: str | None,
) -> list[dict[str, Any]]:
    from rhm_llm_prompts.prompts import CLAIM_EXTRACTION_V1
    prompt = CLAIM_EXTRACTION_V1.format(
        source_url=source_url,
        source_type=source_type,
        published_at=published_at or "unknown",
        chunk_text=chunk,
    )

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    response = client.messages.create(
        model="claude-haiku-4-5",
        max_tokens=2048,
        messages=[{"role": "user", "content": prompt}],
    )
    content = response.content[0].text.strip()

    # Parse JSON
    if "```json" in content:
        content = content.split("```json")[1].split("```")[0].strip()
    elif "```" in content:
        content = content.split("```")[1].split("```")[0].strip()

    try:
        data = json.loads(content)
        return data.get("claims", [])
    except json.JSONDecodeError:
        log.warning("extraction.json_parse_error", content_preview=content[:200])
        return []


# ---------------------------------------------------------------------------
# Embedding
# ---------------------------------------------------------------------------


def _embed_batch(texts: list[str]) -> list[list[float]]:
    if VOYAGE_API_KEY:
        import voyageai
        vo = voyageai.Client(api_key=VOYAGE_API_KEY)
        result = vo.embed(texts, model="voyage-3")
        return result.embeddings
    else:
        import openai
        client = openai.OpenAI(api_key=OPENAI_API_KEY)
        resp = client.embeddings.create(input=texts, model="text-embedding-3-small")
        return [item.embedding for item in resp.data]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _entity_label(entity_type: str) -> str:
    return {
        "person": "Person",
        "per": "Person",
        "org": "Organization",
        "organization": "Organization",
        "event": "Event",
        "gpe": "Organization",
    }.get(entity_type.lower(), "Organization")
