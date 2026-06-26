"""Narrator + Devil's Advocate endpoints."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import CurrentUser
from app.db.postgres import get_db
from app.routers.rabbitholes import _get_owned_rh
from app.services.narrator import NarratorService

router = APIRouter(tags=["narrator"])


class NarrativeResponse(BaseModel):
    text: str
    type: str  # "narrative" | "devils_advocate"


@router.post("/rabbitholes/{rabbit_hole_id}/narrative", response_model=NarrativeResponse)
async def generate_narrative(
    rabbit_hole_id: uuid.UUID,
    user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> NarrativeResponse:
    """Generate a documentary-style AI narrative walkthrough of this rabbit hole."""
    rh = await _get_owned_rh(rabbit_hole_id, user.id, db)
    svc = NarratorService()
    text = await svc.generate_narrative(str(rabbit_hole_id), rh.topic)
    return NarrativeResponse(text=text, type="narrative")


@router.post("/rabbitholes/{rabbit_hole_id}/devils-advocate", response_model=NarrativeResponse)
async def generate_devils_advocate(
    rabbit_hole_id: uuid.UUID,
    user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> NarrativeResponse:
    """Generate a Devil's Advocate brief arguing the minority/contrarian reading."""
    rh = await _get_owned_rh(rabbit_hole_id, user.id, db)
    svc = NarratorService()
    text = await svc.generate_devils_advocate(str(rabbit_hole_id), rh.topic)
    return NarrativeResponse(text=text, type="devils_advocate")
