"""Chat / RAG endpoint."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import CurrentUser
from app.db.postgres import get_db
from app.services.rag import RAGService
from rhm_shared_types.models import ChatRequest, ChatResponse

router = APIRouter(tags=["chat"])


@router.post("/chat", response_model=ChatResponse)
async def chat(
    body: ChatRequest,
    user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> ChatResponse:
    """RAG-grounded chat over a rabbit hole's knowledge graph."""
    svc = RAGService()
    return await svc.answer(body, user_id=user.id)
