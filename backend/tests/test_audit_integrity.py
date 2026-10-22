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
from app.game.events import _derive_title
from app.game.generator import generate_run
from app.game.preview import compact_state
from app.game.schemas import Event, EventOrigin, ToolCall
from app.game.setting_loader import load_setting
from scripts.audit_integrity import (
    Tally,
    audit_turn,
    build_report,
    numbers_near_stats,
    tic_copy_needles,
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
    assert title_ok("Blizzard from the northeast", ARCTIC)  # authored
    assert title_ok("Solar particle event", ARCTIC)  # authored, another shipped setting
    assert title_ok("Aftermath of Blizzard from the northeast", ARCTIC)
    assert title_ok("Window frost", ARCTIC)  # respite template
    assert not title_ok("Invented title", ARCTIC)  # short, but not from any source
    assert not title_ok("Aftermath of Invented title", ARCTIC)
    assert not title_ok("", ARCTIC)
    assert not title_ok("Aftermath of ", ARCTIC)
    assert not title_ok("A coded message arrives addressed to the commissar by his ol", ARCTIC)
    assert not title_ok("An invented headline that trails off into nothing much at all…", ARCTIC)


def test_title_derived_from_prompt_template_passes() -> None:
    cats = [c.model_copy(update={"title": None}) for c in ARCTIC.event_categories]
    untitled = ARCTIC.model_copy(update={"event_categories": cats})
    derived = _derive_title(cats[0].prompt_template)
    assert title_ok(derived, untitled)
    assert not title_ok(derived, None) or derived in {c.title for c in ARCTIC.event_categories}


def test_missing_title_is_a_bad_title() -> None:
    row = _row()
    row.event = {k: v for k, v in row.event.items() if k != "title"}
    t = Tally()
    audit_turn(row, ARCTIC, [], t)
    assert t.titles_bad == [""]


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


def test_empty_audit_fails_the_gate() -> None:
    """No audited turns (bad --since, mistyped ids, pre-branch code) must not pass."""
    t = Tally()
    t.skipped = 5
    rows = build_report(t, dormant_failures=0, costs=[])
    status = {check: s for check, _value, _target, s in rows}
    assert status["Turns audited"] == "FAIL"


def test_skipped_turns_are_counted() -> None:
    t = Tally()
    audit_turn(_row(meta=False), ARCTIC, [], t)
    assert (t.turns, t.skipped) == (0, 1)


def _with_line(text: str, *, keyed: bool) -> tuple[SimpleNamespace, str]:
    """A clean row whose one advisor line is `text`, from a role whose tic has (or lacks) a quoted key phrase."""
    row = _row()
    wanted = {
        a.role_name
        for a in ARCTIC.advisor_archetypes
        if (tic_phrase(a.verbal_tic) is not None) == keyed
    }
    agent = next(a for a in row.turn_meta["pre_pick"]["agents"] if a["role"] in wanted)
    row.agent_responses = [{"agent_id": agent["id"], "text": text}]
    return row, agent["role"]


def _tic_of(role: str) -> str:
    return next(a.verbal_tic for a in ARCTIC.advisor_archetypes if a.role_name == role)


def test_literal_tic_copy_fails_the_gate() -> None:
    for keyed in (True, False):
        row, role = _with_line("x", keyed=keyed)
        needle = tic_copy_needles(_tic_of(role))[0]
        row.agent_responses[0]["text"] = f"I will say it plainly: {needle.capitalize()} today."
        t = Tally()
        audit_turn(row, ARCTIC, [], t)
        assert t.tic_copies == 1
        rows = build_report(t, dormant_failures=0, costs=[10])
        status = {check: s for check, _v, _t, s in rows}
        assert status["Literal tic text copied"] == "FAIL"


def test_plain_line_and_bare_key_phrase_are_not_copies() -> None:
    row, role = _with_line("Burn the stores. Now.", keyed=True)
    t = Tally()
    audit_turn(row, ARCTIC, [], t)
    assert t.tic_copies == 0
    phrase = tic_phrase(_tic_of(role))
    assert phrase
    row.agent_responses[0]["text"] = f"Agreed, {phrase}"
    audit_turn(row, ARCTIC, [], t)
    assert t.tic_copies == 0
    assert t.tic_hits[("arctic_station_v1", role)] == 1


def test_advisor_without_key_phrase_is_reported_not_dropped() -> None:
    t = Tally()
    row, role = _with_line("Burn the stores.", keyed=False)
    audit_turn(row, ARCTIC, [], t)
    rows = build_report(t, dormant_failures=0, costs=[10])
    values = {c: v for c, v, _t, _s in rows}
    assert values[f"Tic phrase share, arctic_station_v1 {role}"] == "no key phrase (1 lines)"


def test_cold_word_bleed_warns_at_baseline_and_never_fails() -> None:
    t = Tally()
    t.turns = 1
    t.cold_words["desert_outpost_v1"] = 23
    t.cold_words["orbital_colony_v1"] = 3
    rows = build_report(t, dormant_failures=0, costs=[10])
    by = {c: (v, tgt, s) for c, v, tgt, s in rows}
    assert by["Cold words in desert_outpost_v1 narration"] == (
        "23 (baseline 23)",
        "< baseline 23",
        "WARN",
    )
    assert by["Cold words in orbital_colony_v1 narration"][2] == "INFO"


def test_effects_preview_only_name_is_not_unknown() -> None:
    """Production allows names that appear in a surfaced proposal's effects_preview."""
    row = _row()
    row.narration = "You backed them. The stores sank near Zorblax."
    t = Tally()
    audit_turn(row, ARCTIC, [], t)
    assert t.stored_unknown == 1
    row.proposals[0]["effects_preview"] = [*row.proposals[0]["effects_preview"], "Zorblax"]
    t2 = Tally()
    audit_turn(row, ARCTIC, [], t2)
    assert t2.stored_unknown == 0


def test_production_recorded_unknown_names_are_counted() -> None:
    row = _row()
    row.turn_meta["narration"]["kept_violations"] = ["unknown_name:zorblax"]
    t = Tally()
    audit_turn(row, ARCTIC, [], t)
    assert t.prod_unknown == 1


def _role_with_tic(fragment: str) -> str:
    return next(a.role_name for a in ARCTIC.advisor_archetypes if fragment in a.verbal_tic)


def _copies_for_line(role: str, text: str) -> int:
    row = _row()
    agent = next(a for a in row.turn_meta["pre_pick"]["agents"] if a["role"] == role)
    row.agent_responses = [{"agent_id": agent["id"], "text": text}]
    t = Tally()
    audit_turn(row, ARCTIC, [], t)
    return t.tic_copies


def test_dash_terminated_quoted_tic_is_a_copy_in_straight_and_curly_quotes() -> None:
    role = _role_with_tic("I heard on the radio that")
    assert tic_copy_needles(_tic_of(role))
    assert _copies_for_line(role, 'Ah, "I heard on the radio that—" and so on.') == 1
    assert _copies_for_line(role, "Ah, “I heard on the radio that—” and so on.") == 1


def test_bare_tic_phrase_in_prose_is_not_a_copy() -> None:
    role = _role_with_tic("I heard on the radio that")
    assert _copies_for_line(role, "I heard on the radio that the pass is open.") == 0


def test_orbital_dash_terminated_tic_needle_matches_the_raw_quote() -> None:
    tic = next(
        a.verbal_tic
        for sid in ("orbital_colony_v1",)
        for a in load_setting(sid).advisor_archetypes
        if "probabilities suggest" in a.verbal_tic
    )
    assert '"the probabilities suggest—"' in tic_copy_needles(tic)


def test_cost_capped_narrations_are_counted_as_info() -> None:
    row = _row()
    row.turn_meta["narration"] = {"source": "cost_cap", "attempts": 0}
    t = Tally()
    audit_turn(row, ARCTIC, [], t)
    assert t.cost_capped == 1
    rows = build_report(t, dormant_failures=0, costs=[10])
    by = {c: (v, s) for c, v, _t, s in rows}
    assert by["Narrations skipped by the cost cap"] == ("1 turns", "INFO")


def _stored_hard_for(narration: str) -> int:
    row = _row()
    row.narration = narration
    t = Tally()
    audit_turn(row, ARCTIC, [], t)
    return t.stored_hard


def test_stored_narration_quoting_the_settings_foreign_word_is_clean() -> None:
    assert _stored_hard_for("The crate was stamped СЕКРЕТНО. Nobody opened it.") == 0


def test_stored_narration_with_an_invented_foreign_word_is_hard() -> None:
    assert _stored_hard_for("The stores burned in Коридор Б. Nobody slept.") == 1


def test_debate_retry_reasons_are_tallied_as_info() -> None:
    t = Tally()
    for reasons in (["options"], ["options", "reactions"], ["reactions"], [], None):
        row = _row()
        row.turn_meta["debate_retried"] = bool(reasons)
        if reasons is not None:
            row.turn_meta["debate_retry_reasons"] = reasons
        audit_turn(row, ARCTIC, [], t)
    assert (t.retried, t.retry_options, t.retry_reactions) == (3, 2, 2)
    by = {c: (v, s) for c, v, _t, s in build_report(t, dormant_failures=0, costs=[10])}
    assert by["Debate retries by reason (options / reactions)"] == ("2 / 2", "INFO")
