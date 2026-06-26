"""
Narrator Service — documentary-style AI walkthrough + Devil's Advocate generation.

These are clearly labeled as AI-generated synthesis, never presented with the
same visual authority as sourced claims.
"""

from __future__ import annotations

import json

import anthropic
import structlog

from app.config import settings
from app.services.graph import GraphService

log = structlog.get_logger(__name__)


class NarratorService:
    def __init__(self) -> None:
        self._client = anthropic.AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
        self._graph = GraphService()

    async def generate_narrative(self, rabbit_hole_id: str, topic: str) -> str:
        """
        Documentary-style walkthrough of the knowledge graph.
        Returns markdown string labeled as AI-generated.
        """
        graph = await self._graph.get_react_flow_graph(rabbit_hole_id)
        contradictions = await self._graph.get_contradictions(rabbit_hole_id)

        graph_summary = {
            "nodes": len(graph.nodes),
            "edges": len(graph.edges),
            "node_types": {},
            "sample_claims": [],
            "contradictions": [
                {
                    "claim_a": c.claim_a_text[:100],
                    "claim_b": c.claim_b_text[:100],
                    "confidence": c.confidence,
                    "rationale": c.llm_rationale[:200],
                }
                for c in contradictions.pairs[:5]
            ],
        }

        # Count node types
        for node in graph.nodes:
            t = node.data.node_type.value
            graph_summary["node_types"][t] = graph_summary["node_types"].get(t, 0) + 1

        # Sample claims
        claim_nodes = [n for n in graph.nodes if n.data.node_type.value == "Claim"][:10]
        graph_summary["sample_claims"] = [n.data.label for n in claim_nodes]

        from rhm_llm_prompts.prompts import NARRATOR_V1
        prompt = NARRATOR_V1.format(
            topic=topic,
            graph_summary_json=json.dumps(graph_summary, indent=2),
        )

        response = await self._client.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=2048,
            system=(
                "You are a documentary narrator. Generate grounded narrative synthesis "
                "with explicit uncertainty. Never present contested claims as settled."
            ),
            messages=[{"role": "user", "content": prompt}],
        )
        return response.content[0].text

    async def generate_devils_advocate(self, rabbit_hole_id: str, topic: str) -> str:
        """
        Devil's Advocate brief — steelmans the minority/contrarian reading.
        Clearly labeled, never merged into the main narrative.
        """
        contradictions = await self._graph.get_contradictions(rabbit_hole_id)

        if not contradictions.pairs:
            return "No contradictions found — a Devil's Advocate brief requires at least one contested claim."

        # Identify minority-view claims (lower-credibility source side)
        minority_claims = []
        mainstream_parts = []
        for pair in contradictions.pairs[:10]:
            cred_a = pair.source_a.credibility_score or 0.5
            cred_b = pair.source_b.credibility_score or 0.5
            if cred_a >= cred_b:
                mainstream_parts.append(pair.claim_a_text)
                minority_claims.append({"id": pair.claim_b_id, "text": pair.claim_b_text, "source": pair.source_b.url})
            else:
                mainstream_parts.append(pair.claim_b_text)
                minority_claims.append({"id": pair.claim_a_id, "text": pair.claim_a_text, "source": pair.source_a.url})

        mainstream_summary = " ".join(mainstream_parts[:3])

        from rhm_llm_prompts.prompts import DEVIL_ADVOCATE_V1
        prompt = DEVIL_ADVOCATE_V1.format(
            topic=topic,
            mainstream_summary=mainstream_summary,
            minority_claims_json=json.dumps(minority_claims, indent=2),
        )

        response = await self._client.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=1536,
            system=(
                "You are a Devil's Advocate analyst. Present the strongest version of the "
                "contrarian case using only evidence from the provided claims. "
                "Cite claim IDs. Never claim the contrarian view is correct."
            ),
            messages=[{"role": "user", "content": prompt}],
        )
        return response.content[0].text
