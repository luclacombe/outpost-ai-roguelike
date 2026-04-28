"""Oracle-wrapping tests."""

from __future__ import annotations

from uuid import UUID

import numpy as np
import pytest

from app.eval.playtest.oracles import (
    compute_corpus_oracles,
    compute_per_run_oracles,
)
from app.eval.types import RunRecord
from app.game.schemas import (
    Agent,
    EndState,
    Quadrant,
)


def _fake_agent(agent_id: str = "00000000-0000-0000-0000-000000000001") -> Agent:
    return Agent(
        id=UUID(agent_id),
        game_id=UUID("00000000-0000-0000-0000-000000000099"),
        name="TestAgent",
        role="Tester",
        archetype_quadrant=Quadrant.ACCOUNTANT,
        ethics=-0.5,
        temperament=0.5,
        loyalty=0.0,
        portrait_seed="seed",
        health=100,
        morale=80,
        grievance=2,
        is_alive=True,
    )


def _fake_run(seed: int = 1, setting_id: str = "arctic_station_v1") -> RunRecord:
    return RunRecord(
        setting_id=setting_id,
        policy="moral",
        seed=seed,
        end_state=EndState.ENDURED,
        turns_played=10,
        final_player_health=80,
        agents_alive_at_end=4,
        total_agents=4,
        cost_cents=20,
        tool_calls=[],
        agent_responses_per_agent={},
        resource_history={"fuel": [80, 70, 65, 60, 55]},
        forced_choice_turns=0,
        narration_per_turn=[
            f"Turn {i}: ice creaks under the heating bay; nothing yet" for i in range(10)
        ],
    )


def test_per_run_oracles_returns_three_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    """Per-run oracles compute tool_validity, repetition, personality_consistency."""

    class _FakeEncoder:
        def encode(self, texts, **_kwargs):  # noqa: ANN001
            return np.array([[1.0, 0.0, 0.0] for _ in texts], dtype=np.float32)

    monkeypatch.setattr("app.eval.personality._get_model", lambda: _FakeEncoder())

    run = _fake_run()
    out = compute_per_run_oracles(run, agents=[_fake_agent()])
    assert "tool_validity_rate" in out
    assert "repetition_bleu2" in out
    assert "personality_consistency" in out
    assert isinstance(out["tool_validity_rate"], float)
    assert isinstance(out["repetition_bleu2"], float)
    assert isinstance(out["personality_consistency"], dict)


def test_corpus_oracles_groups_inter_run_by_setting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Corpus oracles return per-setting inter_run_similarity (since the
    underlying metric requires single-setting input) plus end-state stats."""

    class _FakeEncoder:
        def encode(self, texts, **_kwargs):  # noqa: ANN001
            return np.array([[1.0, 0.0, 0.0] for _ in texts], dtype=np.float32)

    monkeypatch.setattr("app.eval.personality._get_model", lambda: _FakeEncoder())

    runs = [
        _fake_run(seed=1, setting_id="arctic_station_v1"),
        _fake_run(seed=2, setting_id="arctic_station_v1"),
        _fake_run(seed=3, setting_id="orbital_station_v1"),
        _fake_run(seed=4, setting_id="orbital_station_v1"),
    ]
    out = compute_corpus_oracles(runs)

    assert "inter_run_similarity_by_setting" in out
    assert "end_state_distribution" in out
    assert "spec_target_match" in out
    assert isinstance(out["spec_target_match"], bool)

    # Per-setting inter-run sim
    by_setting = out["inter_run_similarity_by_setting"]
    assert "arctic_station_v1" in by_setting
    assert "orbital_station_v1" in by_setting
    # Each entry has the mean cosine
    for _setting_id, info in by_setting.items():
        assert "mean_pairwise_cosine" in info
        assert "verdict" in info


def test_corpus_oracles_handles_single_setting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Single-setting corpus still works."""

    class _FakeEncoder:
        def encode(self, texts, **_kwargs):  # noqa: ANN001
            return np.array([[1.0, 0.0, 0.0] for _ in texts], dtype=np.float32)

    monkeypatch.setattr("app.eval.personality._get_model", lambda: _FakeEncoder())

    runs = [_fake_run(seed=i, setting_id="arctic_station_v1") for i in range(3)]
    out = compute_corpus_oracles(runs)
    assert "arctic_station_v1" in out["inter_run_similarity_by_setting"]
