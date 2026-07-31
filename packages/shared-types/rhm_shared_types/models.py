"""Pydantic models shared across API and workers."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class SourceType(str, Enum):
    ARTICLE = "article"
    REDDIT = "reddit"
    YOUTUBE = "youtube"
    PAPER = "paper"


class JobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class ExportFormat(str, Enum):
    OBSIDIAN = "obsidian"
    PDF = "pdf"
    JSON = "json"


class NodeLabel(str, Enum):
    TOPIC = "Topic"
    SOURCE = "Source"
    CLAIM = "Claim"
    PERSON = "Person"
    ORGANIZATION = "Organization"
    EVENT = "Event"


# ---------------------------------------------------------------------------
# Rabbit Hole models
# ---------------------------------------------------------------------------


class RabbitHoleCreate(BaseModel):
    topic: str = Field(..., min_length=1, max_length=500)
    depth: int = Field(default=2, ge=1, le=5)
    source_types: list[SourceType] = Field(default_factory=lambda: [SourceType.ARTICLE])
    seed_url: str | None = None


class RabbitHoleResponse(BaseModel):
    id: uuid.UUID
    topic: str
    depth: int
    source_types: list[SourceType]
    status: JobStatus
    created_at: datetime
    updated_at: datetime
    node_count: int = 0
    edge_count: int = 0
    source_count: int = 0


# ---------------------------------------------------------------------------
# Source models
# ---------------------------------------------------------------------------


class SourceResponse(BaseModel):
    id: uuid.UUID
    url: str
    source_type: str
    title: str | None = None
    author: str | None = None
    published_at: datetime | None = None
    credibility_score: float | None = None
    bias_score: float | None = None
    retrieved_at: datetime | None = None


# ---------------------------------------------------------------------------
# Graph models
# ---------------------------------------------------------------------------


class GraphNodeData(BaseModel):
    label: str
    node_type: NodeLabel = NodeLabel.TOPIC
    properties: dict[str, Any] = Field(default_factory=dict)
    confidence: float | None = None
    credibility_score: float | None = None


class GraphNode(BaseModel):
    id: str
    type: str
    data: GraphNodeData


class GraphEdge(BaseModel):
    id: str
    source: str
    target: str
    type: str
    data: dict[str, Any] = Field(default_factory=dict)
    label: str | None = None
    animated: bool = False


class GraphResponse(BaseModel):
    nodes: list[GraphNode]
    edges: list[GraphEdge]
    node_count: int
    edge_count: int


# ---------------------------------------------------------------------------
# Timeline models
# ---------------------------------------------------------------------------


class TimelineEvent(BaseModel):
    id: str
    title: str
    date_start: str | None = None
    date_end: str | None = None
    description: str | None = None
    claim_ids: list[str] = Field(default_factory=list)
    source_ids: list[str] = Field(default_factory=list)


class TimelineResponse(BaseModel):
    events: list[TimelineEvent]


# ---------------------------------------------------------------------------
# Contradiction models
# ---------------------------------------------------------------------------


class ContradictionPair(BaseModel):
    claim_a_id: str
    claim_b_id: str
    claim_a_text: str
    claim_b_text: str
    confidence: float
    llm_rationale: str
    source_a: SourceResponse
    source_b: SourceResponse


class ContradictionsResponse(BaseModel):
    pairs: list[ContradictionPair]
    total: int


# ---------------------------------------------------------------------------
# Chat / RAG models
# ---------------------------------------------------------------------------


class ChatRequest(BaseModel):
    rabbit_hole_id: uuid.UUID
    message: str = Field(..., min_length=1)
    conversation_id: uuid.UUID | None = None


class ChatResponse(BaseModel):
    answer: str
    sources: list[SourceResponse]
    claim_ids: list[str]
    conversation_id: uuid.UUID


# ---------------------------------------------------------------------------
# Misc request models
# ---------------------------------------------------------------------------


class ExpandNodeRequest(BaseModel):
    node_id: str
    depth: int = Field(default=1, ge=1, le=3)


class ExportRequest(BaseModel):
    format: ExportFormat


class ExportResponse(BaseModel):
    download_url: str
    expires_at: datetime
