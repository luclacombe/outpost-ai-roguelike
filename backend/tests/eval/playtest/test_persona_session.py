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
    PersonaSubprocessError,
    _build_system_prompt,
    _format_turn_user_message,
    _parse_envelope_and_inner,
    _validate_quotes_against_dashboard,
    preflight_claude_cli,
)
from app.eval.playtest.personas import OPTIMIZER
from app.eval.playtest.schemas import PersonaTurnFlags, PersonaTurnOutput
from tests.eval.playtest._verdicts import verdict_dict


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
    assert cmd[cmd.index("--model") + 1] == "sonnet"  # pinned alias, config default
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
    # --model applies to "the current session" only, so resume re-pins it.
    assert cmd[cmd.index("--model") + 1] == "sonnet"
    assert cmd[cmd.index("--effort") + 1] == "high"


def test_persona_model_is_configurable(
    monkeypatch: pytest.MonkeyPatch,
    fixture_turn_input: dict[str, Any],
    canned_persona_response_factory,
    tmp_shell_dir,
) -> None:
    cmds: list[list[str]] = []
    canned = canned_persona_response_factory(
        chosen_proposal_id="p_mor", displayed_order=["p_dom", "p_mor", "p_wld"]
    )

    def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
        cmds.append(cmd)
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout=canned, stderr="")

    monkeypatch.setattr("app.eval.playtest.persona_session.subprocess.run", fake_run)
    session = PersonaSession(persona=OPTIMIZER, shell_dir=tmp_shell_dir, model="opus")
    order = ["p_dom", "p_mor", "p_wld"]
    session.first_turn(turn_input=fixture_turn_input, displayed_order=order)
    session.subsequent_turn(turn_input={**fixture_turn_input, "turn": 2}, displayed_order=order)
    assert [c[c.index("--model") + 1] for c in cmds] == ["opus", "opus"]


def test_subsequent_turn_without_session_id_raises(
    fixture_turn_input: dict[str, Any], tmp_shell_dir
) -> None:
    session = PersonaSession(persona=OPTIMIZER, shell_dir=tmp_shell_dir)
    assert session.session_id is None
    with pytest.raises(RuntimeError, match="no session_id"):
        session.subsequent_turn(turn_input=fixture_turn_input, displayed_order=["a", "b", "c"])


def test_final_verdict_parses_run_verdict(monkeypatch: pytest.MonkeyPatch, tmp_shell_dir) -> None:
    """End-of-run verdict — persona reads post-mortem and emits 5-axis verdict."""
    verdict_inner = verdict_dict(score=4, difficulty=2, bugs=["Day counter starts at 00."])
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
    assert verdict.axes.meaningful_choices.score == 4
    assert verdict.difficulty.score == 2
    assert verdict.bugs_noticed == ["Day counter starts at 00."]
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


def test_system_prompt_includes_three_id_worked_example() -> None:
    """The system prompt must show a worked example with exactly THREE distinct
    proposal ids in `displayed_order`, not the abstract `[<id1>, <id2>, <id3>]`
    placeholder. Without a positive example, Sonnet 4.6 sometimes emits a
    one-element list and the strict 3-id validator aborts the run (sub-bug i).
    Caught: 3/42 schema aborts in the 2026-04-30 playtest run.
    """
    prompt = _build_system_prompt(OPTIMIZER)
    # The worked example must show three distinct ids — using a marker phrase
    # the prompt-builder owns so this test catches placeholder regression.
    assert "Worked example" in prompt or "EXAMPLE" in prompt or "example shape" in prompt, (
        "Prompt missing a positive worked example for displayed_order — "
        "Sonnet falls back to 1-id output without it."
    )
    # The example must contain three concrete ids; we count proposal-id-shaped
    # tokens in the example block by looking for three distinct quoted strings
    # inside the same `displayed_order` literal.
    # The marker substring "p_dom" / "p_mor" / "p_wld" is the convention used
    # by the harness's own fixtures, so adopt it for the worked example.
    assert "p_dom" in prompt and "p_mor" in prompt and "p_wld" in prompt, (
        "Worked example must show three concrete proposal ids "
        "(p_dom / p_mor / p_wld), not abstract placeholders."
    )


