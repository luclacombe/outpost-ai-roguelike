"""Orchestrator: drives N simulated games with LLMPlayer + emits archives.

Entry points:
  - `run_harness(config)` — async, programmatic API used by tests
  - `main()` — CLI wrapper (uv run python -m scripts.play_test)

The CLI defaults to: 7 personas × 6 runs × 3 settings (round-robin) = 42 games.
Real-LLM mode requires `ANTHROPIC_API_KEY`. The persona side runs through
`claude` CLI and bills against the user's Claude Code Max subscription.

The meta-aggregator runs SEPARATELY via the /playtest-aggregate slash command
in the user's Claude Code session — see .claude/commands/playtest-aggregate.md.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import select, text

from app.database import async_session_factory
from app.eval.playtest.app_state import harness_app_state
from app.eval.playtest.llm_player import LLMPlayer
from app.eval.playtest.oracles import compute_corpus_oracles, compute_per_run_oracles
from app.eval.playtest.output import (
    PerRunArchive,
    write_corpus_artifacts,
    write_one_archive,
)
from app.eval.playtest.personas import PERSONAS, get_persona
from app.eval.playtest.schemas import PersonaId
from app.eval.playtest.subprocess_pool import SubprocessPool
from app.eval.types import RunRecord
from app.game.engine import compute_end_state
from app.game.exceptions import GameNotEnded
from app.game.models import GameSession
from app.game.post_mortem import build_post_mortem
from app.game.schemas import ColonyState, EndState
from app.game.service import _pick_setting_id
from app.game.setting_loader import list_available_settings

logger = logging.getLogger(__name__)


@dataclass
class PlaytestConfig:
    """Runtime configuration for one harness invocation."""

    personas: list[PersonaId] = field(default_factory=lambda: [p.id for p in PERSONAS])
    runs_per_persona: int = 6
    settings: list[str] = field(
        default_factory=lambda: [
            "arctic_station_v1",
            "orbital_colony_v1",
            "desert_outpost_v1",
        ]
    )
    out_dir: Path = field(default_factory=lambda: Path("docs/playtest"))
    shell_dir: Path = field(default_factory=lambda: Path("/tmp/outpost-playtest-shell"))
    max_turns: int = 12
    max_concurrency: int = 5
    git_sha: str = "unknown"
    # If True, delete each game's synthetic auth.users + profiles row after the
    # run completes. Default False: synthetic users persist for forensic
    # re-analysis. Smoke tests set this True to keep the test DB clean.
    cleanup_after_run: bool = False


def resolve_unfinished_end_state(state: ColonyState) -> EndState:
    """Derive a terminal `EndState` for a run the harness left ambiguous.

    Sub-bug (ii) fix: if the harness loop exits with `state.end_state is
    None` (e.g. the iteration counter exhausted before `/decide` ran for
    the terminal turn, or a defensive `pr is None` break fired), the run
    is recorded as `UNKNOWN` and the verdict step never runs. This
    function fills in the gap per SPEC §10 — every run resolves to one
    of {ENDURED, COLLAPSED, OUSTED, KILLED}.

    Strategy:
      1. If `end_state` is already set, return it (idempotent).
      2. Reuse `engine.compute_end_state` priority order via a synthetic
         state with `turn_number == max_turns`. This forces the
         turn-limit branch to fire when the early branches (player dead,
         all agents dead, resource at 0) don't apply, so we get the same
         ENDURED-vs-COLLAPSED arbitration the engine would have used at
         a clean max-turn boundary.
      3. If `compute_end_state` still returns None (defensive — should
         not happen with the synthetic state), default to COLLAPSED so
         the run is at least labelled and verdict-eligible.

    The function does NOT mutate the engine's persisted state — the
    orchestrator is responsible for writing the resolved `end_state`
    back to `game_sessions.end_state` if it wants to enable the
    post-mortem path.
    """
    if state.end_state is not None:
        return state.end_state

    # Force the turn-limit branch by setting turn_number = max_turns. If
    # the colony is otherwise viable, compute_end_state returns ENDURED;
    # if any rail is broken (resources, legitimacy, agent count) it
    # returns COLLAPSED.
    synthetic = state.model_copy(update={"turn_number": state.max_turns})
    derived = compute_end_state(synthetic)
    if derived is not None:
        return derived
    # compute_end_state returned None despite turn_number >= max_turns —
    # only possible if every gate passed somehow. Default to COLLAPSED
    # rather than silently leaving end_state unset.
    return EndState.COLLAPSED


async def _provision_synthetic_user(user_id: UUID, email: str) -> None:
    """Insert auth.users + profiles row for a per-game synthetic user.

    Mirrors tests/eval/conftest.py::_ensure_auth_user. Each game in the
    harness gets its own user_id so the daily-run gate
    (one-run-per-account-per-day) doesn't reject games 2..N.
    """
    async with async_session_factory() as s:
        await s.execute(
            text(
                """
                INSERT INTO auth.users (
                    id, instance_id, aud, role, email, encrypted_password,
                    email_confirmed_at, created_at, updated_at
                )
                VALUES (
                    :id,
                    '00000000-0000-0000-0000-000000000000'::uuid,
                    'authenticated', 'authenticated', :email,
                    '', now(), now(), now()
                )
                ON CONFLICT (id) DO NOTHING
                """
            ),
            {"id": user_id, "email": email},
        )
        await s.execute(
            text(
                """
                INSERT INTO profiles (id, display_name, total_runs, created_at)
                VALUES (:id, :name, 0, now())
                ON CONFLICT (id) DO NOTHING
                """
            ),
            {"id": user_id, "name": "Playtest Synthetic User"},
        )
        await s.commit()


async def _cleanup_synthetic_user(user_id: UUID) -> None:
    """Delete a per-game synthetic auth.users row (cascade removes the
    profiles row + game_sessions + agents + turn_logs via FK)."""
    async with async_session_factory() as s:
        await s.execute(text("DELETE FROM auth.users WHERE id = :id"), {"id": user_id})
        await s.commit()


def _allocate_runs(config: PlaytestConfig) -> list[tuple[PersonaId, str, int]]:
    """Round-robin (persona, setting, seed) tuples.

    Setting index is `(persona_idx * runs_per_persona + run_idx) % len(settings)`,
    which matches the prior persona-local rotation when
    `runs_per_persona % len(settings) == 0` (the production 7×6×3 = 42 case)
    but stays balanced for small smoke runs where `runs_per_persona < len(settings)`.
    """
    out: list[tuple[PersonaId, str, int]] = []
    for persona_idx, persona_id in enumerate(config.personas):
        for run_idx in range(config.runs_per_persona):
            setting_idx = (persona_idx * config.runs_per_persona + run_idx) % len(config.settings)
            setting = config.settings[setting_idx]
            seed = hash((persona_id, setting, run_idx)) & 0x7FFFFFFF
            out.append((persona_id, setting, seed))
    return out


def _uuid_for_setting(setting_id: str, now_utc: datetime, max_attempts: int = 1000) -> UUID:
    """Generate a `uuid4` whose `_pick_setting_id(uid, now_utc)` returns the
    requested setting.

    `app.game.service.create_game_session` runs `_pick_setting_id` on the
    incoming `user_id` and serves whatever setting that picker returns —
    ignoring any setting the harness wanted. Brute-forcing uuids is the
    cheapest way to drive a specific setting per game without growing the
    engine API: with ~3 valid settings, expected attempts is ~3.

    Raises `ValueError` if `setting_id` isn't in the engine's available pool
    (otherwise the loop spins until `max_attempts`).
    """
    available = set(list_available_settings())
    if setting_id not in available:
        raise ValueError(f"setting {setting_id!r} not in available settings {sorted(available)!r}")
    for _ in range(max_attempts):
        candidate = uuid.uuid4()
        if _pick_setting_id(candidate, now_utc) == setting_id:
            return candidate
    raise RuntimeError(
        f"could not find a uuid mapping to setting {setting_id!r} after {max_attempts} attempts"
    )


async def _drive_one_game(
    *,
    persona_id: PersonaId,
    setting_id: str,
    seed: int,
    pool: SubprocessPool,
    config: PlaytestConfig,
) -> PerRunArchive:
    """Drive one simulated game with an LLMPlayer. Returns the archive bundle.

    Provisions a fresh synthetic user (auth.users + profiles row) per game so
    the daily-run gate doesn't reject games 2..N. The user_id is brute-forced
    via `_uuid_for_setting` so the engine's deterministic per-user setting
    picker returns the *requested* setting (not whatever a fresh uuid4 would
    happen to hash to). Game seeds remain reproducible per
    `(persona_id, setting, run_idx)`; user_ids do not.
    """
    from tests.fixtures.run_simulator import simulate_game_with_player  # noqa: PLC0415

    persona = get_persona(persona_id)
    # Brute-force a uuid whose engine-side picker yields the requested setting,
    # otherwise per-cell aggregation collapses to whatever rotation the
    # picker happens to produce for fresh uuid4s.
    per_game_user_id = _uuid_for_setting(setting_id, datetime.now(UTC))
    per_game_email = f"playtest-{per_game_user_id}@outpost.local"
    await _provision_synthetic_user(per_game_user_id, per_game_email)

    try:
        # A ref-cell updated by on_turn_input before each pick().
        last_turn_input: dict[str, Any] = {}

        def turn_input_provider() -> dict[str, Any]:
            return dict(last_turn_input)

        def on_turn_input(d: dict[str, Any]) -> None:
            last_turn_input.clear()
            last_turn_input.update(d)

        player = LLMPlayer(
            persona=persona,
            pool=pool,
            shell_dir=config.shell_dir,
            seed=seed,
            turn_input_provider=turn_input_provider,
        )

        run_record = await simulate_game_with_player(
            setting_id=setting_id,
            seed=seed,
            real_llm=False,
            max_turns=config.max_turns,
            user_id=per_game_user_id,
            player=player,
            on_turn_input=on_turn_input,
        )

        # End-of-run: look up the most-recently started game session for this user.
        async with async_session_factory() as session:
            session_row = (
                await session.execute(
                    select(GameSession)
                    .where(GameSession.user_id == per_game_user_id)
                    .order_by(GameSession.started_at.desc())
                    .limit(1)
                )
            ).scalar_one()

            # Sub-bug (ii) defensive resolution: if the harness loop exited
            # leaving end_state=None, derive a terminal state per SPEC §10
            # and persist it back so build_post_mortem succeeds and the
            # verdict step has something to score. The harness loop exits
            # without setting end_state when its iteration counter exhausts
            # before /decide fires for the terminal turn, OR when a defensive
            # break (`pr is None`, `not proposals`) trips on a healthy run.
            final_state = ColonyState.model_validate(session_row.state)
            if final_state.end_state is None:
                resolved = resolve_unfinished_end_state(final_state)
                logger.warning(
                    "harness exit with end_state=None for game %s "
                    "(persona=%s setting=%s seed=%s turns=%s); "
                    "resolving defensively to %s",
                    session_row.id,
                    persona_id,
                    setting_id,
                    seed,
                    final_state.turn_number,
                    resolved.value,
                )
                final_state = final_state.model_copy(update={"end_state": resolved})
                session_row.state = final_state.model_dump(mode="json")
                session_row.end_state = resolved.value
                if session_row.ended_at is None:
                    session_row.ended_at = datetime.now(UTC)
                await session.commit()
                # Mirror into the run_record so the per-run summary reflects
                # the resolution (the run aggregator already loaded a stale
                # snapshot before resolution).
                run_record = run_record.model_copy(update={"end_state": resolved})

            try:
                post_mortem = await build_post_mortem(game_id=session_row.id, db=session)
            except GameNotEnded:
                # Should not happen now that we always resolve end_state
                # above, but keep the guard so the orchestrator still
                # records the run if a future regression breaks the
                # resolver.
                post_mortem = None

        verdict_error: str | None = None
        verdict_obj = None
        if post_mortem is not None:
            # Same blocking-subprocess concern as `player.pick` — run in a
            # thread so concurrent games' verdict calls don't serialize.
            verdict_result = await asyncio.to_thread(
                pool.run_final_verdict,
                session=player._session,  # noqa: SLF001 — orchestrator is the only legit consumer
                post_mortem_json=post_mortem.model_dump(mode="json"),
            )
            verdict_obj = verdict_result.verdict
            verdict_error = verdict_result.error

        # Compute per-run oracles using agents from the final ColonyState.
        per_run_oracles = compute_per_run_oracles(run_record, agents=final_state.agents)

        return PerRunArchive(
            run_id=str(session_row.id),
            persona_id=persona_id,
            # Use the actually-served setting from the run record, not the
            # requested setting — the daily-gate-aware create_game_session
            # may pick a different setting (one-run-per-day → server rotation).
            setting_id=run_record.setting_id,
            seed=seed,
            end_state=str(run_record.end_state.value if run_record.end_state else "UNKNOWN"),
            turns_played=run_record.turns_played,
            per_turn_outputs=player.per_turn_outputs,
            verdict=verdict_obj,
            verdict_error=verdict_error,
            run_record_summary={
                "end_state": run_record.end_state,
                "final_player_health": run_record.final_player_health,
                "agents_alive_at_end": run_record.agents_alive_at_end,
                "total_agents": run_record.total_agents,
                "cost_cents": run_record.cost_cents,
                "tool_calls": [tc.model_dump() for tc in run_record.tool_calls],
                "agent_responses_per_agent": {
                    str(k): v for k, v in run_record.agent_responses_per_agent.items()
                },
                "resource_history": run_record.resource_history,
                "forced_choice_turns": run_record.forced_choice_turns,
                "narration_per_turn": run_record.narration_per_turn,
            },
            per_run_oracles=per_run_oracles,
        )
    finally:
        if config.cleanup_after_run:
            await _cleanup_synthetic_user(per_game_user_id)


async def run_harness(*, config: PlaytestConfig) -> None:
    """Top-level entry. Allocates runs, drives them with bounded concurrency,
    emits archives + corpus artifacts.

    Wraps the driving loop in `harness_app_state()` so `app.state.graph`
    + `app.state.checkpointer` are attached for the in-process ASGI POSTs
    that drive each game (FastAPI lifespan does NOT run under
    `httpx.ASGITransport`, so this bootstrap is mandatory).
    """
    allocations = _allocate_runs(config)

    config.out_dir.mkdir(parents=True, exist_ok=True)
    config.shell_dir.mkdir(parents=True, exist_ok=True)
    pool = SubprocessPool(max_concurrency=config.max_concurrency)
    started = time.monotonic()
    archives: list[PerRunArchive] = []
    runs_for_corpus: list[RunRecord] = []

    sema = asyncio.Semaphore(config.max_concurrency)

    async def go(persona_id: PersonaId, setting_id: str, seed: int) -> None:
        async with sema:
            try:
                arc = await _drive_one_game(
                    persona_id=persona_id,
                    setting_id=setting_id,
                    seed=seed,
                    pool=pool,
                    config=config,
                )
                # Persist before append so a SIGTERM mid-orchestrator-loop still
                # leaves a recoverable corpus on disk.
                write_one_archive(config.out_dir, arc)
                archives.append(arc)
            except Exception as exc:  # noqa: BLE001 — log and continue
                logger.exception("game %s/%s/%s failed: %s", persona_id, setting_id, seed, exc)
                pool.findings.aborted_runs.append(
                    {
                        "persona": persona_id,
                        "setting": setting_id,
                        "seed": seed,
                        "reason": str(exc),
                    }
                )

    async with harness_app_state():
        await asyncio.gather(*(go(p, s, sd) for (p, s, sd) in allocations))

    # Reconstruct RunRecords for corpus oracles from per-archive summary fields.
    for a in archives:
        rr = RunRecord(
            setting_id=a.setting_id,
            policy=a.persona_id,
            seed=a.seed,
            end_state=a.run_record_summary.get("end_state"),
            turns_played=a.turns_played,
            final_player_health=a.run_record_summary.get("final_player_health", 0),
            agents_alive_at_end=a.run_record_summary.get("agents_alive_at_end", 0),
            total_agents=a.run_record_summary.get("total_agents", 0),
            cost_cents=a.run_record_summary.get("cost_cents", 0),
            tool_calls=a.run_record_summary.get("tool_calls", []),
            agent_responses_per_agent=a.run_record_summary.get("agent_responses_per_agent", {}),
            resource_history=a.run_record_summary.get("resource_history", {}),
            forced_choice_turns=a.run_record_summary.get("forced_choice_turns", 0),
            narration_per_turn=a.run_record_summary.get("narration_per_turn", []),
        )
        runs_for_corpus.append(rr)

    corpus_oracles = compute_corpus_oracles(runs_for_corpus)
    findings_dict = {
        "malformed_json_count": pool.findings.malformed_json_count,
        "invalid_pick_count": pool.findings.invalid_pick_count,
        "persona_refusals": pool.findings.persona_refusals,
        "subprocess_hangs": pool.findings.subprocess_hangs,
        "subprocess_crashes": pool.findings.subprocess_crashes,
        "aborted_runs": pool.findings.aborted_runs,
        "by_persona": dict(pool.findings.by_persona),
    }

    write_corpus_artifacts(
        config.out_dir,
        archives=archives,
        findings=findings_dict,
        corpus_oracles=corpus_oracles,
        git_sha=config.git_sha,
        wall_clock_seconds=int(time.monotonic() - started),
    )

    logger.info(
        "harness complete: %d runs in %d sec → %s",
        len(archives),
        int(time.monotonic() - started),
        config.out_dir,
    )


def main() -> None:  # pragma: no cover — CLI entry
    """CLI: defaults to 7 × 6 = 42 runs across 3 settings."""
    import argparse
    import os

    p = argparse.ArgumentParser(description="Run the LLM play-test harness.")
    p.add_argument("--out", default=None, help="Output dir; default docs/playtest/<date>")
    p.add_argument("--max-concurrency", type=int, default=5)
    p.add_argument("--runs-per-persona", type=int, default=6)
    args = p.parse_args()

    from datetime import UTC, datetime

    today = datetime.now(UTC).strftime("%Y-%m-%d")
    if args.out:
        out_dir = Path(args.out)
    else:
        # Anchor to repo root so artifacts land in <repo>/docs/playtest/<date>/
        # regardless of cwd. Script lives at <repo>/backend/scripts/play_test.py.
        repo_root = Path(__file__).resolve().parent.parent.parent
        out_dir = repo_root / "docs" / "playtest" / today

    git_sha = os.environ.get("OUTPOST_GIT_SHA", "unknown")

    config = PlaytestConfig(
        out_dir=out_dir,
        max_concurrency=args.max_concurrency,
        runs_per_persona=args.runs_per_persona,
        git_sha=git_sha,
    )

    asyncio.run(run_harness(config=config))


if __name__ == "__main__":  # pragma: no cover
    main()
