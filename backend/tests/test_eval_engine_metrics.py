"""Tests for engine-only ($0 LLM) eval metrics in `app.eval`.

Covers four pure metrics:
- `tool_validity` — fraction of valid ToolCalls + breakdown of rejection reasons.
- `repetition` — pairwise BLEU-2 similarity over agent responses.
- `balance` — aggregated survival/resource/forced-choice/cost stats.
- `end_state` — observed end-state distribution vs target band.

All tests build Pydantic objects directly; no DB, no YAML loader, no LLM.
"""

from __future__ import annotations

import statistics
from uuid import UUID, uuid4

import pytest

from app.eval.balance import GameBalanceMetrics, game_balance_metrics
from app.eval.end_state import (
    END_STATE_TARGETS,
    end_state_distribution_check,
)
from app.eval.repetition import (
    REPETITION_THRESHOLD,
    narrative_repetition_index,
    repetitive_pairs,
)
from app.eval.tool_validity import (
    TOOL_VALIDITY_THRESHOLD,
    tool_validity_breakdown,
    tool_validity_rate,
)
from app.eval.types import RunRecord
from app.game.schemas import EndState, ToolCall

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _tc(rejection_reason: str | None = None, name: str = "adjust_resource") -> ToolCall:
    return ToolCall(
        name=name,
        caller_id=None,
        args={"resource_id": "morale", "delta": -1},
        rejection_reason=rejection_reason,
    )


def _make_run(
    *,
    end_state: EndState | None = EndState.ENDURED,
    turns_played: int = 10,
    final_player_health: int = 80,
    agents_alive_at_end: int = 4,
    total_agents: int = 4,
    cost_cents: int = 12,
    tool_calls: list[ToolCall] | None = None,
    resource_history: dict[str, list[int]] | None = None,
    forced_choice_turns: int = 0,
    setting_id: str = "wartime-bunker",
    policy: str = "moral",
    seed: int = 1,
    agent_responses_per_agent: dict[UUID, list[str]] | None = None,
    narration_per_turn: list[str] | None = None,
) -> RunRecord:
    return RunRecord(
        setting_id=setting_id,
        policy=policy,
        seed=seed,
        end_state=end_state,
        turns_played=turns_played,
        final_player_health=final_player_health,
        agents_alive_at_end=agents_alive_at_end,
        total_agents=total_agents,
        cost_cents=cost_cents,
        tool_calls=tool_calls or [],
        agent_responses_per_agent=agent_responses_per_agent or {},
        resource_history=resource_history or {},
        forced_choice_turns=forced_choice_turns,
        narration_per_turn=narration_per_turn or [],
    )


# ---------------------------------------------------------------------------
# Tool validity
# ---------------------------------------------------------------------------


class TestToolValidity:
    def test_threshold_constant_present(self) -> None:
        assert TOOL_VALIDITY_THRESHOLD == 0.85

    def test_tool_validity_all_valid(self) -> None:
        calls = [_tc() for _ in range(5)]
        assert tool_validity_rate(calls) == 1.0
        assert tool_validity_breakdown(calls) == {}

    def test_tool_validity_mixed(self) -> None:
        calls = [
            _tc(),
            _tc(),
            _tc(),
            _tc(rejection_reason="resource_below_zero"),
            _tc(rejection_reason="caller_not_alive"),
        ]
        assert tool_validity_rate(calls) == pytest.approx(0.6)
        breakdown = tool_validity_breakdown(calls)
        assert breakdown == {"resource_below_zero": 1, "caller_not_alive": 1}

    def test_tool_validity_empty(self) -> None:
        assert tool_validity_rate([]) == 1.0
        assert tool_validity_breakdown([]) == {}

    def test_tool_validity_breakdown_groups_by_reason(self) -> None:
        calls = [
            _tc(rejection_reason="resource_below_zero"),
            _tc(rejection_reason="resource_below_zero"),
            _tc(rejection_reason="resource_below_zero"),
            _tc(rejection_reason="caller_not_alive"),
            _tc(),
        ]
        breakdown = tool_validity_breakdown(calls)
        assert breakdown == {"resource_below_zero": 3, "caller_not_alive": 1}


# ---------------------------------------------------------------------------
# Repetition
# ---------------------------------------------------------------------------


class TestRepetition:
    def test_repetition_identical_strings_high(self) -> None:
        responses = ["the colony is starving and morale is low"] * 3
        score = narrative_repetition_index(responses)
        assert score == pytest.approx(1.0, abs=1e-6)
        assert score > REPETITION_THRESHOLD

    def test_repetition_distinct_strings_low(self) -> None:
        responses = [
            "the quartermaster counts every grain twice",
            "engines howl beneath cracked deck plating",
            "children whisper rumors about the radio tower",
        ]
        score = narrative_repetition_index(responses)
        assert score < REPETITION_THRESHOLD
        assert score < 0.1

    def test_repetition_empty(self) -> None:
        assert narrative_repetition_index([]) == 0.0

    def test_repetition_single_string(self) -> None:
        assert narrative_repetition_index(["only one response here"]) == 0.0

    def test_repetitive_pairs_returns_indices_above_threshold(self) -> None:
        responses = [
            "the colony is starving and morale is low",
            "the colony is starving and morale is low",
            "engines howl beneath cracked deck plating",
        ]
        pairs = repetitive_pairs(responses)
        assert len(pairs) == 1
        i, j, score = pairs[0]
        assert (i, j) == (0, 1)
        assert score > REPETITION_THRESHOLD