def test_first_turn_reprompts_on_displayed_order_count_mismatch(
    monkeypatch: pytest.MonkeyPatch,
    fixture_turn_input: dict[str, Any],
    canned_persona_response_factory,
    tmp_shell_dir,
) -> None:
    """Sub-bug (i) tolerance path: first attempt returns 1 id, second attempt
    (after re-prompt with the actual displayed_order echoed in the user
    message) returns the correct 3-id list. Persona output should parse
    cleanly without falling through to the synthetic-fallback path.
    """
    expected_order = ["p_mor", "p_dom", "p_wld"]
    bad_inner = {
        "turn": 1,
        "displayed_order": ["p_dom"],  # only 1 id — the bug
        "chosen_proposal_id": "p_dom",
        "shown_position_of_chosen": 0,
        "reaction": "...",
        "flags": {
            "surprise": False,
            "tone_break": False,
            "advisor_oc": False,
            "stakes_low": False,
        },
        "quotes": {},
    }
    bad_envelope = json.dumps(
        {
            "session_id": "test-reprompt-1",
            "result": "```json\n" + json.dumps(bad_inner) + "\n```",
        }
    )
    good_envelope = canned_persona_response_factory(
        chosen_proposal_id="p_dom", displayed_order=expected_order
    )

    call_log: list[list[str]] = []
    responses = [bad_envelope, good_envelope]

    def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
        call_log.append(cmd)
        stdout = responses[len(call_log) - 1]
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout=stdout, stderr="")

    monkeypatch.setattr("app.eval.playtest.persona_session.subprocess.run", fake_run)

    session = PersonaSession(persona=OPTIMIZER, shell_dir=tmp_shell_dir)
    out = session.first_turn(turn_input=fixture_turn_input, displayed_order=expected_order)

    # Two subprocess calls: original + one re-prompt.
    assert len(call_log) == 2, f"expected 2 subprocess calls, got {len(call_log)}: {call_log}"
    # Final parsed output is the good one.
    assert out.displayed_order == expected_order
    # The second call must mention the actual displayed_order in its user
    # message — that's the load-bearing nudge that fixes Sonnet's 1-id drift.
    second_user_msg = call_log[1][-1]
    for pid in expected_order:
        assert pid in second_user_msg, (
            f"re-prompt user message must echo {pid!r} verbatim; got: {second_user_msg[:300]}"
        )


def test_subsequent_turn_reprompts_on_displayed_order_count_mismatch(
    monkeypatch: pytest.MonkeyPatch,
    fixture_turn_input: dict[str, Any],
    canned_persona_response_factory,
    tmp_shell_dir,
) -> None:
    """Same tolerance path on the resume call site — ensures both turn
    paths are protected. (subsequent_turn is what fires for turns 2-N,
    which is most of every game.)"""
    expected_order = ["p_dom", "p_mor", "p_wld"]
    bad_inner = {
        "turn": 2,
        "displayed_order": ["p_mor"],
        "chosen_proposal_id": "p_mor",
        "shown_position_of_chosen": 0,
        "reaction": "...",
        "flags": {
            "surprise": False,
            "tone_break": False,
            "advisor_oc": False,
            "stakes_low": False,
        },
        "quotes": {},
    }
    bad_envelope = json.dumps(
        {
            "session_id": "test-reprompt-2",
            "result": "```json\n" + json.dumps(bad_inner) + "\n```",
        }
    )
    good_envelope = canned_persona_response_factory(
        chosen_proposal_id="p_mor", displayed_order=expected_order
    )

    call_log: list[list[str]] = []
    responses = [bad_envelope, good_envelope]

    def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
        call_log.append(cmd)
        stdout = responses[len(call_log) - 1]
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout=stdout, stderr="")

    monkeypatch.setattr("app.eval.playtest.persona_session.subprocess.run", fake_run)

    session = PersonaSession(persona=OPTIMIZER, shell_dir=tmp_shell_dir)
    session.session_id = "preset-id-from-prior-turn"

    out = session.subsequent_turn(
        turn_input={**fixture_turn_input, "turn": 2}, displayed_order=expected_order
    )

    assert len(call_log) == 2
    assert out.displayed_order == expected_order


def test_first_turn_reprompt_gives_up_after_one_retry(
    monkeypatch: pytest.MonkeyPatch,
    fixture_turn_input: dict[str, Any],
    tmp_shell_dir,
) -> None:
    """If both attempts return a 1-id list, raise PersonaParseError so the
    pool's existing retry/fallback machinery handles it. The single
    re-prompt is the load-bearing first line of defense; the pool's
    fallback is the second."""
    bad_inner = {
        "turn": 1,
        "displayed_order": ["p_dom"],
        "chosen_proposal_id": "p_dom",
        "shown_position_of_chosen": 0,
        "reaction": "...",
        "flags": {
            "surprise": False,
            "tone_break": False,
            "advisor_oc": False,
            "stakes_low": False,
        },
        "quotes": {},
    }
    bad_envelope = json.dumps(
        {
            "session_id": "test-reprompt-fail",
            "result": "```json\n" + json.dumps(bad_inner) + "\n```",
        }
    )

    monkeypatch.setattr(
        "app.eval.playtest.persona_session.subprocess.run",
        lambda cmd, **_kw: subprocess.CompletedProcess(
            args=cmd, returncode=0, stdout=bad_envelope, stderr=""
        ),
    )

    session = PersonaSession(persona=OPTIMIZER, shell_dir=tmp_shell_dir)
    with pytest.raises(PersonaParseError):
        session.first_turn(
            turn_input=fixture_turn_input, displayed_order=["p_mor", "p_dom", "p_wld"]
        )


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


# ---------------------------------------------------------------------------
# UI-parity rendering (turn_view.py → _format_turn_user_message)
# ---------------------------------------------------------------------------

_ORDER = ["p_mor", "p_dom", "p_wld"]


