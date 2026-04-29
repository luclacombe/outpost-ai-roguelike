"""Regression tests for concurrent-game correctness in `simulate_game_with_player`.

Covers two pre-existing bugs surfaced by scaling the playtest harness:

  * **Auth race (fixed via ContextVar):** concurrent games used to stomp
    `app.dependency_overrides[get_current_user]` on the singleton FastAPI app.
  * **Pick serialization (fixed via `asyncio.to_thread`):** `player.pick(...)`
    is sync (subprocess-backed for `LLMPlayer`); calling it inline from async
    blocked the event loop, so concurrent games' picks effectively serialized
    even though the engine `/turn` HTTP calls were already async.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any

import pytest
import respx
from sqlalchemy import text

from app.database import async_session_factory
from app.eval.playtest.app_state import harness_app_state
from tests.fixtures.run_simulator import simulate_game_with_player
from tests.fixtures.simulated_player import SimulatedPlayer


async def _ensure_auth_user(user_id: uuid.UUID) -> None:
    async with async_session_factory() as s:
        await s.execute(
            text(
                """
                INSERT INTO auth.users (id, instance_id, aud, role, email, encrypted_password,
                    email_confirmed_at, created_at, updated_at)
                VALUES (:id, '00000000-0000-0000-0000-000000000000'::uuid,
                    'authenticated', 'authenticated', :email, '', now(), now(), now())
                ON CONFLICT (id) DO NOTHING
                """
            ),
            {"id": user_id, "email": f"concurrent-{user_id}@outpost.local"},
        )
        await s.execute(
            text(
                """
                INSERT INTO profiles (id, display_name, total_runs, created_at)
                VALUES (:id, 'Concurrent Test User', 0, now())
                ON CONFLICT (id) DO NOTHING
                """
            ),
            {"id": user_id},
        )
        await s.commit()


async def _cleanup(user_id: uuid.UUID) -> None:
    async with async_session_factory() as s:
        await s.execute(text("DELETE FROM auth.users WHERE id = :id"), {"id": user_id})
        await s.commit()


@pytest.mark.eval
async def test_two_concurrent_games_do_not_stomp_each_others_auth() -> None:
    """Run 2 games concurrently against `simulate_game_with_player`. Each
    must complete its own create_session → /turn → /decide cycle without
    leaking auth into the other task."""
    from tests.fixtures._factories import make_haiku_debate_side_effect

    user_a = uuid.uuid4()
    user_b = uuid.uuid4()
    await _ensure_auth_user(user_a)
    await _ensure_auth_user(user_b)

    try:
        async with harness_app_state():
            with respx.mock(
                base_url="https://api.anthropic.com", assert_all_called=False
            ) as router:
                router.post("/v1/messages").mock(side_effect=make_haiku_debate_side_effect())

                results = await asyncio.gather(
                    simulate_game_with_player(
                        setting_id="arctic_station_v1",
                        seed=11111,
                        real_llm=False,
                        max_turns=2,
                        user_id=user_a,
                        player=SimulatedPlayer("moral", seed=11111),
                        on_turn_input=None,
                    ),
                    simulate_game_with_player(
                        setting_id="arctic_station_v1",
                        seed=22222,
                        real_llm=False,
                        max_turns=2,
                        user_id=user_b,
                        player=SimulatedPlayer("moral", seed=22222),
                        on_turn_input=None,
                    ),
                    return_exceptions=True,
                )

        # Both must succeed (no AssertionError leaking from the simulator's
        # status-code asserts under auth race).
        for i, r in enumerate(results):
            assert not isinstance(r, BaseException), f"game {i} raised under concurrency: {r!r}"
    finally:
        await _cleanup(user_a)
        await _cleanup(user_b)


class _SleepyStubPlayer:
    """Sync pick that sleeps `sleep_sec` and timestamps start/end into `log`.

    Mimics the latency profile of `LLMPlayer.pick` (which subprocess-blocks on
    `claude --print`) so we can detect whether two concurrent games' picks
    actually overlap in time or serialize.
    """

    def __init__(self, name: str, sleep_sec: float, log: list[tuple[str, str, float]]) -> None:
        self.name = name
        self.sleep_sec = sleep_sec
        self._log = log

    def pick(self, proposals: list[Any]) -> str:
        # list.append is atomic under CPython GIL — safe across threads.
        self._log.append((self.name, "start", time.monotonic()))
        time.sleep(self.sleep_sec)
        self._log.append((self.name, "end", time.monotonic()))
        return proposals[0]["id"]


@pytest.mark.eval
async def test_concurrent_picks_overlap_in_time() -> None:
    """Two concurrent `simulate_game_with_player` runs whose stub players each
    sleep 1s in `pick` must overlap — i.e. game B's pick starts before game
    A's pick ends. Without `asyncio.to_thread` at the call site, sync `pick`
    blocks the event loop and picks serialize."""
    from tests.fixtures._factories import make_haiku_debate_side_effect

    user_a = uuid.uuid4()
    user_b = uuid.uuid4()
    await _ensure_auth_user(user_a)
    await _ensure_auth_user(user_b)

    pick_log: list[tuple[str, str, float]] = []
    sleep_sec = 1.0

    try:
        async with harness_app_state():
            with respx.mock(
                base_url="https://api.anthropic.com", assert_all_called=False
            ) as router:
                router.post("/v1/messages").mock(side_effect=make_haiku_debate_side_effect())

                await asyncio.gather(
                    simulate_game_with_player(
                        setting_id="arctic_station_v1",
                        seed=33333,
                        real_llm=False,
                        max_turns=1,
                        user_id=user_a,
                        player=_SleepyStubPlayer("A", sleep_sec, pick_log),
                        on_turn_input=None,
                    ),
                    simulate_game_with_player(
                        setting_id="arctic_station_v1",
                        seed=44444,
                        real_llm=False,
                        max_turns=1,
                        user_id=user_b,
                        player=_SleepyStubPlayer("B", sleep_sec, pick_log),
                        on_turn_input=None,
                    ),
                )

        # Both games must have reached pick at all.
        starts = {name: ts for (name, kind, ts) in pick_log if kind == "start"}
        ends = {name: ts for (name, kind, ts) in pick_log if kind == "end"}
        assert {"A", "B"} <= starts.keys(), f"both picks must run; got {pick_log}"

        # Overlap definition: the second pick to start began before the first
        # pick to end. Robust to which game's HTTP setup happened to win the
        # race to /turn — order is non-deterministic, but overlap is mandatory.
        first_end = min(ends.values())
        last_start = max(starts.values())
        assert last_start < first_end, (
            f"picks did not overlap (serialized): log={pick_log}, "
            f"first_end={first_end:.3f}, last_start={last_start:.3f}"
        )
    finally:
        await _cleanup(user_a)
        await _cleanup(user_b)
