"""Shared pytest fixtures.

The `client` fixture wires httpx.AsyncClient directly to the FastAPI app via
ASGITransport — no network, no uvicorn. That keeps test runs fast and avoids
needing a free port.
"""

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.main import app


@pytest_asyncio.fixture
async def client():
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c