def test_user_message_renders_header_and_dashboard(fixture_turn_input: dict[str, Any]) -> None:
    msg = _format_turn_user_message(turn_input=fixture_turn_input, displayed_order=_ORDER)
    assert "Day 00 / 9" in msg
    assert "Vorgrad Station" in msg
    assert "Legitimacy: 50 (warning)" in msg
    assert "Fuel 80/100" in msg
    assert "Food 18/100 (low)" in msg
    assert "Medicine 8/100 (critical)" in msg
    assert "Nadia (Quartermaster): STEWING" in msg
    assert "infrastructure · serious" in msg


def test_user_message_hides_what_the_ui_hides(fixture_turn_input: dict[str, Any]) -> None:
    msg = _format_turn_user_message(turn_input=fixture_turn_input, displayed_order=_ORDER)
    assert "health" not in msg.lower()
    assert "grievance" not in msg.lower()
    assert "critical at" not in msg.lower()


def test_user_message_renders_effects_preview(fixture_turn_input: dict[str, Any]) -> None:
    msg = _format_turn_user_message(turn_input=fixture_turn_input, displayed_order=_ORDER)
    proposals = msg[msg.index("## Proposals") :]
    assert "Effects: +12 fuel; -5 morale" in proposals
    assert "Effects: (none shown)" in proposals


def test_user_message_first_turn_has_no_last_turn(fixture_turn_input: dict[str, Any]) -> None:
    msg = _format_turn_user_message(turn_input=fixture_turn_input, displayed_order=_ORDER)
    assert "## Last turn" not in msg


def test_user_message_renders_last_turn_outcome(fixture_turn_input_t2: dict[str, Any]) -> None:
    msg = _format_turn_user_message(turn_input=fixture_turn_input_t2, displayed_order=_ORDER)
    last = msg[msg.index("## Last turn") : msg.index("## Event")]
    assert "You chose: **Cut perimeter heating** (DOMINANT)" in last
    assert "Effects shown: +12 fuel; -5 morale" in last
    assert "The perimeter went dark." in last
    assert "Legitimacy -4" in last
    assert "Fuel +12" in last and "Morale -5" in last
    assert "Deaths: Yuri" in last
    assert "Yuri: TIRED → KIA" in last
    # Section order: dashboard, log, last turn, event, reactions, proposals.
    order = [
        "## Dashboard",
        "## Log",
        "## Last turn",
        "## Event",
        "## Advisor reactions",
        "## Proposals",
    ]
    positions = [msg.index(h) for h in order]
    assert positions == sorted(positions)


def test_user_message_dead_advisors_are_not_silent(fixture_turn_input_t2: dict[str, Any]) -> None:
    """KIA advisors appear in the roster, not in reactions, so no `(silent)`."""
    msg = _format_turn_user_message(turn_input=fixture_turn_input_t2, displayed_order=_ORDER)
    assert "Yuri (Radio Operator): KIA" in msg
    assert "(silent)" not in msg
    assert "CONSEQUENCE" in msg


_AUTH_FAIL_ENVELOPE = json.dumps(
    {
        "type": "result",
        "is_error": True,
        "session_id": "dead-session",
        "result": "Failed to authenticate: OAuth session expired and could not be refreshed",
    }
)


def test_nonzero_exit_surfaces_envelope_result(
    monkeypatch: pytest.MonkeyPatch, fixture_turn_input: dict[str, Any], tmp_shell_dir
) -> None:
    """`claude --print` reports auth/API failures in the stdout envelope with an
    empty stderr; the error must carry that text, not just the exit code."""

    def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
        return subprocess.CompletedProcess(
            args=cmd, returncode=1, stdout=_AUTH_FAIL_ENVELOPE, stderr=""
        )

    monkeypatch.setattr("app.eval.playtest.persona_session.subprocess.run", fake_run)
    session = PersonaSession(persona=OPTIMIZER, shell_dir=tmp_shell_dir)
    with pytest.raises(PersonaSubprocessError, match="OAuth session expired"):
        session.first_turn(
            turn_input=fixture_turn_input, displayed_order=["p_mor", "p_dom", "p_wld"]
        )


def test_preflight_raises_with_cli_error(monkeypatch: pytest.MonkeyPatch, tmp_shell_dir) -> None:
    def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
        return subprocess.CompletedProcess(
            args=cmd, returncode=1, stdout=_AUTH_FAIL_ENVELOPE, stderr=""
        )

    monkeypatch.setattr("app.eval.playtest.persona_session.subprocess.run", fake_run)
    with pytest.raises(PersonaSubprocessError, match="OAuth session expired"):
        preflight_claude_cli(shell_dir=tmp_shell_dir, model="sonnet")


def test_preflight_passes_and_pins_model(monkeypatch: pytest.MonkeyPatch, tmp_shell_dir) -> None:
    seen: dict[str, list[str]] = {}

    def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
        seen["cmd"] = cmd
        ok = {"type": "result", "is_error": False, "session_id": "s", "result": "ok"}
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout=json.dumps(ok), stderr="")

    monkeypatch.setattr("app.eval.playtest.persona_session.subprocess.run", fake_run)
    preflight_claude_cli(shell_dir=tmp_shell_dir, model="opus")
    assert seen["cmd"][seen["cmd"].index("--model") + 1] == "opus"
