"""Scripted balance sweep: SimulatedPlayer policies × stable seeds × settings.

    uv run python -m scripts.sweep [--seeds 10] [--concurrency 5] [--max-turns 12]

Cheap counterpart to the persona harness (scripts/play_test.py): no
`claude` CLI, only the engine LLM. Every policy (`moral`, `pragmatic`,
`random`) plays the same (setting, seed) cells (`app/eval/playtest/seeds.py`),
so per-cell outcome differences are the "do decisions matter" signal.
Default 3 policies × 10 seeds × 3 settings = 90 games.

Output: `docs/playtest/<YYYY-MM-DD>-sweep[-N]/` with `summary.json`,
`summary.md` (`app/eval/sweep_summary.py`) and `raw/<policy>-<setting>-seed<n>.json`
per game, written as each game finishes. Each game provisions its own
synthetic user (daily gate). Langfuse tracing is off unless `--trace`; a
non-local DATABASE_URL is refused unless `--allow-remote-db`.

Lives in `scripts/` because it drives games through
`tests/fixtures/run_simulator.py` (app code can't import test fixtures).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select

import app.config
from app.database import async_session_factory
from app.eval.choice_spread import summarize_choice_spread
from app.eval.paired import resource_fraction
from app.eval.playtest.app_state import harness_app_state
from app.eval.playtest.seeds import paired_cells
from app.eval.playtest.tracing import tracing_disabled
from app.eval.sweep_summary import build_sweep_summary, render_sweep_markdown
from app.eval.types import RunRecord
from app.game.models import GameSession
from app.game.schemas import ColonyState, EndState
from scripts._harness import (
    _cleanup_synthetic_user,
    _provision_synthetic_user,
    _uuid_for_setting,
    git_sha,
    next_free_dir,
    require_local_database,
    resolve_unfinished_end_state,
)

logger = logging.getLogger(__name__)

POLICIES = ("moral", "pragmatic", "random")
SETTINGS = ("arctic_station_v1", "orbital_colony_v1", "desert_outpost_v1")


@dataclass
class SweepConfig:
    policies: list[str] = field(default_factory=lambda: list(POLICIES))
    seeds: int = 10  # per setting
    settings: list[str] = field(default_factory=lambda: list(SETTINGS))
    max_turns: int = 12
    concurrency: int = 5
    out_dir: Path = field(default_factory=lambda: Path("docs/playtest/sweep"))
    trace: bool = False
    cleanup_after_run: bool = False
    git_sha: str = "unknown"


def allocate_sweep(config: SweepConfig) -> list[tuple[str, str, int]]:
    """(policy, setting, seed) triples; every policy plays every cell."""
    cells = paired_cells(config.settings, config.seeds * len(config.settings))
    return [(policy, setting, seed) for setting, seed in cells for policy in config.policies]


def game_record(
    record: RunRecord,
    *,
    colony: ColonyState,
    requested_setting: str,
    cost_cents: int,
    end_state: EndState,
    resolved_by_harness: bool,
) -> dict[str, Any]:
    """One finished game as a JSON-ready dict (`raw/*.json` row)."""
    return {
        "policy": record.policy,
        "setting": record.setting_id,
        "requested_setting": requested_setting,
        "seed": record.seed,
        "game_id": str(record.game_id),
        "end_state": end_state.value,
        "end_state_resolved_by_harness": resolved_by_harness,
        "turns_played": record.turns_played,
        "max_turns": colony.max_turns,
        "final_legitimacy": colony.legitimacy,
        "final_player_health": colony.player_health,
        "final_resource_frac": resource_fraction(colony),
        "final_resources": {rid: r.value for rid, r in colony.resources.items()},
        "agents_alive_at_end": record.agents_alive_at_end,
        "cost_cents": cost_cents,
        "tool_calls_total": len(record.tool_calls),
        "tool_calls_rejected": sum(1 for tc in record.tool_calls if tc.rejection_reason),
        "choice_spread_summary": summarize_choice_spread(record.choice_spread),
        "choice_spread": record.choice_spread,
    }


async def _drive_one_sweep_game(
    *, policy: str, setting: str, seed: int, config: SweepConfig
) -> dict[str, Any]:
    """Provision a synthetic user pinned to `setting`, play one game, read it back."""
    from tests.fixtures.run_simulator import simulate_game  # noqa: PLC0415

    user_id = _uuid_for_setting(setting, datetime.now(UTC))
    await _provision_synthetic_user(user_id, f"sweep-{user_id}@outpost.local")
    try:
        record = await simulate_game(
            setting_id=setting,
            policy=policy,  # type: ignore[arg-type]
            seed=seed,
            max_turns=config.max_turns,
            user_id=user_id,
        )
        async with async_session_factory() as s:
            row = (
                await s.execute(select(GameSession).where(GameSession.id == record.game_id))
            ).scalar_one()
        colony = ColonyState.model_validate(row.state)
        end_state = resolve_unfinished_end_state(colony)
        if colony.end_state is None:
            logger.warning(
                "sweep game %s (%s/%s/%s) exited with end_state=None; resolved to %s",
                row.id,
                policy,
                setting,
                seed,
                end_state.value,
            )
        return game_record(
            record,
            colony=colony,
            requested_setting=setting,
            cost_cents=row.cost_cents,
            end_state=end_state,
            resolved_by_harness=colony.end_state is None,
        )
    finally:
        if config.cleanup_after_run:
            await _cleanup_synthetic_user(user_id)


async def run_sweep(config: SweepConfig) -> dict[str, Any]:
    """Drive every allocation with bounded concurrency; write raw + summary."""
    allocations = allocate_sweep(config)
    raw_dir = config.out_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    games: list[dict[str, Any]] = []
    aborted: list[dict[str, Any]] = []
    sema = asyncio.Semaphore(config.concurrency)
    started = time.monotonic()

    async def go(policy: str, setting: str, seed: int) -> None:
        async with sema:
            try:
                row = await _drive_one_sweep_game(
                    policy=policy, setting=setting, seed=seed, config=config
                )
            except Exception as exc:  # noqa: BLE001 — log, record, keep sweeping
                logger.exception("sweep game %s/%s/%s failed", policy, setting, seed)
                aborted.append(
                    {
                        "policy": policy,
                        "setting": setting,
                        "seed": seed,
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )
                return
            # Persist per game so an interrupted sweep still leaves data.
            name = f"{policy}-{row['setting']}-seed{seed}.json"
            (raw_dir / name).write_text(json.dumps(row, indent=2, default=str))
            games.append(row)
            logger.info("sweep %d/%d done", len(games) + len(aborted), len(allocations))

    with tracing_disabled(enabled=not config.trace):
        async with harness_app_state():
            await asyncio.gather(*(go(p, s, sd) for p, s, sd in allocations))

    meta = {
        **{k: v for k, v in asdict(config).items() if k != "out_dir"},
        "out_dir": str(config.out_dir),
        "n_allocated": len(allocations),
        "wall_clock_seconds": int(time.monotonic() - started),
    }
    summary = build_sweep_summary(games, aborted=aborted, meta=meta)
    (config.out_dir / "summary.json").write_text(json.dumps(summary, indent=2, default=str))
    (config.out_dir / "summary.md").write_text(render_sweep_markdown(summary))
    return summary


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Scripted balance sweep (no persona LLM).")
    p.add_argument("--seeds", type=int, default=10, help="Seeds per setting (default 10)")
    p.add_argument("--max-turns", type=int, default=12)
    p.add_argument("--concurrency", type=int, default=5)
    p.add_argument("--out", default=None, help="Output dir; default docs/playtest/<date>-sweep[-N]")
    p.add_argument("--policies", default=None, help=f"Comma list; default {','.join(POLICIES)}")
    p.add_argument("--settings", default=None, help="Comma list; default all three")
    p.add_argument("--trace", action="store_true", help="Enable Langfuse tracing (default off)")
    p.add_argument("--cleanup", action="store_true", help="Delete synthetic users afterwards")
    p.add_argument(
        "--allow-remote-db",
        action="store_true",
        help="Skip the local-DATABASE_URL guard (synthetic users get inserted!)",
    )
    return p


def _csv(value: str | None, default: tuple[str, ...]) -> list[str]:
    return [v.strip() for v in value.split(",") if v.strip()] if value else list(default)


def main() -> None:  # pragma: no cover — CLI entry
    args = build_parser().parse_args()
    require_local_database(app.config.settings.DATABASE_URL, allow_remote=args.allow_remote_db)
    policies = _csv(args.policies, POLICIES)
    unknown = sorted(set(policies) - set(POLICIES))
    if unknown:
        raise SystemExit(f"unknown policies {unknown}; expected a subset of {list(POLICIES)}")

    today = datetime.now(UTC).strftime("%Y-%m-%d")
    repo_root = Path(__file__).resolve().parent.parent.parent
    out_dir = (
        Path(args.out)
        if args.out
        else next_free_dir(repo_root / "docs" / "playtest", f"{today}-sweep")
    )
    config = SweepConfig(
        policies=policies,
        seeds=args.seeds,
        settings=_csv(args.settings, SETTINGS),
        max_turns=args.max_turns,
        concurrency=args.concurrency,
        out_dir=out_dir,
        trace=args.trace,
        cleanup_after_run=args.cleanup,
        git_sha=git_sha(),
    )
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    summary = asyncio.run(run_sweep(config))
    print(f"sweep: {summary['n_games']} games, {summary['n_aborted']} aborted → {out_dir}")


if __name__ == "__main__":  # pragma: no cover
    main()
