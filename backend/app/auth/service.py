"""Supabase JWT decoding.

Supabase is in the middle of migrating from symmetric (HS256, legacy JWT
secret) to asymmetric (ES256/RS256, JWKS) signing. We accept both:
legacy projects keep working against `SUPABASE_JWT_SECRET`, and
JWKS-signed tokens verify against the project's published public key.

Algorithm dispatch is strict — we read the token header's `alg`, pick
the matching verification path, and pass *only* that algorithm to
`jwt.decode`. This closes the classic "alg confusion" attack where an
attacker flips an RS256 token to HS256 and uses the public key as the
HMAC secret.

All verification failures (expired, bad signature, wrong audience,
malformed, JWKS fetch failure) surface as HTTP 401. Callers should not
have to map the PyJWT exception taxonomy.
"""

import jwt
from fastapi import HTTPException, status
from jwt.exceptions import PyJWKClientError

from app.auth.jwks import get_jwks_client
from app.config import settings

# Whitelisted asymmetric algorithms. Kept narrow — Supabase's JWKS today
# serves ES256; RS256 support is cheap insurance.
_ASYM_ALGORITHMS = {"ES256", "RS256"}


def decode_supabase_jwt(token: str) -> dict:
    """Verify a Supabase access token and return its claims.

    Dispatches on the token's header `alg`:
      - HS256        → verified against `settings.SUPABASE_JWT_SECRET`
      - ES256/RS256  → verified against the project's JWKS public key

    Raises HTTPException(401) on any failure.
    """
    try:
        header = jwt.get_unverified_header(token)
    except jwt.InvalidTokenError as e:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token") from e

    alg = header.get("alg")

    try:
        if alg == "HS256":
            return jwt.decode(
                token,
                settings.SUPABASE_JWT_SECRET,
                algorithms=["HS256"],
                audience="authenticated",
            )
        if alg in _ASYM_ALGORITHMS:
            signing_key = get_jwks_client().get_signing_key_from_jwt(token)
            return jwt.decode(
                token,
                signing_key.key,
                algorithms=[alg],
                audience="authenticated",
            )
        # Unknown / unsupported algorithm — reject before touching key material.
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    except jwt.ExpiredSignatureError as e:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token expired") from e
    except (jwt.InvalidTokenError, PyJWKClientError) as e:
        # Base class for bad signature, wrong audience, malformed, plus
        # JWKS-specific failures (key not found, fetch error).
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token") from e
