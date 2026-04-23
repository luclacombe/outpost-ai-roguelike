"""FastAPI dependency providers.

`get_current_user` is the auth gate: it decodes the bearer token and returns
the raw Supabase JWT claim dict. Feature routers depend on this and pull
`user["sub"]` for the user UUID — they do not re-parse the token themselves.

`get_db` yields a short-lived AsyncSession from the module-level factory.
Transaction ownership stays with the caller: do `await db.commit()` after
the business logic succeeds, or rely on the implicit rollback when the
context manager exits with an exception.
"""

from collections.abc import AsyncGenerator

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.service import decode_supabase_jwt
from app.database import async_session_factory

# auto_error=True => FastAPI returns 403 when the Authorization header is
# missing or malformed, before our handler runs. Decode failures from a
# present-but-bad token surface as 401 from decode_supabase_jwt.
bearer_scheme = HTTPBearer(auto_error=True)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with async_session_factory() as session:
        yield session


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
) -> dict:
    return decode_supabase_jwt(credentials.credentials)
