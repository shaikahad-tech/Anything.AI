"""
RAG (Retrieval-Augmented Generation) service.
Retrieves relevant claims from Qdrant, then generates a grounded answer via Claude.
"""

from __future__ import annotations

import uuid

import anthropic
import structlog
from qdrant_client import AsyncQdrantClient
from qdrant_client.models import Filter, FieldCondition, MatchValue

from app.config import settings
from rhm_shared_types.models import ChatRequest, ChatResponse, SourceResponse

log = structlog.get_logger(__name__)

COLLECTION_NAME = "claims"
TOP_K = 10


class RAGService:
    def __init__(self) -> None:
        self._qdrant = AsyncQdrantClient(url=settings.QDRANT_URL, api_key=settings.QDRANT_API_KEY or None)
        self._anthropic = anthropic.AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)

    async def answer(self, request: ChatRequest, user_id: uuid.UUID) -> ChatResponse:
        # 1. Embed the query
        embedding = await self._embed(request.message)

        # 2. Retrieve relevant claims filtered by rabbit_hole_id
        results = await self._qdrant.search(
            collection_name=COLLECTION_NAME,
            query_vector=embedding,
            query_filter=Filter(
                must=[
                    FieldCondition(
                        key="rabbit_hole_id",
                        match=MatchValue(value=str(request.rabbit_hole_id)),
                    )
                ]
            ),
            limit=TOP_K,
            with_payload=True,
        )

        if not results:
            return ChatResponse(
                answer="No relevant claims found in this rabbit hole for your question.",
                sources=[],
                claim_ids=[],
                conversation_id=request.conversation_id or uuid.uuid4(),
            )

        # 3. Build context
        context_parts = []
        claim_ids = []
        source_ids_seen: set[str] = set()
        sources: list[SourceResponse] = []

        for hit in results:
            payload = hit.payload or {}
            claim_id = payload.get("claim_id", "")
            claim_text = payload.get("text", "")
            source_url = payload.get("source_url", "")
            source_id = payload.get("source_id", "")

            claim_ids.append(claim_id)
            context_parts.append(
                f"[claim:{claim_id}] (source: {source_url})\n{claim_text}"
            )
            if source_id not in source_ids_seen:
                source_ids_seen.add(source_id)
                sources.append(
                    SourceResponse(
                        id=uuid.UUID(source_id) if source_id else uuid.uuid4(),
                        url=source_url,
                        source_type=payload.get("source_type", "article"),
                        title=payload.get("source_title"),
                        author=None,
                        published_at=None,
                        credibility_score=payload.get("credibility_score"),
                        bias_score=None,
                        retrieved_at=None,  # type: ignore[arg-type]
                    )
                )

        context = "\n\n".join(context_parts)

        # 4. Generate answer
        system_prompt = (
            "You are a research assistant grounded strictly in the provided evidence. "
            "Rules: cite claim IDs as [claim:{id}] for every assertion. "
            "If sources disagree, surface the disagreement explicitly — never flatten it. "
            "Never present a contested claim as settled truth. "
            "If the evidence does not answer the question, say so directly."
        )
        user_message = (
            f"Question: {request.message}\n\n"
            f"Evidence (from the knowledge graph):\n{context}"
        )

        response = await self._anthropic.messages.create(
            model="claude-haiku-4-5",
            max_tokens=1024,
            system=system_prompt,
            messages=[{"role": "user", "content": user_message}],
        )
        answer_text = response.content[0].text if response.content else "No answer generated."

        return ChatResponse(
            answer=answer_text,
            sources=sources,
            claim_ids=claim_ids,
            conversation_id=request.conversation_id or uuid.uuid4(),
        )

    async def _embed(self, text: str) -> list[float]:
        """Embed text using Voyage AI (preferred) or OpenAI as fallback."""
        if settings.VOYAGE_API_KEY:
            import voyageai
            vo = voyageai.AsyncClient(api_key=settings.VOYAGE_API_KEY)
            result = await vo.embed([text], model="voyage-3")
            return result.embeddings[0]
        else:
            import openai
            client = openai.AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
            resp = await client.embeddings.create(input=text, model="text-embedding-3-small")
            return resp.data[0].embedding
