"""Export endpoints — Obsidian vault, PDF, JSON."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import CurrentUser
from app.db.postgres import get_db
from app.routers.rabbitholes import _get_owned_rh
from app.services.export import ExportService
from rhm_shared_types.models import ExportRequest, ExportResponse

router = APIRouter(tags=["export"])


@router.post("/export/{rabbit_hole_id}", response_model=ExportResponse)
async def export_rabbit_hole(
    rabbit_hole_id: uuid.UUID,
    body: ExportRequest,
    user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> ExportResponse:
    rh = await _get_owned_rh(rabbit_hole_id, user.id, db)
    svc = ExportService()
    return await svc.export(rh, body.format)
