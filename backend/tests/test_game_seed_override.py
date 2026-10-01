"""POST /game/session seed hook — no DB needed.

`get_run_seed_override` returns None in production. In-process harnesses
(playtest, balance sweep) override it so a run's seed is the harness seed,
which is what makes paired comparisons possible. The service is stubbed so
the router wiring is tested without Postgres.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import pytest

from app.dependencies import get_run_seed_override
from app.game.generator import generate_run
from app.main import app


@pytest.fixture
def captured(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    async def fake_create(*, user_id: UUID, db: Any, rng_seed_source: int | None = None):
        calls.append({"user_id": user_id, "rng_seed_source": rng_seed_source})
        return generate_run(
            user_id=user_id, setting_id="arctic_station_v1", seed=rng_seed_source or 1
        )

    monkeypatch.setattr("app.game.router.create_game_session", fake_create)
    return calls


async def test_default_seed_override_is_none() -> None:
    assert await get_run_seed_override() is None


async def test_session_create_passes_no_seed_by_default(client, captured) -> None:
    resp = await client.post("/game/session")
    assert resp.status_code == 201, resp.text
    assert captured[-1]["rng_seed_source"] is None


async def test_session_create_uses_overridden_seed(client, captured) -> None:
    app.dependency_overrides[get_run_seed_override] = lambda: 777
    resp = await client.post("/game/session")
    assert resp.status_code == 201, resp.text
    assert captured[-1]["rng_seed_source"] == 777


async def test_session_body_still_rejects_client_seed(client, captured) -> None:
    """Players can never pick a seed: the request body stays extra=forbid."""
    resp = await client.post("/game/session", json={"seed": 5})
    assert resp.status_code == 422
    assert captured == []
