"""
JWT / Clerk authentication dependency.

In development (ENVIRONMENT=development, DEBUG=True) with no CLERK_SECRET_KEY set,
the auth dependency short-circuits and returns a synthetic dev user so the API is
usable without a full Clerk setup. In production, the Clerk session token is
validated against the Clerk JWKS endpoint.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Annotated

import httpx
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwk, jwt

from app.config import settings

_bearer = HTTPBearer(auto_error=False)

# Cached JWKS for the process lifetime (refreshed lazily on decode failure).
_jwks_cache: dict | None = None


@dataclass
class AuthUser:
    id: uuid.UUID
    clerk_user_id: str
    email: str


_DEV_USER = AuthUser(
    id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
    clerk_user_id="dev_user",
    email="dev@localhost",
)


async def _get_jwks() -> dict:
    global _jwks_cache
    if _jwks_cache:
        return _jwks_cache
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"https://api.clerk.dev/v1/jwks",
            headers={"Authorization": f"Bearer {settings.CLERK_SECRET_KEY}"},
        )
        resp.raise_for_status()
        _jwks_cache = resp.json()
        return _jwks_cache


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> AuthUser:
    # Dev short-circuit — never active in production
    if settings.DEBUG and not settings.CLERK_SECRET_KEY:
        return _DEV_USER

    if not credentials:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

    token = credentials.credentials
    try:
        header = jwt.get_unverified_header(token)
        kid = header.get("kid")
        jwks = await _get_jwks()
        matching_key = next(
            (k for k in jwks.get("keys", []) if k.get("kid") == kid), None
        )
        if not matching_key:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token key"
            )
        public_key = jwk.construct(matching_key)
        payload = jwt.decode(
            token,
            public_key,
            algorithms=["RS256"],
            options={"verify_aud": False},
        )
        clerk_user_id: str = payload["sub"]
        email: str = payload.get("email", "")
        # In a real system you'd look up the user in Postgres here; for the
        # initial scaffold we derive the UUID deterministically from the Clerk ID.
        user_id = uuid.uuid5(uuid.NAMESPACE_DNS, clerk_user_id)
        return AuthUser(id=user_id, clerk_user_id=clerk_user_id, email=email)
    except (JWTError, KeyError, StopIteration) as exc:
        global _jwks_cache
        _jwks_cache = None  # force refresh on next request
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
        ) from exc


CurrentUser = Annotated[AuthUser, Depends(get_current_user)]
