"""Shared pytest fixtures.

Two HTTP clients are exposed:

* `client` — auth is stubbed. Use for the majority of router tests where the
  user identity is incidental. The override returns a fixed test-user claim
  dict, so routes that depend on `get_current_user` get a `sub` of
  "test-user-id".
* `unauth_client` — no override. Use to assert that endpoints reject missing
  or invalid bearer tokens.

Both clients speak to the app in-process via httpx + ASGITransport — no
uvicorn, no sockets.
"""

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.dependencies import get_current_user
from app.main import app


@pytest_asyncio.fixture
async def client():
    app.dependency_overrides[get_current_user] = lambda: {
        "sub": "00000000-0000-0000-0000-000000000001",
        "email": "test@outpost.local",
        "aud": "authenticated",
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def unauth_client():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
