"""Tests for SubprocessPool's retry / degrade rules per spec §10."""

from __future__ import annotations

import subprocess
import threading
from typing import Any

import pytest

from app.eval.playtest.persona_session import PersonaSession
from app.eval.playtest.personas import OPTIMIZER
from app.eval.playtest.subprocess_pool import (
    PoolPickResult,
    SubprocessPool,
)


def test_malformed_json_retries_once_then_falls_back(
    monkeypatch: pytest.MonkeyPatch,
    fixture_turn_input: dict[str, Any],
    canned_persona_response_factory,
    tmp_shell_dir,
) -> None:
    """First attempt: malformed envelope → retry with strict reminder.
    Second attempt: still malformed → fallback pick (first in displayed_order).
    Both events recorded in harness_findings.malformed_json_count."""
    call_count = {"n": 0}

    def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
        call_count["n"] += 1
        return subprocess.CompletedProcess(
            args=cmd, returncode=0, stdout="not-valid-json", stderr=""
        )

    monkeypatch.setattr("app.eval.playtest.persona_session.subprocess.run", fake_run)

    pool = SubprocessPool(max_concurrency=1)
    session = PersonaSession(persona=OPTIMIZER, shell_dir=tmp_shell_dir)

    result = pool.run_first_turn(
        session=session,
        turn_input=fixture_turn_input,
        displayed_order=["p_mor", "p_dom", "p_wld"],
    )

    assert call_count["n"] == 2  # one + one retry
    assert isinstance(result, PoolPickResult)
    assert result.fallback_used is True
    # Fallback rule: first proposal in displayed_order
    assert result.chosen_proposal_id == "p_mor"
    # Recorded as harness finding
    assert pool.findings.malformed_json_count == 2


def test_subprocess_timeout_recorded_as_hang(
    monkeypatch: pytest.MonkeyPatch,
    fixture_turn_input: dict[str, Any],
    tmp_shell_dir,
) -> None:
    """`subprocess.TimeoutExpired` → PersonaSession raises PersonaTimeout;
    pool counts each as `subprocess_hangs`. Two attempts → two increments,
    then fallback."""

    def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=kwargs.get("timeout", 300))

    monkeypatch.setattr("app.eval.playtest.persona_session.subprocess.run", fake_run)

    pool = SubprocessPool(max_concurrency=1)
    session = PersonaSession(
        persona=OPTIMIZER, shell_dir=tmp_shell_dir, per_turn_timeout_seconds=10
    )

    result = pool.run_first_turn(
        session=session,
        turn_input=fixture_turn_input,
        displayed_order=["p_mor", "p_dom", "p_wld"],
    )

    assert result.fallback_used is True
    assert pool.findings.subprocess_hangs == 2  # both attempts hung
    assert pool.findings.malformed_json_count == 0  # not a parse error


def test_subprocess_crash_recorded_as_crash(
    monkeypatch: pytest.MonkeyPatch,
    fixture_turn_input: dict[str, Any],
    tmp_shell_dir,
) -> None:
    """Non-zero exit → PersonaSession raises PersonaSubprocessError; pool
    counts each as `subprocess_crashes`."""

    def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
        return subprocess.CompletedProcess(args=cmd, returncode=137, stdout="", stderr="OOM")

    monkeypatch.setattr("app.eval.playtest.persona_session.subprocess.run", fake_run)

    pool = SubprocessPool(max_concurrency=1)
    session = PersonaSession(persona=OPTIMIZER, shell_dir=tmp_shell_dir)

    result = pool.run_first_turn(
        session=session,
        turn_input=fixture_turn_input,
        displayed_order=["p_mor", "p_dom", "p_wld"],
    )

    assert result.fallback_used is True
    assert pool.findings.subprocess_crashes == 2
    assert pool.findings.subprocess_hangs == 0
    assert pool.findings.malformed_json_count == 0


def test_concurrent_first_turns_share_semaphore(
    monkeypatch: pytest.MonkeyPatch,
    fixture_turn_input: dict[str, Any],
    canned_persona_response_factory,
    tmp_shell_dir,
) -> None:
    """SubprocessPool with max_concurrency=2 admits at most 2 simultaneous
    sessions."""
    import time

    in_flight = {"n": 0, "max": 0}
    lock = threading.Lock()

    canned = canned_persona_response_factory(
        chosen_proposal_id="p_dom", displayed_order=["p_mor", "p_dom", "p_wld"]
    )

    def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
        with lock:
            in_flight["n"] += 1
            in_flight["max"] = max(in_flight["max"], in_flight["n"])
        time.sleep(0.05)
        with lock:
            in_flight["n"] -= 1
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout=canned, stderr="")

    monkeypatch.setattr("app.eval.playtest.persona_session.subprocess.run", fake_run)

    pool = SubprocessPool(max_concurrency=2)
    threads: list[threading.Thread] = []
    for _ in range(5):
        session = PersonaSession(persona=OPTIMIZER, shell_dir=tmp_shell_dir)

        def go(s: PersonaSession = session) -> None:
            pool.run_first_turn(
                session=s,
                turn_input=fixture_turn_input,
                displayed_order=["p_mor", "p_dom", "p_wld"],
            )

        t = threading.Thread(target=go)
        t.start()
        threads.append(t)
    for t in threads:
        t.join()

    assert in_flight["max"] <= 2


