"""JWKS (JSON Web Key Set) client for Supabase asymmetric token verification.

Supabase exposes a JWKS endpoint at `{SUPABASE_URL}/auth/v1/.well-known/
jwks.json` whose content drives verification of ES256/RS256 access tokens.
Legacy projects sign HS256 and their JWKS may be empty — that's fine, we
only consult this client when the incoming token's header advertises an
asymmetric algorithm.

Caching: `PyJWKClient` maintains a time-based JWK set cache (300s) plus an
LRU per-key cache. The first resolution per process does a sync HTTP
fetch; subsequent are in-memory. For our request volumes that's
negligible, but if latency on the cold path becomes a concern we can
pre-warm by calling `get_signing_keys()` in the FastAPI lifespan hook.
"""

from jwt import PyJWKClient

from app.config import settings

_JWKS_PATH = "/auth/v1/.well-known/jwks.json"

_client: PyJWKClient | None = None


def get_jwks_client() -> PyJWKClient:
    """Return the process-wide PyJWKClient, constructing it on first call."""
    global _client
    if _client is None:
        jwks_url = settings.SUPABASE_URL.rstrip("/") + _JWKS_PATH
        _client = PyJWKClient(
            jwks_url,
            cache_jwk_set=True,
            lifespan=300,
            cache_keys=True,
            max_cached_keys=16,
            timeout=5,
        )
    return _client


def reset_jwks_client() -> None:
    """Test-only escape hatch: drop the cached client so a test can swap in
    a different SUPABASE_URL or monkeypatch `get_signing_key_from_jwt`."""
    global _client
    _client = None