# ---------------------------------------------------------------------------
# Balance
# ---------------------------------------------------------------------------


class TestBalance:
    def test_game_balance_survival_rate_aggregates_across_runs(self) -> None:
        runs = [
            _make_run(final_player_health=80, agents_alive_at_end=4, total_agents=4),
            _make_run(final_player_health=0, agents_alive_at_end=2, total_agents=4),
            _make_run(final_player_health=10, agents_alive_at_end=3, total_agents=4),
        ]
        m = game_balance_metrics(runs)
        # 2 of 3 runs have player_health > 0
        assert m.survival_rate == pytest.approx(2 / 3)
        # mean fraction alive: (1.0 + 0.5 + 0.75) / 3
        assert m.agent_survival_rate == pytest.approx((1.0 + 0.5 + 0.75) / 3)

    def test_game_balance_resource_volatility_uses_per_resource_stddev(self) -> None:
        # Two runs, two resources. Compute per-run stddev, mean across runs.
        # Run 1: food = [10,10,10] -> std=0; water = [0,5,10] -> std≈4.0825 (population)
        # Run 2: food = [4,6,8]    -> std≈1.6330; water = [2,2,2] -> std=0
        runs = [
            _make_run(resource_history={"food": [10, 10, 10], "water": [0, 5, 10]}),
            _make_run(resource_history={"food": [4, 6, 8], "water": [2, 2, 2]}),
        ]
        m = game_balance_metrics(runs)

        food_mean = statistics.mean([statistics.pstdev([10, 10, 10]), statistics.pstdev([4, 6, 8])])
        water_mean = statistics.mean([statistics.pstdev([0, 5, 10]), statistics.pstdev([2, 2, 2])])
        assert m.resource_volatility["food"] == pytest.approx(food_mean)
        assert m.resource_volatility["water"] == pytest.approx(water_mean)

    def test_game_balance_forced_choice_rate_means_across_runs(self) -> None:
        runs = [
            _make_run(turns_played=10, forced_choice_turns=2),
            _make_run(turns_played=8, forced_choice_turns=4),
        ]
        m = game_balance_metrics(runs)
        # Mean of (2/10, 4/8) = mean(0.2, 0.5) = 0.35
        assert m.forced_choice_rate == pytest.approx(0.35)

    def test_game_balance_handles_empty_run_list(self) -> None:
        m = game_balance_metrics([])
        assert isinstance(m, GameBalanceMetrics)
        assert m.survival_rate == 0.0
        assert m.agent_survival_rate == 0.0
        assert m.resource_volatility == {}
        assert m.forced_choice_rate == 0.0
        assert m.mean_turns_played == 0.0
        assert m.mean_cost_cents == 0.0


# ---------------------------------------------------------------------------
# End-state distribution
# ---------------------------------------------------------------------------


class TestEndStateDistribution:
    def test_end_state_distribution_in_band(self) -> None:
        # Build 50 runs with target frequencies (40% endured, 35% collapsed,
        # 13% ousted, 12% killed) — all within ±10 pp of target bands.
        runs: list[RunRecord] = []
        runs.extend(_make_run(end_state=EndState.ENDURED) for _ in range(20))
        runs.extend(_make_run(end_state=EndState.COLLAPSED) for _ in range(17))
        runs.extend(_make_run(end_state=EndState.OUSTED) for _ in range(7))
        runs.extend(_make_run(end_state=EndState.KILLED) for _ in range(6))
        assert len(runs) == 50
        result = end_state_distribution_check(runs)
        assert result.n_runs == 50
        assert result.out_of_band == []
        # Sum of observed frequencies is 1.0
        assert sum(result.observed.values()) == pytest.approx(1.0)
        # Targets are exposed unchanged
        assert result.targets == END_STATE_TARGETS

    def test_end_state_distribution_out_of_band(self) -> None:
        # 80% COLLAPSED — far outside target [0.30, 0.40] even with ±0.10 tol.
        runs = [_make_run(end_state=EndState.COLLAPSED) for _ in range(40)]
        runs += [_make_run(end_state=EndState.ENDURED) for _ in range(10)]
        result = end_state_distribution_check(runs)
        assert EndState.COLLAPSED in result.out_of_band

    def test_end_state_distribution_skips_active_runs(self) -> None:
        # 4 finished runs + 6 active (end_state=None). Denominator should be 4.
        runs: list[RunRecord] = [
            _make_run(end_state=EndState.ENDURED),
            _make_run(end_state=EndState.ENDURED),
            _make_run(end_state=EndState.COLLAPSED),
            _make_run(end_state=EndState.COLLAPSED),
        ]
        runs += [_make_run(end_state=None) for _ in range(6)]
        result = end_state_distribution_check(runs)
        assert result.n_runs == 4
        assert result.observed[EndState.ENDURED] == pytest.approx(0.5)
        assert result.observed[EndState.COLLAPSED] == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# RunRecord round-trip
# ---------------------------------------------------------------------------


class TestRunRecordRoundTrip:
    def test_run_record_round_trips(self) -> None:
        agent_id = uuid4()
        original = _make_run(
            tool_calls=[_tc(), _tc(rejection_reason="bad")],
            resource_history={"food": [3, 2, 1]},
            agent_responses_per_agent={agent_id: ["line one", "line two"]},
            narration_per_turn=["turn one narration"],
            forced_choice_turns=1,
        )
        dumped = original.model_dump()
        restored = RunRecord.model_validate(dumped)
        assert restored == original
