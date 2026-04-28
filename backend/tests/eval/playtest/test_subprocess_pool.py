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
