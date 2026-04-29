"""PersonaSession tests including the vertical-slice smoke
(one persona × one turn against fixture state)."""

from __future__ import annotations

import json
import subprocess
from typing import Any

import pytest

from app.eval.playtest.persona_session import (
    PersonaParseError,
    PersonaSession,
    _build_system_prompt,
    _format_turn_user_message,
    _parse_envelope_and_inner,
    _validate_quotes_against_dashboard,
)
from app.eval.playtest.personas import OPTIMIZER
from app.eval.playtest.schemas import PersonaTurnFlags, PersonaTurnOutput


def test_first_turn_invokes_claude_print_with_correct_flags(
    monkeypatch: pytest.MonkeyPatch,
    fixture_turn_input: dict[str, Any],
    canned_persona_response_factory,
    tmp_shell_dir,
) -> None:
    """Vertical-slice: one persona × one turn against a fixture state.

    Verifies the subprocess command, cwd, and output parsing.
    """
    captured: dict[str, Any] = {}
    canned = canned_persona_response_factory(
        chosen_proposal_id="p_dom",
        displayed_order=["p_mor", "p_dom", "p_wld"],  # shuffled order
    )

    def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
        captured["cmd"] = cmd
        captured["cwd"] = kwargs.get("cwd")
        captured["timeout"] = kwargs.get("timeout")
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout=canned, stderr="")

    monkeypatch.setattr("app.eval.playtest.persona_session.subprocess.run", fake_run)

    session = PersonaSession(
        persona=OPTIMIZER,
        shell_dir=tmp_shell_dir,
        effort="high",
        per_turn_timeout_seconds=300,
    )

    out = session.first_turn(
        turn_input=fixture_turn_input,
        displayed_order=["p_mor", "p_dom", "p_wld"],
    )

    # Output parsed correctly
    assert isinstance(out, PersonaTurnOutput)
    assert out.chosen_proposal_id == "p_dom"
    assert out.shown_position_of_chosen == 1

    # Command shape
    cmd = captured["cmd"]
    assert "claude" in cmd[0]
    assert "--print" in cmd
    assert "--output-format" in cmd and "json" in cmd
    assert "--effort" in cmd and "high" in cmd
    assert "--model" in cmd and "claude-sonnet-4-6" in cmd
    assert "--append-system-prompt" in cmd

    # System prompt content includes persona block
    sys_prompt_idx = cmd.index("--append-system-prompt") + 1
    sys_prompt = cmd[sys_prompt_idx]
    assert "Picking rule" in sys_prompt
    assert "Maximize" in sys_prompt or "resource_avg" in sys_prompt  # optimizer rule body

    # cwd + timeout
    assert captured["cwd"] == tmp_shell_dir
    assert captured["timeout"] == 300

    # Session ID captured for later --resume
    assert session.session_id == "test-session-abc123"


def test_subsequent_turn_uses_resume(
    monkeypatch: pytest.MonkeyPatch,
    fixture_turn_input: dict[str, Any],
    canned_persona_response_factory,
    tmp_shell_dir,
) -> None:
    captured: dict[str, Any] = {}
    canned = canned_persona_response_factory(
        chosen_proposal_id="p_mor", displayed_order=["p_dom", "p_mor", "p_wld"]
    )

    def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout=canned, stderr="")

    monkeypatch.setattr("app.eval.playtest.persona_session.subprocess.run", fake_run)

    session = PersonaSession(
        persona=OPTIMIZER,
        shell_dir=tmp_shell_dir,
    )
    session.session_id = "preset-session-id-from-prior-turn"

    out = session.subsequent_turn(
        turn_input={**fixture_turn_input, "turn": 2},
        displayed_order=["p_dom", "p_mor", "p_wld"],
    )

    assert out.chosen_proposal_id == "p_mor"
    cmd = captured["cmd"]
    assert "--resume" in cmd
    resume_idx = cmd.index("--resume") + 1
    assert cmd[resume_idx] == "preset-session-id-from-prior-turn"
    # No --append-system-prompt on resume — system prompt is preserved across
    # the session by the server. Confirm this is the case:
    assert "--append-system-prompt" not in cmd


def test_subsequent_turn_without_session_id_raises(
    fixture_turn_input: dict[str, Any], tmp_shell_dir
) -> None:
    session = PersonaSession(persona=OPTIMIZER, shell_dir=tmp_shell_dir)
    assert session.session_id is None
    with pytest.raises(RuntimeError, match="no session_id"):
        session.subsequent_turn(turn_input=fixture_turn_input, displayed_order=["a", "b", "c"])


