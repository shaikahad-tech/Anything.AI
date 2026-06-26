"""
Rabbit Hole Mapper — API Gateway
FastAPI application entry point.
"""

from __future__ import annotations

import sentry_sdk
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sentry_sdk.integrations.fastapi import FastApiIntegration
from sentry_sdk.integrations.starlette import StarletteIntegration

from app.config import settings
from app.routers import chat, export, narrator, rabbitholes, sources, ws
from app.telemetry import setup_telemetry


def create_app() -> FastAPI:
    if settings.SENTRY_DSN:
        sentry_sdk.init(
            dsn=settings.SENTRY_DSN,
            environment=settings.ENVIRONMENT,
            integrations=[StarletteIntegration(), FastApiIntegration()],
            traces_sample_rate=0.1,
        )

    app = FastAPI(
        title="Rabbit Hole Mapper API",
        version="0.1.0",
        description="Research intelligence platform — knowledge graph + contradiction engine",
        docs_url="/docs" if settings.DEBUG else None,
        redoc_url="/redoc" if settings.DEBUG else None,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    setup_telemetry(app)

    app.include_router(rabbitholes.router, prefix="/api/v1")
    app.include_router(sources.router, prefix="/api/v1")
    app.include_router(chat.router, prefix="/api/v1")
    app.include_router(export.router, prefix="/api/v1")
    app.include_router(narrator.router, prefix="/api/v1")
    app.include_router(ws.router)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "service": "rhm-api"}

    return app


app = create_app()
