"""Supabase JWT decoding.

Supabase issues HS256 JWTs signed with the project's JWT secret. We verify
locally (symmetric key, zero-network-cost) rather than calling JWKS per
request. All decode failures surface as HTTP 401 — callers should not have
to know which specific PyJWT exception fired.
"""

import jwt
from fastapi import HTTPException, status

from app.config import settings


def decode_supabase_jwt(token: str) -> dict:
    """Verify a Supabase access token and return its claims.

    Raises HTTPException(401) on any validation failure (expired, wrong
    signature, wrong audience, malformed).
    """
    try:
        return jwt.decode(
            token,
            settings.SUPABASE_JWT_SECRET,
            algorithms=["HS256"],
            audience="authenticated",
        )
    except jwt.ExpiredSignatureError as e:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token expired") from e
    except jwt.InvalidTokenError as e:
        # Base class for bad signature, wrong audience, malformed, etc.
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token") from e
