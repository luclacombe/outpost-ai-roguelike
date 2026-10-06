"""Orchestrator: drives N simulated games with LLMPlayer + emits archives.

Entry points:
  - `run_harness(config)` — async, programmatic API used by tests
  - `main()` — CLI wrapper (uv run python -m scripts.play_test)

The CLI defaults to 7 personas × 6 paired cells (3 settings × 2 seeds) = 42
games. The engine LLM uses whatever `LLM_*` settings point at
(`settings.llm_api_key`: LLM_API_KEY, else ANTHROPIC_API_KEY). The persona
side runs through the `claude` CLI (model pinned by `persona_model`, default
`sonnet`) and bills against the user's Claude Code subscription. Langfuse
tracing is off unless `--trace`. The CLI refuses a non-local DATABASE_URL.

A run the driver aborts (a turn with zero proposals) is archived with
`end_state: "ABORTED"` + `abort_reason` and listed under
`harness_findings.aborted_runs`, outside the corpus stats.

The meta-aggregator runs SEPARATELY via the /playtest-aggregate slash command
in the user's Claude Code session — see .claude/commands/playtest-aggregate.md.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import shutil
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select

import app.config
from app.database import async_session_factory
from app.eval.choice_spread import summarize_choice_spread
from app.eval.paired import resource_fraction
from app.eval.playtest.app_state import harness_app_state
from app.eval.playtest.llm_player import LLMPlayer
from app.eval.playtest.oracles import compute_corpus_oracles, compute_per_run_oracles
from app.eval.playtest.output import (
    PerRunArchive,
    _archive_basename,
    write_corpus_artifacts,
    write_one_archive,
)
from app.eval.playtest.persona_session import PersonaSessionError, preflight_claude_cli
from app.eval.playtest.personas import PERSONAS, get_persona
from app.eval.playtest.schemas import PersonaId
from app.eval.playtest.seeds import paired_cells
from app.eval.playtest.subprocess_pool import SubprocessPool
from app.eval.playtest.summaries import build_turn_context
from app.eval.playtest.tracing import tracing_disabled
from app.eval.types import RunRecord
from app.game.exceptions import GameNotEnded
from app.game.models import GameSession
from app.game.post_mortem import build_post_mortem
from app.game.schemas import ColonyState
from scripts._harness import (
    ABORTED,
    _cleanup_synthetic_user,
    _provision_synthetic_user,
    _uuid_for_setting,
    git_sha,
    next_free_dir,
    require_local_database,
    resolve_unfinished_end_state,
)

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
    # `claude --model` alias for every persona invocation.
    persona_model: str = "sonnet"
    # Langfuse tracing for engine calls. Off by default: a 42-game run would
    # burn a large share of the free 50K obs/month.
    trace: bool = False


def _allocate_runs(config: PlaytestConfig) -> list[tuple[PersonaId, str, int]]:
    """Paired (persona, setting, seed) allocation.

    Every persona plays the same `runs_per_persona` cells from
    `seeds.paired_cells`; seeds depend on (setting, run_idx) only. At the
    production size (6 runs, 3 settings) that is 2 seeds per setting and
    42 games across 7 personas.
    """
    cells = paired_cells(config.settings, config.runs_per_persona)
    return [(p, setting, seed) for p in config.personas for setting, seed in cells]


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
    happen to hash to). `seed` reaches the engine through
    `simulate_game_with_player` (seed-override dependency), so every
    persona on the same cell starts from the same colony; user_ids are
    still random.
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
            model=config.persona_model,
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

        if run_record.abort_reason is not None:
            # The driver stopped a run it could not continue (e.g. a turn
            # with no proposals). It is not a finished game: no end-state
            # resolution, no post-mortem, no verdict.
            logger.warning(
                "game %s aborted (persona=%s setting=%s seed=%s): %s",
                run_record.game_id,
                persona_id,
                setting_id,
                seed,
                run_record.abort_reason,
            )
            return PerRunArchive(
                run_id=str(run_record.game_id),
                persona_id=persona_id,
                setting_id=run_record.setting_id,
                seed=seed,
                end_state=ABORTED,
                abort_reason=run_record.abort_reason,
                turns_played=run_record.turns_played,
                per_turn_outputs=player.per_turn_outputs,
                verdict=None,
                run_record_summary={
                    "end_state": None,
                    "abort_reason": run_record.abort_reason,
                    "cost_cents": run_record.cost_cents,
                    "tool_calls": [tc.model_dump() for tc in run_record.tool_calls],
                    "narration_per_turn": run_record.narration_per_turn,
                    "choice_spread": run_record.choice_spread,
                },
                per_run_oracles={
                    "choice_spread_summary": summarize_choice_spread(run_record.choice_spread)
                },
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
            # before /decide fires for the terminal turn. Zero-proposal turns
            # never reach here: they return ABORTED above.
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
        per_run_oracles["choice_spread_summary"] = summarize_choice_spread(run_record.choice_spread)

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
                "choice_spread": run_record.choice_spread,
                "cost_cents_db": session_row.cost_cents,
            },
            per_run_oracles=per_run_oracles,
            turn_context=build_turn_context(
                turn_inputs=player.per_turn_inputs,
                outputs=player.per_turn_outputs,
                choice_spread=run_record.choice_spread,
            ),
            final_legitimacy=final_state.legitimacy,
            final_resource_frac=resource_fraction(final_state),
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
                if arc.abort_reason is not None:
                    # Listed and counted, kept out of the corpus stats.
                    pool.findings.aborted_runs.append(
                        {
                            "persona": persona_id,
                            "setting": setting_id,
                            "seed": seed,
                            "reason": arc.abort_reason,
                            "end_state": ABORTED,
                            "run_id": arc.run_id,
                            "turns_played": arc.turns_played,
                            "raw_json_path": f"raw/{_archive_basename(arc)}.json",
                        }
                    )
                else:
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

    with tracing_disabled(enabled=not config.trace):
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


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Run the LLM persona play-test harness.")
    p.add_argument("--out", default=None, help="Output dir; default docs/playtest/<date>[-N]")
    p.add_argument("--max-concurrency", type=int, default=5)
    p.add_argument(
        "--runs-per-persona",
        type=int,
        default=6,
        help="Paired cells each persona plays (interleaved over settings); default 6",
    )
    p.add_argument("--personas", default=None, help="Comma list; default all 7")
    p.add_argument("--max-turns", type=int, default=12)
    p.add_argument("--persona-model", default="sonnet", help="claude --model alias")
    p.add_argument("--trace", action="store_true", help="Enable Langfuse tracing (default off)")
    p.add_argument(
        "--allow-remote-db",
        action="store_true",
        help="Skip the local-DATABASE_URL guard (synthetic users get inserted!)",
    )
    return p


def main() -> None:  # pragma: no cover — CLI entry
    """CLI: defaults to 7 personas × 6 paired cells = 42 games."""
    args = build_parser().parse_args()
    require_local_database(app.config.settings.DATABASE_URL, allow_remote=args.allow_remote_db)
    if shutil.which("claude") is None:
        raise SystemExit("`claude` CLI not on PATH; the persona side needs it.")

    today = datetime.now(UTC).strftime("%Y-%m-%d")
    # Anchor to repo root so artifacts land in <repo>/docs/playtest/ regardless
    # of cwd. Script lives at <repo>/backend/scripts/play_test.py.
    repo_root = Path(__file__).resolve().parent.parent.parent
    out_dir = Path(args.out) if args.out else next_free_dir(repo_root / "docs" / "playtest", today)

    config = PlaytestConfig(
        out_dir=out_dir,
        max_concurrency=args.max_concurrency,
        runs_per_persona=args.runs_per_persona,
        max_turns=args.max_turns,
        persona_model=args.persona_model,
        trace=args.trace,
        git_sha=git_sha(),
    )
    if args.personas:
        config.personas = [p.strip() for p in args.personas.split(",") if p.strip()]  # type: ignore[misc]
        for pid in config.personas:
            get_persona(pid)  # fail fast on a typo

    try:
        preflight_claude_cli(shell_dir=config.shell_dir, model=config.persona_model)
    except PersonaSessionError as exc:
        raise SystemExit(f"`claude` CLI preflight failed: {exc}") from exc

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    asyncio.run(run_harness(config=config))


if __name__ == "__main__":  # pragma: no cover
    main()
