"""Tests for the HTTP auth/db dependencies.

These probe the dependency wiring, not the underlying services — those
each have their own test module. The goal here is to catch regressions in
how `get_current_user` behaves at the HTTP boundary: missing header,
malformed header, bad token, valid token.
"""

import jwt
from fastapi import APIRouter, Depends, FastAPI
from httpx import ASGITransport, AsyncClient

from app.config import settings
from app.dependencies import get_current_user


def _build_probe_app() -> FastAPI:
    """Stand up a throwaway app that exposes a single probe route.

    We keep this off the real `app` so the test cannot accidentally depend
    on middleware ordering from main.py.
    """
    probe = FastAPI()
    router = APIRouter()

    @router.get("/probe")
    async def probe_route(user: dict = Depends(get_current_user)):
        return {"sub": user["sub"]}

    probe.include_router(router)
    return probe


async def test_missing_header_rejected():
    app = _build_probe_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.get("/probe")
    assert resp.status_code in (401, 403)


async def test_malformed_header_rejected():
    app = _build_probe_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.get("/probe", headers={"Authorization": "NotBearer x"})
    assert resp.status_code in (401, 403)


async def test_bad_token_returns_401():
    app = _build_probe_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.get("/probe", headers={"Authorization": "Bearer garbage.token.here"})
    assert resp.status_code == 401


async def test_valid_token_extracts_sub():
    token = jwt.encode(
        {"sub": "user-xyz", "aud": "authenticated", "exp": 9999999999},
        settings.SUPABASE_JWT_SECRET,
        algorithm="HS256",
    )
    app = _build_probe_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.get("/probe", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json() == {"sub": "user-xyz"}
