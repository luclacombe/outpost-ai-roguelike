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
    _parse_envelope_and_inner,
)
from app.eval.playtest.personas import OPTIMIZER
from app.eval.playtest.schemas import PersonaTurnOutput


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