def test_invalid_pick_recorded_only_as_invalid_pick(
    monkeypatch: pytest.MonkeyPatch,
    fixture_turn_input: dict[str, Any],
    tmp_shell_dir,
) -> None:
    """When a persona's chosen_proposal_id passes Pydantic validation (it's
    in the persona's *own* displayed_order) but doesn't match the pool's
    input displayed_order, count as invalid_pick — NOT also as malformed.
    Spec §10 treats these as mutually exclusive failure rows."""
    from app.eval.playtest.schemas import PersonaTurnFlags, PersonaTurnOutput

    # Build a "successful" persona output where chosen_proposal_id is valid
    # internally (in the persona's displayed_order) but won't be in the
    # pool's displayed_order parameter.
    rogue_output = PersonaTurnOutput(
        turn=1,
        displayed_order=["x_alpha", "x_beta", "x_gamma"],
        chosen_proposal_id="x_alpha",
        shown_position_of_chosen=0,
        reaction="picked first",
        flags=PersonaTurnFlags(
            surprise=False, tone_break=False, advisor_oc=False, stakes_low=False
        ),
        quotes={},
    )

    # Patch session.first_turn to return the rogue output (bypassing
    # subprocess entirely — simulates a persona that happened to ignore
    # our displayed_order instruction).
    session = PersonaSession(persona=OPTIMIZER, shell_dir=tmp_shell_dir)
    monkeypatch.setattr(session, "first_turn", lambda **_kwargs: rogue_output)

    pool = SubprocessPool(max_concurrency=1)
    result = pool.run_first_turn(
        session=session,
        turn_input=fixture_turn_input,
        displayed_order=["p_mor", "p_dom", "p_wld"],
    )

    assert result.fallback_used is True
    assert pool.findings.invalid_pick_count == 2  # both attempts produced rogue picks
    assert pool.findings.by_persona[session.persona.id]["invalid_pick"] == 2
    # Critical: must NOT be counted as malformed (the bug C1)
    assert pool.findings.malformed_json_count == 0
    assert pool.findings.by_persona[session.persona.id]["malformed"] == 0


@pytest.mark.parametrize(
    "exception_class,counter_name,counter_target,expected_error_substring",
    [
        ("PersonaTimeout", "subprocess_hangs", 2, "timeout"),
        ("PersonaSubprocessError", "subprocess_crashes", 2, "subprocess"),
        ("PersonaParseError", "malformed_json_count", 2, "parse"),
    ],
)
def test_run_final_verdict_failure_modes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_shell_dir,
    exception_class: str,
    counter_name: str,
    counter_target: int,
    expected_error_substring: str,
) -> None:
    """Each typed exception triggered by final_verdict increments the
    matching counter; second failure surfaces a `VerdictResult` with
    `verdict=None` AND a non-empty `error` describing the failure mode
    so the orchestrator can mark the run as `verdict_failed` (sub-bug iii).
    """
    from app.eval.playtest.persona_session import (
        PersonaParseError,
        PersonaSubprocessError,
        PersonaTimeout,
    )

    exc_map = {
        "PersonaTimeout": PersonaTimeout,
        "PersonaSubprocessError": PersonaSubprocessError,
        "PersonaParseError": PersonaParseError,
    }
    exc_cls = exc_map[exception_class]

    session = PersonaSession(persona=OPTIMIZER, shell_dir=tmp_shell_dir)
    session.session_id = "preset"

    def raise_exc(**_kwargs: Any) -> Any:
        raise exc_cls("simulated failure")

    monkeypatch.setattr(session, "final_verdict", raise_exc)

    pool = SubprocessPool(max_concurrency=1)
    result = pool.run_final_verdict(session=session, post_mortem_json={"end_state": "ENDURED"})

    # New: result is a VerdictResult, not a bare PersonaRunVerdict | None.
    assert result.verdict is None
    assert result.error is not None
    assert expected_error_substring in result.error.lower()
    assert getattr(pool.findings, counter_name) == counter_target


def test_run_final_verdict_success_returns_verdict_with_no_error(
    monkeypatch: pytest.MonkeyPatch, tmp_shell_dir
) -> None:
    """Happy path: VerdictResult.verdict carries the parsed verdict and
    error is None. Preserves callers' "did the verdict succeed?" check."""
    from tests.eval.playtest._verdicts import make_verdict

    fake_verdict = make_verdict()

    session = PersonaSession(persona=OPTIMIZER, shell_dir=tmp_shell_dir)
    session.session_id = "preset"
    monkeypatch.setattr(session, "final_verdict", lambda **_kw: fake_verdict)

    pool = SubprocessPool(max_concurrency=1)
    result = pool.run_final_verdict(session=session, post_mortem_json={"end_state": "ENDURED"})

    assert result.verdict is fake_verdict
    assert result.error is None
