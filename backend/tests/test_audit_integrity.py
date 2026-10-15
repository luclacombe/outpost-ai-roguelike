"""Pure parts of the spec-1 exit-gate audit."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

from app.agent.prepare import prepare_proposals
from app.game import engine
from app.game.generator import generate_run
from app.game.preview import compact_state
from app.game.schemas import Event, EventOrigin, ToolCall
from app.game.setting_loader import load_setting
from scripts.audit_integrity import (
    Tally,
    audit_turn,
    build_report,
    numbers_near_stats,
    tic_phrase,
    title_ok,
)

ARCTIC = load_setting("arctic_station_v1")
USER = UUID("bbbbbbbb-0000-0000-0000-000000000002")
BACKEND = Path(__file__).resolve().parent.parent


def _row(tamper: bool = False, meta: bool = True) -> SimpleNamespace:
    colony = generate_run(user_id=USER, setting_id="arctic_station_v1", seed=7)
    call = ToolCall(
        name="propose_action",
        caller_id=colony.agents[0].id,
        args={
            "name": "Burn stores",
            "description": "Burn them.",
            "flavor": "DOMINANT",
            "tool_calls_on_pick": [
                {
                    "name": "modify_resource",
                    "args": {"resource_id": "fuel", "delta": -5, "reason": "r"},
                }
            ],
        },
    )
    prepared = prepare_proposals(colony, [call], ARCTIC)
    picked = prepared.proposals[0]
    derived = engine.derive_resolution_consequences(prepared.state, picked, prepared.proposals)
    after = engine.apply_tool_calls(
        prepared.state, engine.stamp_phase([*picked.tool_calls_on_pick, *derived], "resolve")
    )
    closed = engine.close_turn(after)
    proposals = [p.model_dump(mode="json") for p in prepared.proposals]
    if tamper:
        proposals[0]["effects_preview"] = ["fuel −50"]
    event = Event(
        id="e",
        category="weather_blizzard",
        title="Blizzard from the northeast",
        body="Snow.",
        severity=2,
        origin=EventOrigin.NATURAL,
    )
    return SimpleNamespace(
        game_id=colony.game_id,
        turn_number=0,
        event=event.model_dump(mode="json"),
        proposals=proposals,
        chosen={"chosen_proposal_id": picked.id},
        agent_responses=[],
        narration=f"You backed {colony.agents[0].name}. The stores burned down.",
        tool_calls=[tc.model_dump(mode="json") for tc in closed.turn_tool_calls],
        turn_meta=(
            {
                "debate_retried": False,
                "proposal_classes": prepared.classes,
                "risk_notes_dropped": 0,
                "pre_pick": compact_state(prepared.state),
                "narration": {
                    "source": "llm",
                    "attempts": 1,
                    "first_attempt_violations": [],
                    "kept_violations": [],
                },
            }
            if meta
            else None
        ),
    )


def test_clean_turn_passes() -> None:
    t = Tally()
    audit_turn(_row(), ARCTIC, [], t)
    assert (t.turns, t.preview_mismatch, t.picked_rejected, t.stored_hard) == (1, 0, 0, 0)
    assert t.titles_bad == []


def test_tampered_preview_is_a_mismatch() -> None:
    t = Tally()
    audit_turn(_row(tamper=True), ARCTIC, [], t)
    assert t.preview_mismatch == 1


def test_pre_branch_turn_is_skipped() -> None:
    t = Tally()
    audit_turn(_row(meta=False), ARCTIC, [], t)
    assert t.turns == 0


def test_paused_pre_branch_turn_with_narration_only_meta_is_skipped() -> None:
    """Games paused before the branch landed carry `{"narration": ...}` only."""
    row = _row()
    row.turn_meta = {"narration": {"source": "llm", "attempts": 1}}
    t = Tally()
    audit_turn(row, ARCTIC, [], t)
    assert t.turns == 0
    row.turn_meta = {}
    audit_turn(row, ARCTIC, [], t)
    assert t.turns == 0


def test_title_rules() -> None:
    assert title_ok("Blizzard from the northeast", ARCTIC)
    assert title_ok("Aftermath of Blizzard from the northeast", ARCTIC)
    assert title_ok("Window frost", ARCTIC)  # respite template
    assert title_ok("A coded message arrives addressed to the…", ARCTIC)
    assert not title_ok("A coded message arrives addressed to the commissar by his ol", ARCTIC)


def test_tic_phrase_and_numbers() -> None:
    assert tic_phrase('Sometimes closes a line with "yes?" when pushing.') == "yes?"
    assert tic_phrase("Reaches for clinical framing.") is None
    assert numbers_near_stats("Cut fuel −10 now", ARCTIC)
    assert numbers_near_stats("+5 legitimacy if it works", ARCTIC)
    assert not numbers_near_stats("Wait three days", ARCTIC)


def test_report_fails_on_mismatch() -> None:
    t = Tally()
    audit_turn(_row(tamper=True), ARCTIC, [], t)
    rows = build_report(t, dormant_failures=0, costs=[10, 12])
    status = {check: s for check, _value, _target, s in rows}
    assert status["Preview ≠ resolve render"] == "FAIL"
    assert status["Picked calls rejected at resolve"] == "PASS"


def test_cli_refuses_a_non_local_database() -> None:
    """The audit exits non-zero on a remote host and never opens a connection."""
    env = {
        **os.environ,
        "DATABASE_URL": "postgresql+psycopg://u:p@db.example.com:5432/x",
    }
    proc = subprocess.run(
        [sys.executable, "-m", "scripts.audit_integrity", "--since", "2026-10-01T00:00:00+00:00"],
        cwd=BACKEND,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode != 0
    assert "Refusing to run against non-local database host" in proc.stderr
