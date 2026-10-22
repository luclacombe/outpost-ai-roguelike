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

`fake_haiku_response` and `fake_sonnet_response` are **factories** (plain
module-level functions called with kwargs in tests), not pytest fixtures.
They shape JSON bodies that match the Anthropic `messages.create` result
so respx-mocked HTTP calls can be authored without hand-rolling the
envelope. Shared across the agent-node tests so both Haiku (debate /
autonomous) and Sonnet (narration) coverage use the same scaffolding.
"""

# Must precede every other `app.*` import: swaps in pinned settings so unit
# runs ignore the developer's .env LLM / gate / tracing values.
import tests._settings_isolation  # noqa: F401

# isort: split

from typing import Any
from uuid import uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.agent import nodes
from app.agent.constants import MODEL_HAIKU, MODEL_SONNET
from app.dependencies import get_current_user
from app.main import app


@pytest.fixture(autouse=True)
def _reset_forced_tool_memory():
    """`nodes._FORCED_REJECTED` is per-process state; keep tests independent."""
    nodes._FORCED_REJECTED.clear()
    yield
    nodes._FORCED_REJECTED.clear()


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


# ---------------------------------------------------------------------------
# Anthropic response factories (plain functions, not fixtures)
# ---------------------------------------------------------------------------


def fake_haiku_response(
    *,
    text_xml: str = "",
    tool_uses: list[dict[str, Any]] | None = None,
    usage_overrides: dict[str, int] | None = None,
) -> dict[str, Any]:
    """Build a JSON body shaped like an Anthropic Haiku messages.create result."""
    content: list[dict[str, Any]] = []
    if text_xml:
        content.append({"type": "text", "text": text_xml})
    for tu in tool_uses or []:
        content.append(
            {
                "type": "tool_use",
                "id": tu.get("id", "toolu_" + uuid4().hex[:8]),
                "name": tu["name"],
                "input": tu["input"],
            }
        )

    usage = {
        "input_tokens": 1200,
        "output_tokens": 400,
        "cache_read_input_tokens": 800,
        "cache_creation_input_tokens": 0,
    }
    usage.update(usage_overrides or {})
    return {
        "id": "msg_" + uuid4().hex[:8],
        "type": "message",
        "role": "assistant",
        "model": MODEL_HAIKU,
        "content": content,
        "stop_reason": "end_turn",
        "usage": usage,
    }


def fake_sonnet_response(
    text: str,
    *,
    usage_overrides: dict[str, int] | None = None,
) -> dict[str, Any]:
    """Build a JSON body shaped like an Anthropic Sonnet messages.create result.

    Narration is prose-only (no tool_use blocks), so the shape is narrower
    than `fake_haiku_response`: one positional `text` arg, no tool_uses.
    """
    usage = {
        "input_tokens": 1500,
        "output_tokens": 300,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0,
    }
    usage.update(usage_overrides or {})
    return {
        "id": "msg_sonnet_1",
        "type": "message",
        "role": "assistant",
        "model": MODEL_SONNET,
        "content": [{"type": "text", "text": text}],
        "stop_reason": "end_turn",
        "usage": usage,
    }