def test_final_verdict_parses_run_verdict(monkeypatch: pytest.MonkeyPatch, tmp_shell_dir) -> None:
    """End-of-run verdict — persona reads post-mortem and emits 5-axis verdict."""
    verdict_inner = {
        "axes": {
            "stakes_felt_real": {"score": 4, "rationale": "...", "turn_citations": [4, 7]},
            "tonal_coherence": {"score": 5, "rationale": "...", "turn_citations": []},
            "advisor_distinctness": {"score": 4, "rationale": "...", "turn_citations": [3]},
            "dramatic_arc": {"score": 4, "rationale": "...", "turn_citations": [11]},
        },
        "continue_tomorrow": {"decision": True, "reason": "dissident path landed"},
        "closing_paragraph": "...",
    }
    envelope_json = json.dumps(
        {
            "type": "result",
            "session_id": "preset-session-id",
            "result": "```json\n" + json.dumps(verdict_inner) + "\n```",
        }
    )

    monkeypatch.setattr(
        "app.eval.playtest.persona_session.subprocess.run",
        lambda cmd, **kw: subprocess.CompletedProcess(
            args=cmd, returncode=0, stdout=envelope_json, stderr=""
        ),
    )

    session = PersonaSession(persona=OPTIMIZER, shell_dir=tmp_shell_dir)
    session.session_id = "preset-session-id"

    verdict = session.final_verdict(post_mortem_json={"end_state": "ENDURED"})
    assert verdict.axes.stakes_felt_real.score == 4
    assert verdict.continue_tomorrow.decision is True


def test_first_turn_drops_fabricated_silence_via_validator(
    monkeypatch: pytest.MonkeyPatch,
    fixture_turn_input: dict[str, Any],
    tmp_shell_dir,
) -> None:
    """Integration: feed PersonaSession.first_turn a subprocess response that
    fabricates `(silent)` quotes against advisors whose reactions are
    rendered. Verify the validator is wired in and produces a cleaned output.

    Distinct from `test_validator_*` (which tests the function in isolation).
    This test catches the case where the validator function is correct but
    the wiring at the end of `first_turn` is missing or broken — a regression
    suspected in the v3 smoke."""
    fabricated_inner = {
        "turn": 1,
        "displayed_order": ["p_mor", "p_dom", "p_wld"],
        "chosen_proposal_id": "p_dom",
        "shown_position_of_chosen": 1,
        "reaction": "picked DOMINANT.",
        "flags": {
            "surprise": True,
            "tone_break": False,
            "advisor_oc": False,
            "stakes_low": False,
        },
        "quotes": {
            "surprise": "Marko (Commissar): (silent) / Nadia (Quartermaster): (silent)",
        },
    }
    envelope = json.dumps(
        {
            "session_id": "test-fabricate",
            "result": "```json\n" + json.dumps(fabricated_inner) + "\n```",
        }
    )

    def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout=envelope, stderr="")

    monkeypatch.setattr("app.eval.playtest.persona_session.subprocess.run", fake_run)
    session = PersonaSession(persona=OPTIMIZER, shell_dir=tmp_shell_dir)
    out = session.first_turn(
        turn_input=fixture_turn_input,
        displayed_order=["p_mor", "p_dom", "p_wld"],
    )
    assert out.flags.surprise is False, (
        "Validator wiring broken: surprise flag should have been downgraded. "
        f"Actual flags: {out.flags.model_dump()}"
    )
    assert "surprise" not in out.quotes, f"Quote should be dropped. Got: {out.quotes}"


