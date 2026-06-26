"""Sources / credibility endpoints."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import CurrentUser
from app.db.models import Source
from app.db.postgres import get_db
from rhm_shared_types.models import SourceResponse

router = APIRouter(tags=["sources"])


@router.get("/sources/{source_id}/credibility")
async def get_source_credibility(
    source_id: uuid.UUID,
    user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> dict:
    result = await db.execute(select(Source).where(Source.id == source_id))
    source = result.scalar_one_or_none()
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")

    return {
        "source_id": str(source.id),
        "url": source.url,
        "credibility_score": source.credibility_score,
        "bias_score": source.bias_score,
        "source_type": source.source_type,
        "retrieved_at": source.retrieved_at.isoformat() if source.retrieved_at else None,
        "breakdown": {
            "domain_reputation": None,  # populated by credibility service
            "corroboration_count": None,
            "recency": None,
            "author_history": None,
        },
    }
