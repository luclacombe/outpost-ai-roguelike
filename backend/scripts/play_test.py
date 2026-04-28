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
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import select

from app.database import async_session_factory
from app.eval.playtest.llm_player import LLMPlayer
from app.eval.playtest.oracles import compute_corpus_oracles, compute_per_run_oracles
from app.eval.playtest.output import (
    PerRunArchive,
    write_corpus_artifacts,
    write_per_run_archives,
)
from app.eval.playtest.personas import PERSONAS, get_persona
from app.eval.playtest.schemas import PersonaId
from app.eval.playtest.subprocess_pool import SubprocessPool
from app.eval.types import RunRecord
from app.game.exceptions import GameNotEnded
from app.game.models import GameSession
from app.game.post_mortem import build_post_mortem
from app.game.schemas import ColonyState

logger = logging.getLogger(__name__)


@dataclass
class PlaytestConfig:
    """Runtime configuration for one harness invocation."""

    personas: list[PersonaId] = field(default_factory=lambda: [p.id for p in PERSONAS])
    runs_per_persona: int = 6
    settings: list[str] = field(
        default_factory=lambda: [
            "arctic_station_v1",
            "orbital_station_v1",
            "desert_outpost_v1",
        ]
    )
    out_dir: Path = field(default_factory=lambda: Path("docs/playtest"))
    shell_dir: Path = field(default_factory=lambda: Path("/tmp/outpost-playtest-shell"))
    user_id: UUID = field(default_factory=uuid.uuid4)
    max_turns: int = 12
    max_concurrency: int = 5
    git_sha: str = "unknown"
    # Safety guard: the daily-run gate (one-run-per-account-per-day) will reject
    # games 2..N when `user_id` is reused. The current `_drive_one_game` does
    # not provision a per-game synthetic user, so a multi-game invocation with
    # this flag false fails fast at run_harness entry. To run more than one
    # game in a single invocation, refactor `_drive_one_game` to insert
    # auth.users + profiles per game (mirror tests/eval/conftest.py::
    # _ensure_auth_user) — or set this flag and accept silent aborts on N-1
    # games for diagnostic purposes.
    allow_daily_gate_violation: bool = False


def _allocate_runs(config: PlaytestConfig) -> list[tuple[PersonaId, str, int]]:
    """Round-robin (persona, setting, seed) tuples. Each persona plays each
    setting at least floor(runs_per_persona / len(settings)) times."""
    out: list[tuple[PersonaId, str, int]] = []
    for persona_id in config.personas:
        for run_idx in range(config.runs_per_persona):
            setting = config.settings[run_idx % len(config.settings)]
            seed = hash((persona_id, setting, run_idx)) & 0x7FFFFFFF
            out.append((persona_id, setting, seed))
    return out


async def _drive_one_game(
    *,
    persona_id: PersonaId,
    setting_id: str,
    seed: int,
    pool: SubprocessPool,
    config: PlaytestConfig,
) -> PerRunArchive:
    """Drive one simulated game with an LLMPlayer. Returns the archive bundle.

    Uses `config.user_id` for the game session. In tests this is the seeded
    eval user; in production it is a synthetic user pre-registered in the DB.
    """
    from tests.fixtures.run_simulator import simulate_game_with_player  # noqa: PLC0415

    persona = get_persona(persona_id)
    per_game_user_id = config.user_id

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

    # End-of-run: look up the most-recently started game session for this user,
    # then build the post-mortem. Using started_at DESC + limit 1 is safe because
    # games run sequentially per user within _drive_one_game.
    async with async_session_factory() as session:
        session_row = (
            await session.execute(
                select(GameSession)
                .where(GameSession.user_id == per_game_user_id)
                .order_by(GameSession.started_at.desc())
                .limit(1)
            )
        ).scalar_one()
        try:
            post_mortem = await build_post_mortem(game_id=session_row.id, db=session)
        except GameNotEnded:
            # max_turns exhausted before a terminal event — no post-mortem available.
            post_mortem = None

    if post_mortem is not None:
        verdict = pool.run_final_verdict(
            session=player._session,  # noqa: SLF001 — orchestrator is the only legit consumer
            post_mortem_json=post_mortem.model_dump(),
        )
    else:
        verdict = None

    # Compute per-run oracles using agents from the final ColonyState.
    final_state = ColonyState.model_validate(session_row.state)
    per_run_oracles = compute_per_run_oracles(run_record, agents=final_state.agents)

    return PerRunArchive(
        run_id=str(session_row.id),
        persona_id=persona_id,
        setting_id=setting_id,
        seed=seed,
        end_state=str(run_record.end_state.value if run_record.end_state else "UNKNOWN"),
        turns_played=run_record.turns_played,
        per_turn_outputs=player.per_turn_outputs,
        verdict=verdict,
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


async def run_harness(*, config: PlaytestConfig) -> None:
    """Top-level entry. Allocates runs, drives them with bounded concurrency,
    emits archives + corpus artifacts."""
    allocations = _allocate_runs(config)
    if len(allocations) > 1 and not config.allow_daily_gate_violation:
        raise RuntimeError(
            f"PlaytestConfig requests {len(allocations)} games but reuses "
            f"one user_id ({config.user_id}). The daily-run gate will reject "
            f"games 2..N. Either (a) refactor _drive_one_game to provision a "
            f"per-game synthetic user (auth.users + profiles row, mirror "
            f"tests/eval/conftest.py::_ensure_auth_user), or (b) set "
            f"PlaytestConfig.allow_daily_gate_violation=True to proceed and "
            f"accept silent aborts on games 2..N. See "
            f"docs/superpowers/specs/2026-04-26-llm-playtest-harness-design.md "
            f"§13 'open questions / future work'."
        )

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

    write_per_run_archives(config.out_dir, archives)
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
