"""LLMPlayer drop-in tests."""

from __future__ import annotations

import subprocess
from typing import Any

import pytest

from app.eval.playtest.llm_player import LLMPlayer
from app.eval.playtest.personas import OPTIMIZER
from app.eval.playtest.subprocess_pool import SubprocessPool


def test_player_pick_returns_id_in_displayed_order(
    monkeypatch: pytest.MonkeyPatch,
    fixture_turn_input: dict[str, Any],
    canned_persona_response_factory,
    tmp_shell_dir,
) -> None:
    """LLMPlayer.pick(proposals) shuffles, calls pool, returns chosen id.

    Verifies (a) chosen id is one of the input proposals, (b) the persona's
    output was recorded for later RunRecord assembly.
    """

    def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
        # Find displayed_order in the prompt to mirror it back
        # (the canned envelope's inner JSON must match the actual displayed_order).
        user_msg = cmd[-1]
        line = next(line for line in user_msg.splitlines() if "displayed_order" in line)
        import ast

        bracket_start = line.find("[")
        bracket_end = line.rfind("]") + 1
        order = ast.literal_eval(line[bracket_start:bracket_end])
        canned = canned_persona_response_factory(chosen_proposal_id=order[0], displayed_order=order)
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout=canned, stderr="")

    monkeypatch.setattr("app.eval.playtest.persona_session.subprocess.run", fake_run)

    pool = SubprocessPool(max_concurrency=1)
    player = LLMPlayer(
        persona=OPTIMIZER,
        pool=pool,
        shell_dir=tmp_shell_dir,
        seed=0,
        turn_input_provider=lambda: fixture_turn_input,
    )

    proposals = fixture_turn_input["proposals"]
    chosen_id = player.pick(proposals)
    assert chosen_id in {p["id"] for p in proposals}
    assert len(player.per_turn_outputs) == 1
    rec = player.per_turn_outputs[0]
    assert rec.chosen_proposal_id == chosen_id


def test_player_seed_makes_shuffle_deterministic(
    fixture_turn_input: dict[str, Any], tmp_shell_dir
) -> None:
    """Two LLMPlayer instances constructed with the same seed produce the
    same `displayed_order` for the same proposals input."""

    def make_player() -> LLMPlayer:
        return LLMPlayer(
            persona=OPTIMIZER,
            pool=SubprocessPool(max_concurrency=1),
            shell_dir=tmp_shell_dir,
            seed=42,
            turn_input_provider=lambda: fixture_turn_input,
        )

    p1 = make_player()
    p2 = make_player()
    proposals = fixture_turn_input["proposals"]
    o1 = p1._displayed_order_for(proposals)
    o2 = p2._displayed_order_for(proposals)
    assert o1 == o2  # same seed → same shuffle