def test_subsequent_turn_drops_fabricated_silence_via_validator(
    monkeypatch: pytest.MonkeyPatch,
    fixture_turn_input: dict[str, Any],
    tmp_shell_dir,
) -> None:
    """All v3 smoke silences fired on Turns 2-3 (subsequent_turn path), so
    that wiring is the load-bearing one to verify."""
    fabricated_inner = {
        "turn": 2,
        "displayed_order": ["p_dom", "p_mor", "p_wld"],
        "chosen_proposal_id": "p_mor",
        "shown_position_of_chosen": 1,
        "reaction": "picked MORAL.",
        "flags": {
            "surprise": True,
            "tone_break": False,
            "advisor_oc": False,
            "stakes_low": False,
        },
        "quotes": {
            "surprise": "Marko (Commissar): (silent) / Nadia (Quartermaster): (silent)",
        },
    }
    envelope = json.dumps(
        {
            "session_id": "test-fabricate-2",
            "result": "```json\n" + json.dumps(fabricated_inner) + "\n```",
        }
    )

    def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout=envelope, stderr="")

    monkeypatch.setattr("app.eval.playtest.persona_session.subprocess.run", fake_run)
    session = PersonaSession(persona=OPTIMIZER, shell_dir=tmp_shell_dir)
    session.session_id = "preset-id"
    out = session.subsequent_turn(
        turn_input={**fixture_turn_input, "turn": 2},
        displayed_order=["p_dom", "p_mor", "p_wld"],
    )
    assert out.flags.surprise is False, (
        "subsequent_turn validator wiring broken: surprise flag should have been "
        f"downgraded. Actual: {out.flags.model_dump()}"
    )
    assert "surprise" not in out.quotes


def test_user_message_renders_reactions_before_proposals(
    fixture_turn_input: dict[str, Any],
) -> None:
    """Reactions must precede proposals so the persona reads them while
    context is fresh — the prior order (proposals then reactions) caused
    Sonnet personas to skim past the reactions section, generating false
    `(silent)` allegations against advisors whose reactions WERE rendered.
    """
    msg = _format_turn_user_message(
        turn_input=fixture_turn_input,
        displayed_order=["p_mor", "p_dom", "p_wld"],
    )
    reactions_pos = msg.index("## Advisor reactions")
    proposals_pos = msg.index("## Proposals")
    assert reactions_pos < proposals_pos, (
        "Reactions section must appear before Proposals section. "
        f"reactions at {reactions_pos}, proposals at {proposals_pos}"
    )


def test_user_message_renders_silent_only_when_reaction_missing(
    fixture_turn_input: dict[str, Any],
) -> None:
    """The dashboard fallback `(silent)` must render only for advisors with
    no `advisor_reactions` entry. With all four reactions present, the
    rendered message should contain zero `(silent)` markers."""
    msg = _format_turn_user_message(
        turn_input=fixture_turn_input,
        displayed_order=["p_mor", "p_dom", "p_wld"],
    )
    assert "(silent)" not in msg, (
        f"With every advisor reacting, the message should not render '(silent)'. "
        f"Sample: {msg[msg.find('## Advisor reactions') :][:400]}"
    )


def _make_output(
    *,
    flags: dict[str, bool] | None = None,
    quotes: dict[str, str] | None = None,
) -> PersonaTurnOutput:
    """Build a minimal PersonaTurnOutput for validator testing."""
    base_flags = {"surprise": False, "tone_break": False, "advisor_oc": False, "stakes_low": False}
    if flags:
        base_flags.update(flags)
    return PersonaTurnOutput(
        turn=1,
        displayed_order=["a", "b", "c"],
        chosen_proposal_id="a",
        shown_position_of_chosen=0,
        reaction="picked the first option",
        flags=PersonaTurnFlags(**base_flags),
        quotes=quotes or {},
    )


def test_validator_drops_fabricated_silent_quote() -> None:
    """When dashboard renders zero `(silent)` markers but a quote claims
    `(silent)`, drop the quote and downgrade the flag to false."""
    out = _make_output(
        flags={"advisor_oc": True},
        quotes={"advisor_oc": "Marko (Commissar): (silent) — out of character"},
    )
    user_msg = "## Advisor reactions\n- **Marko** (Commissar): a real reaction line.\n"
    cleaned = _validate_quotes_against_dashboard(out, user_msg)
    assert cleaned.flags.advisor_oc is False
    assert "advisor_oc" not in cleaned.quotes


def test_validator_keeps_quote_when_dashboard_renders_silence() -> None:
    """Conservative: if the dashboard DID render `(silent)`, we accept the
    quote — we can't tell which silences are real vs fabricated."""
    out = _make_output(
        flags={"advisor_oc": True},
        quotes={"advisor_oc": "Marko (Commissar): (silent)"},
    )
    user_msg = "## Advisor reactions\n- **Marko** (Commissar): (silent)\n"
    cleaned = _validate_quotes_against_dashboard(out, user_msg)
    assert cleaned.flags.advisor_oc is True
    assert cleaned.quotes["advisor_oc"] == out.quotes["advisor_oc"]


