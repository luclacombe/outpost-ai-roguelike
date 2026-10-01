"""Balance-sweep summary: per policy, per policy×setting, paired, markdown."""

from __future__ import annotations

from typing import Any

import pytest

from app.eval.sweep_summary import build_sweep_summary, render_sweep_markdown


def _game(
    policy: str,
    setting: str,
    seed: int,
    end: str,
    *,
    legit: int = 50,
    turns: int = 9,
    cost: int = 20,
    spread: float = 0.2,
    picked_rejected: int = 0,
) -> dict[str, Any]:
    return {
        "policy": policy,
        "setting": setting,
        "seed": seed,
        "end_state": end,
        "turns_played": turns,
        "final_legitimacy": legit,
        "final_player_health": 100,
        "final_resource_frac": 0.5,
        "cost_cents": cost,
        "tool_calls_total": 10,
        "tool_calls_rejected": 1,
        "end_state_resolved_by_harness": False,
        "choice_spread_summary": {
            "n_turns": 2,
            "spread_l1_max_mean": spread,
            "zero_spread_turns": 1 if spread == 0 else 0,
            "turns_with_run_ending_option": 0,
            "picked_calls": 4,
            "picked_rejected_calls": picked_rejected,
            "oracle_errors": 0,
        },
    }


@pytest.fixture
def games() -> list[dict[str, Any]]:
    return [
        _game("moral", "arctic", 1, "ENDURED", legit=70, cost=10),
        _game("pragmatic", "arctic", 1, "COLLAPSED", legit=30, turns=6, cost=30),
        _game("moral", "desert", 2, "ENDURED", legit=60, spread=0.0),
        _game("pragmatic", "desert", 2, "ENDURED", legit=62, picked_rejected=2),
    ]


def test_by_policy_stats(games: list[dict[str, Any]]) -> None:
    s = build_sweep_summary(games, aborted=[], meta={"seeds": 1})
    moral = s["by_policy"]["moral"]
    assert moral["n"] == 2
    assert moral["end_states"]["counts"] == {
        "ENDURED": 2,
        "COLLAPSED": 0,
        "OUSTED": 0,
        "KILLED": 0,
    }
    assert moral["end_states"]["share"]["ENDURED"] == pytest.approx(1.0)
    assert "ENDURED" in moral["end_states"]["out_of_band"]  # 100% vs 35-45% (+-10pp)
    assert moral["turns_survived"]["mean"] == pytest.approx(9)
    assert moral["final_legitimacy_mean"] == pytest.approx(65)
    assert moral["cost_cents"] == {"mean": pytest.approx(15), "total": 30}
    assert moral["choice_spread"]["zero_spread_turn_share"] == pytest.approx(1 / 4)
    prag = s["by_policy"]["pragmatic"]
    assert prag["picked_rejected_rate"] == pytest.approx(2 / 8)
    assert prag["turn_log_rejected_rate"] == pytest.approx(0.1)


def test_by_policy_setting_and_paired(games: list[dict[str, Any]]) -> None:
    s = build_sweep_summary(games, aborted=[], meta={})
    assert s["by_policy_setting"]["pragmatic"]["arctic"]["n"] == 1
    paired = s["paired"]["summary"]
    assert paired["n_paired_cells"] == 2
    assert paired["share_end_state_differs"] == pytest.approx(0.5)
    assert s["targets"]["ENDURED"] == [0.35, 0.45]
    assert s["n_games"] == 4


def test_aborted_games_are_counted_not_summarized(games: list[dict[str, Any]]) -> None:
    aborted = [{"policy": "random", "setting": "arctic", "seed": 1, "error": "boom"}]
    s = build_sweep_summary(games, aborted=aborted, meta={})
    assert s["n_aborted"] == 1
    assert s["aborted"] == aborted
    assert "random" not in s["by_policy"]


def test_markdown_has_sections(games: list[dict[str, Any]]) -> None:
    aborted = [{"policy": "random", "setting": "arctic", "seed": 1, "error": "boom"}]
    md = render_sweep_markdown(build_sweep_summary(games, aborted=aborted, meta={"seeds": 1}))
    for heading in (
        "# Balance sweep",
        "## By policy",
        "## By policy × setting",
        "## Do decisions matter? (paired cells)",
        "## Aborted games",
    ):
        assert heading in md
    assert "| moral |" in md
    assert "boom" in md


def test_empty_sweep() -> None:
    s = build_sweep_summary([], aborted=[], meta={})
    assert s["n_games"] == 0
    assert s["by_policy"] == {}
    assert "# Balance sweep" in render_sweep_markdown(s)
