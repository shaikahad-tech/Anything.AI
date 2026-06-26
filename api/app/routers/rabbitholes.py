"""
Rabbit Holes router — CRUD + graph + timeline + contradictions + expand.
"""

from __future__ import annotations

import uuid

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import CurrentUser
from app.db.models import Job, RabbitHole
from app.db.postgres import get_db
from app.services.graph import GraphService
from app.services.ingestion import IngestionService
from rhm_shared_types.models import (
    ContradictionsResponse,
    ExpandNodeRequest,
    GraphResponse,
    RabbitHoleCreate,
    RabbitHoleResponse,
    TimelineResponse,
)

log = structlog.get_logger(__name__)
router = APIRouter(tags=["rabbit-holes"])


@router.post(
    "/rabbitholes",
    response_model=RabbitHoleResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_rabbit_hole(
    body: RabbitHoleCreate,
    user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> RabbitHoleResponse:
    """Create a new rabbit hole and kick off ingestion."""
    rh = RabbitHole(
        user_id=user.id,
        topic=body.topic,
        depth=body.depth,
        source_types=[st.value for st in body.source_types],
        status="pending",
    )
    db.add(rh)
    await db.flush()

    # Kick off the ingestion orchestrator asynchronously
    ingestion = IngestionService(db)
    await ingestion.start(rabbit_hole_id=rh.id, topic=body.topic, depth=body.depth, source_types=body.source_types)

    await db.commit()
    await db.refresh(rh)
    log.info("rabbit_hole.created", id=str(rh.id), topic=body.topic)
    return _to_response(rh)


@router.get("/rabbitholes/{rabbit_hole_id}", response_model=RabbitHoleResponse)
async def get_rabbit_hole(
    rabbit_hole_id: uuid.UUID,
    user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> RabbitHoleResponse:
    rh = await _get_owned_rh(rabbit_hole_id, user.id, db)
    return _to_response(rh)


@router.get("/rabbitholes", response_model=list[RabbitHoleResponse])
async def list_rabbit_holes(
    user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> list[RabbitHoleResponse]:
    result = await db.execute(
        select(RabbitHole).where(RabbitHole.user_id == user.id).order_by(RabbitHole.created_at.desc())
    )
    return [_to_response(rh) for rh in result.scalars().all()]


@router.get("/rabbitholes/{rabbit_hole_id}/graph", response_model=GraphResponse)
async def get_graph(
    rabbit_hole_id: uuid.UUID,
    user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> GraphResponse:
    await _get_owned_rh(rabbit_hole_id, user.id, db)
    svc = GraphService()
    return await svc.get_react_flow_graph(str(rabbit_hole_id))


@router.get("/rabbitholes/{rabbit_hole_id}/timeline", response_model=TimelineResponse)
async def get_timeline(
    rabbit_hole_id: uuid.UUID,
    user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> TimelineResponse:
    await _get_owned_rh(rabbit_hole_id, user.id, db)
    svc = GraphService()
    return await svc.get_timeline(str(rabbit_hole_id))


@router.get(
    "/rabbitholes/{rabbit_hole_id}/contradictions",
    response_model=ContradictionsResponse,
)
async def get_contradictions(
    rabbit_hole_id: uuid.UUID,
    user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> ContradictionsResponse:
    await _get_owned_rh(rabbit_hole_id, user.id, db)
    svc = GraphService()
    return await svc.get_contradictions(str(rabbit_hole_id))


@router.post(
    "/rabbitholes/{rabbit_hole_id}/expand",
    status_code=status.HTTP_202_ACCEPTED,
)
async def expand_node(
    rabbit_hole_id: uuid.UUID,
    body: ExpandNodeRequest,
    user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    """Trigger a deeper crawl on a specific entity node."""
    rh = await _get_owned_rh(rabbit_hole_id, user.id, db)
    ingestion = IngestionService(db)
    await ingestion.expand_node(rh, body.node_id, body.depth)
    return {"status": "accepted"}


@router.delete(
    "/rabbitholes/{rabbit_hole_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_rabbit_hole(
    rabbit_hole_id: uuid.UUID,
    user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> None:
    rh = await _get_owned_rh(rabbit_hole_id, user.id, db)
    await db.delete(rh)
    await db.commit()
    log.info("rabbit_hole.deleted", id=str(rabbit_hole_id))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _get_owned_rh(
    rabbit_hole_id: uuid.UUID, user_id: uuid.UUID, db: AsyncSession
) -> RabbitHole:
    result = await db.execute(
        select(RabbitHole).where(
            RabbitHole.id == rabbit_hole_id, RabbitHole.user_id == user_id
        )
    )
    rh = result.scalar_one_or_none()
    if not rh:
        raise HTTPException(status_code=404, detail="Rabbit hole not found")
    return rh


def _to_response(rh: RabbitHole) -> RabbitHoleResponse:
    from rhm_shared_types.models import JobStatus, SourceType

    return RabbitHoleResponse(
        id=rh.id,
        topic=rh.topic,
        depth=rh.depth,
        source_types=[SourceType(st) for st in rh.source_types],
        status=JobStatus(rh.status),
        created_at=rh.created_at,
        updated_at=rh.updated_at,
        node_count=rh.node_count,
        edge_count=rh.edge_count,
        source_count=rh.source_count,
    )
