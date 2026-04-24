"""End-to-end: real local Supabase ES256 token → /me returns 200.

Skipped unless:
  - Local Supabase Auth is reachable at `settings.SUPABASE_URL`
  - Local Postgres is reachable at `settings.DATABASE_URL`
  - The ANON key env var is provided (only needed for the signup call)

Run with:
  SUPABASE_URL=http://127.0.0.1:54321 \\
  DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:54322/postgres \\
  SUPABASE_ANON_KEY="eyJhbGc..." \\
  uv run pytest tests/test_integration_supabase_jwks.py -v

The test is marked `integration` so the default `uv run pytest tests/`
does not depend on local Supabase being up.
"""

import os
import time

import httpx
import pytest
from sqlalchemy import text

from app.auth.jwks import reset_jwks_client
from app.config import settings
from app.database import async_session_factory

pytestmark = pytest.mark.integration


async def _skip_if_missing_deps() -> str:
    anon = os.environ.get("SUPABASE_ANON_KEY")
    if not anon:
        pytest.skip("SUPABASE_ANON_KEY not set")
    try:
        async with async_session_factory() as s:
            await s.execute(text("SELECT 1"))
    except Exception as exc:  # pragma: no cover - env-dependent
        pytest.skip(f"Postgres unreachable: {exc}")
    try:
        async with httpx.AsyncClient(timeout=2.0) as c:
            r = await c.get(settings.SUPABASE_URL.rstrip("/") + "/auth/v1/.well-known/jwks.json")
            r.raise_for_status()
    except Exception as exc:  # pragma: no cover - env-dependent
        pytest.skip(f"Supabase JWKS unreachable at {settings.SUPABASE_URL}: {exc}")
    return anon


async def test_real_supabase_token_passes_through_me(unauth_client):
    anon = await _skip_if_missing_deps()
    # Force a fresh PyJWKClient bound to settings.SUPABASE_URL in case a
    # prior test in the session cached a different URL.
    reset_jwks_client()

    email = f"jwks-e2e-{int(time.time())}@outpost.local"
    async with httpx.AsyncClient(timeout=5.0) as c:
        signup = await c.post(
            settings.SUPABASE_URL.rstrip("/") + "/auth/v1/signup",
            headers={"apikey": anon, "Content-Type": "application/json"},
            json={
                "email": email,
                "password": "Test1234!",
                "data": {"name": "JWKS Integration"},
            },
        )
    signup.raise_for_status()
    body = signup.json()
    access_token = body.get("access_token")
    assert access_token, f"signup did not return an access_token: {body}"

    resp = await unauth_client.get("/me", headers={"Authorization": f"Bearer {access_token}"})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["id"] == body["user"]["id"]
    assert data["email"] == email
    # Bootstrap trigger should have seeded the display_name from metadata.
    assert data["display_name"] == "JWKS Integration"