def test_validator_passthrough_when_no_silent_quotes() -> None:
    """No `(silent)` substring anywhere → no-op."""
    out = _make_output(
        flags={"surprise": True, "stakes_low": True},
        quotes={
            "surprise": "Three vehicles at the gate.",
            "stakes_low": "A quiet turn. Nothing demands a decision.",
        },
    )
    user_msg = "## Advisor reactions\n- **Marko** (Commissar): some line.\n"
    cleaned = _validate_quotes_against_dashboard(out, user_msg)
    assert cleaned.flags.model_dump() == out.flags.model_dump()
    assert cleaned.quotes == out.quotes


def test_validator_drops_only_fabricated_flags_not_legitimate_ones() -> None:
    """Mixed input: one flag has fabricated `(silent)` quote, one doesn't.
    Drop only the fabricated one; leave the legitimate flag and quote intact."""
    out = _make_output(
        flags={"surprise": True, "advisor_oc": True},
        quotes={
            "surprise": "All four advisors silent: (silent)/(silent)/(silent)/(silent)",
            "advisor_oc": "Marko advocates mercy yet authors the harsh WILDCARD.",
        },
    )
    user_msg = "## Advisor reactions\n- **Marko** (Commissar): real reaction.\n"
    cleaned = _validate_quotes_against_dashboard(out, user_msg)
    assert cleaned.flags.surprise is False  # downgraded — quote was fabricated
    assert cleaned.flags.advisor_oc is True  # kept — quote had no `(silent)`
    assert "surprise" not in cleaned.quotes
    assert cleaned.quotes["advisor_oc"] == out.quotes["advisor_oc"]


def test_system_prompt_requires_verbatim_reaction_quote() -> None:
    """The system prompt must include a contractual clause directing the
    persona to (a) read the `## Advisor reactions` section before flagging
    `advisor_oc`, and (b) cite a verbatim excerpt from it.

    Without this, Sonnet 4.6 skims the section and fabricates `(silent)`
    quotes for advisors whose reactions are rendered. See investigation
    notes 2026-04-29: 24/24 alleged silences across the smoke run were
    false positives.
    """
    prompt = _build_system_prompt(OPTIMIZER)
    # The exact phrasing can evolve; the load-bearing pieces are the
    # section name (so the persona knows where to look) and the verbatim
    # requirement (so the persona can't fabricate quotes).
    assert "## Advisor reactions" in prompt
    assert "verbatim" in prompt.lower()


@pytest.mark.parametrize(
    "envelope_json,expects_raise",
    [
        # Baseline: fenced JSON inside .result
        (
            json.dumps({"session_id": "s", "result": '```json\n{"a": 1}\n```'}),
            False,
        ),
        # Raw JSON, no fence
        (
            json.dumps({"session_id": "s", "result": '{"a": 1}'}),
            False,
        ),
        # Fence with no language tag
        (
            json.dumps({"session_id": "s", "result": '```\n{"a": 1}\n```'}),
            False,
        ),
        # Trailing prose after closing fence (rfind picks up the closing fence)
        (
            json.dumps({"session_id": "s", "result": '```json\n{"a": 1}\n```\nDone!'}),
            False,
        ),
        # Prose BEFORE fence (broken — preamble fed to json.loads)
        (
            json.dumps({"session_id": "s", "result": 'Here:\n```json\n{"a": 1}\n```'}),
            True,
        ),
        # Fence opened but never closed
        (
            json.dumps({"session_id": "s", "result": '```json\n{"a": 1}'}),
            True,
        ),
        # Empty result
        (json.dumps({"session_id": "s", "result": ""}), True),
        # Envelope itself not JSON
        ("not-valid-json", True),
        # Envelope missing session_id
        (json.dumps({"result": '{"a": 1}'}), True),
        # result not a string
        (json.dumps({"session_id": "s", "result": 42}), True),
        # Inner JSON is not an object (e.g. a list)
        (json.dumps({"session_id": "s", "result": "[1,2,3]"}), True),
    ],
)
def test_parse_envelope_handles_fence_variants(envelope_json: str, expects_raise: bool) -> None:
    if expects_raise:
        with pytest.raises(PersonaParseError):
            _parse_envelope_and_inner(envelope_json)
    else:
        session_id, inner = _parse_envelope_and_inner(envelope_json)
        assert session_id == "s"
        assert isinstance(inner, dict)
