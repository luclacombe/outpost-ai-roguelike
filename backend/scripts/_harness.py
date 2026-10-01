"""Shared plumbing for the in-process harness CLIs (play_test, sweep).

Synthetic-user provisioning, setting pinning, defensive end-state
resolution, the local-DB guard and dated output dirs. Both CLIs insert
rows into `auth.users`; the guard keeps them off the remote project.
"""

from __future__ import annotations

import os
import subprocess
import uuid
from datetime import datetime
from pathlib import Path
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.engine import make_url

from app.database import async_session_factory
from app.game.engine import compute_end_state
from app.game.schemas import ColonyState, EndState
from app.game.service import _pick_setting_id
from app.game.setting_loader import list_available_settings

LOCAL_DB_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "host.docker.internal"})


def require_local_database(url: str, *, allow_remote: bool = False) -> None:
    """Exit unless DATABASE_URL points at a local Postgres.

    `.env` usually points at the remote Supabase project; the harness must
    run against `supabase start` (localhost:54322) via a DATABASE_URL
    override. `allow_remote` is the explicit escape hatch.
    """
    host = make_url(url).host or ""
    if host in LOCAL_DB_HOSTS or allow_remote:
        return
    raise SystemExit(
        f"Refusing to run against non-local database host {host!r}. Start local "
        "Supabase and prefix the command with DATABASE_URL="
        '"postgresql+psycopg://postgres:postgres@localhost:54322/postgres" '
        "(or pass --allow-remote-db if you really mean it)."
    )


def git_sha() -> str:
    """Short HEAD sha for the run header; OUTPOST_GIT_SHA wins if set."""
    env = os.environ.get("OUTPOST_GIT_SHA")
    if env:
        return env
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
            cwd=Path(__file__).resolve().parent,
        )
    except (OSError, subprocess.CalledProcessError):
        return "unknown"
    return out.stdout.strip() or "unknown"


def next_free_dir(base: Path, name: str) -> Path:
    """`base/name`, or `base/name-2`, `-3`, ... if it already exists."""
    candidate = base / name
    n = 2
    while candidate.exists():
        candidate = base / f"{name}-{n}"
        n += 1
    return candidate


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
