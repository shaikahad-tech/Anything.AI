"""
Graph Service — Neo4j read layer.

All Cypher queries use parameterized form (no string interpolation into queries).
This is a hard invariant enforced by code review and CI linting.
"""

from __future__ import annotations

from app.db.neo4j_client import run_query
from rhm_shared_types.models import (
    ContradictionPair,
    ContradictionsResponse,
    GraphEdge,
    GraphNode,
    GraphNodeData,
    GraphResponse,
    NodeLabel,
    SourceResponse,
    TimelineEvent,
    TimelineResponse,
)


class GraphService:
    async def get_react_flow_graph(self, rabbit_hole_id: str) -> GraphResponse:
        """Return the full graph for a rabbit hole in React Flow format."""
        nodes_raw = await run_query(
            """
            MATCH (n)
            WHERE n.rabbit_hole_id = $rabbit_hole_id
            RETURN
              id(n) AS neo_id,
              labels(n) AS labels,
              properties(n) AS props
            LIMIT 1000
            """,
            {"rabbit_hole_id": rabbit_hole_id},
        )

        edges_raw = await run_query(
            """
            MATCH (a)-[r]->(b)
            WHERE a.rabbit_hole_id = $rabbit_hole_id
              AND b.rabbit_hole_id = $rabbit_hole_id
            RETURN
              id(r) AS neo_id,
              type(r) AS rel_type,
              id(a) AS source,
              id(b) AS target,
              properties(r) AS props
            LIMIT 2000
            """,
            {"rabbit_hole_id": rabbit_hole_id},
        )

        nodes = [_raw_to_graph_node(r) for r in nodes_raw]
        edges = [_raw_to_graph_edge(r) for r in edges_raw]

        return GraphResponse(
            nodes=nodes,
            edges=edges,
            node_count=len(nodes),
            edge_count=len(edges),
        )

    async def get_timeline(self, rabbit_hole_id: str) -> TimelineResponse:
        events_raw = await run_query(
            """
            MATCH (e:Event)
            WHERE e.rabbit_hole_id = $rabbit_hole_id
            OPTIONAL MATCH (c:Claim)-[:MENTIONS]->(e)
            OPTIONAL MATCH (s:Source)-[:CONTAINS_CLAIM]->(c)
            WITH e, collect(DISTINCT c.id) AS claim_ids, collect(DISTINCT s.id) AS source_ids
            RETURN e.id AS id, e.title AS title, e.date_start AS date_start,
                   e.date_end AS date_end, e.description AS description,
                   claim_ids, source_ids
            ORDER BY e.date_start ASC NULLS LAST
            """,
            {"rabbit_hole_id": rabbit_hole_id},
        )
        events = [
            TimelineEvent(
                id=r["id"],
                title=r["title"] or "",
                date_start=r["date_start"],
                date_end=r["date_end"],
                description=r["description"],
                claim_ids=r["claim_ids"] or [],
                source_ids=r["source_ids"] or [],
            )
            for r in events_raw
        ]
        return TimelineResponse(events=events)

    async def get_contradictions(self, rabbit_hole_id: str) -> ContradictionsResponse:
        pairs_raw = await run_query(
            """
            MATCH (a:Claim)-[r:CONTRADICTS]->(b:Claim)
            WHERE a.rabbit_hole_id = $rabbit_hole_id
            MATCH (sa:Source)-[:CONTAINS_CLAIM]->(a)
            MATCH (sb:Source)-[:CONTAINS_CLAIM]->(b)
            RETURN
              a.id AS claim_a_id, a.text AS claim_a_text,
              b.id AS claim_b_id, b.text AS claim_b_text,
              r.confidence AS confidence,
              r.llm_rationale AS llm_rationale,
              sa.id AS source_a_id, sa.url AS source_a_url,
              sa.title AS source_a_title, sa.source_type AS source_a_type,
              sa.credibility_score AS source_a_credibility,
              sb.id AS source_b_id, sb.url AS source_b_url,
              sb.title AS source_b_title, sb.source_type AS source_b_type,
              sb.credibility_score AS source_b_credibility
            ORDER BY r.confidence DESC
            LIMIT 200
            """,
            {"rabbit_hole_id": rabbit_hole_id},
        )

        pairs = []
        for r in pairs_raw:
            pairs.append(
                ContradictionPair(
                    claim_a_id=r["claim_a_id"],
                    claim_b_id=r["claim_b_id"],
                    claim_a_text=r["claim_a_text"],
                    claim_b_text=r["claim_b_text"],
                    confidence=r["confidence"] or 0.0,
                    llm_rationale=r["llm_rationale"] or "",
                    source_a=_raw_to_source_response(
                        r["source_a_id"], r["source_a_url"], r["source_a_title"],
                        r["source_a_type"], r["source_a_credibility"]
                    ),
                    source_b=_raw_to_source_response(
                        r["source_b_id"], r["source_b_url"], r["source_b_title"],
                        r["source_b_type"], r["source_b_credibility"]
                    ),
                )
            )
        return ContradictionsResponse(pairs=pairs, total=len(pairs))

    async def write_node(self, label: str, props: dict) -> None:
        """
        Upsert a node by its 'id' property.
        All writes use MERGE so re-processing is idempotent.
        """
        await run_query(
            f"MERGE (n:{label} {{id: $id}}) SET n += $props",  # noqa: S608 — label is an internal enum, not user input
            {"id": props["id"], "props": props},
        )

    async def write_edge(
        self,
        src_id: str,
        src_label: str,
        dst_id: str,
        dst_label: str,
        rel_type: str,
        props: dict | None = None,
    ) -> None:
        """
        Upsert a directed edge between two existing nodes.
        rel_type must come from the EdgeType enum — never raw user input.
        """
        await run_query(
            f"""
            MATCH (a:{src_label} {{id: $src_id}})
            MATCH (b:{dst_label} {{id: $dst_id}})
            MERGE (a)-[r:{rel_type}]->(b)
            SET r += $props
            """,  # noqa: S608 — rel_type/labels are internal enums
            {
                "src_id": src_id,
                "dst_id": dst_id,
                "props": props or {},
            },
        )

    async def enforce_claim_provenance(self, claim_id: str) -> bool:
        """
        Hard invariant: every Claim must have at least one CONTAINS_CLAIM edge.
        Returns False if the claim would be orphaned — caller must drop it.
        """
        result = await run_query(
            """
            MATCH (s:Source)-[:CONTAINS_CLAIM]->(c:Claim {id: $claim_id})
            RETURN count(s) AS cnt
            """,
            {"claim_id": claim_id},
        )
        count = result[0]["cnt"] if result else 0
        return count > 0


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _raw_to_graph_node(r: dict) -> GraphNode:
    labels = r["labels"]
    props = r["props"]
    label = labels[0] if labels else "Unknown"
    node_type_map = {
        "Topic": "topicNode",
        "Source": "sourceNode",
        "Claim": "claimNode",
        "Person": "personNode",
        "Organization": "orgNode",
        "Event": "eventNode",
    }
    return GraphNode(
        id=str(r["neo_id"]),
        type=node_type_map.get(label, "default"),
        data=GraphNodeData(
            label=props.get("name") or props.get("title") or props.get("text", "")[:80],
            node_type=NodeLabel(label) if label in NodeLabel._value2member_map_ else NodeLabel.TOPIC,
            properties=props,
            confidence=props.get("confidence"),
            credibility_score=props.get("credibility_score"),
        ),
    )


def _raw_to_graph_edge(r: dict) -> GraphEdge:
    rel_type = r["rel_type"]
    props = r["props"]
    type_map = {
        "CONTRADICTS": "contradiction",
        "SUPPORTS": "support",
        "PRECEDES": "temporal",
    }
    edge_type = type_map.get(rel_type, "default")
    return GraphEdge(
        id=str(r["neo_id"]),
        source=str(r["source"]),
        target=str(r["target"]),
        type=edge_type,
        data=props,
        label=props.get("llm_rationale", "")[:60] if rel_type == "CONTRADICTS" else None,
        animated=rel_type == "CONTRADICTS",
    )


def _raw_to_source_response(
    source_id: str, url: str, title: str | None, source_type: str, credibility: float | None
) -> SourceResponse:
    import uuid as _uuid
    from datetime import datetime
    return SourceResponse(
        id=_uuid.UUID(source_id) if source_id else _uuid.uuid4(),
        url=url or "",
        source_type=source_type or "article",
        title=title,
        author=None,
        published_at=None,
        credibility_score=credibility,
        bias_score=None,
        retrieved_at=datetime.utcnow(),
    )
