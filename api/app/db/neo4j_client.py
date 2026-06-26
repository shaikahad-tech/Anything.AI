"""Neo4j driver — async-safe singleton."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

from neo4j import AsyncGraphDatabase, AsyncSession

from app.config import settings

_driver = AsyncGraphDatabase.driver(
    settings.NEO4J_URI,
    auth=(settings.NEO4J_USER, settings.NEO4J_PASSWORD),
    max_connection_pool_size=50,
)


@asynccontextmanager
async def neo4j_session() -> AsyncSession:  # type: ignore[misc]
    async with _driver.session() as session:
        yield session


async def close_driver() -> None:
    await _driver.close()


async def run_query(
    query: str, parameters: dict[str, Any] | None = None
) -> list[dict[str, Any]]:
    """
    Execute a parameterized Cypher query and return results as plain dicts.
    NEVER build Cypher via string concatenation — always use parameters.
    """
    async with neo4j_session() as session:
        result = await session.run(query, parameters or {})
        return [dict(record) async for record in result]
