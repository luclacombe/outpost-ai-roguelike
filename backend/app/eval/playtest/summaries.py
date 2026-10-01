"""Per-run summaries and corpus aggregates for `runs.json`.

Precomputes the numbers behind the baseline questions (difficulty, do
decisions matter, win/lose, strategy, loop clarity, bugs, engine health)
so the aggregator reads them instead of re-deriving from raw archives.
Pure: input is `PerRunArchive` objects, output is JSON-ready dicts.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from statistics import mean
from typing import TYPE_CHECKING, Any

from app.eval.choice_spread import summarize_choice_spread
from app.eval.paired import paired_comparison
from app.eval.playtest.schemas import PersonaTurnFlags, PersonaTurnOutput, VerdictAxes

if TYPE_CHECKING:  # pragma: no cover
    from app.eval.playtest.output import PerRunArchive

FLAG_NAMES: tuple[str, ...] = tuple(PersonaTurnFlags.model_fields)
AXIS_NAMES: tuple[str, ...] = tuple(VerdictAxes.model_fields)
QUOTED_FLAGS = ("suspected_bug", "confused")


def _choice_spread_turns(a: PerRunArchive) -> list[dict[str, Any]]:
    return list(a.run_record_summary.get("choice_spread") or [])


def _outcome_counts(outputs: list[PersonaTurnOutput]) -> dict[str, int]:
    vals = [t.outcome_matched_expectation for t in outputs]
    return {
        "matched": sum(1 for v in vals if v is True),
        "mismatched": sum(1 for v in vals if v is False),
        "n_a": sum(1 for v in vals if v is None),
    }


def per_run_summary(a: PerRunArchive) -> dict[str, Any]:
    """Run-level row for `runs.json.per_run_summaries` (paths added by caller)."""
    flag_counts = dict.fromkeys(FLAG_NAMES, 0)
    for t in a.per_turn_outputs:
        for k, v in t.flags.model_dump().items():
            if v:
                flag_counts[k] = flag_counts.get(k, 0) + 1

    axis_scores: dict[str, Any] = {}
    if a.verdict is not None:
        for k, axis in a.verdict.axes.model_dump().items():
            axis_scores[k] = axis["score"]
        axis_scores["continue_tomorrow"] = a.verdict.continue_tomorrow.decision

    return {
        "run_id": a.run_id,
        "persona": a.persona_id,
        "setting": a.setting_id,
        "seed": a.seed,
        "end_state": a.end_state,
        "turns_played": a.turns_played,
        "final_legitimacy": a.final_legitimacy,
        "final_resource_frac": a.final_resource_frac,
        "final_player_health": a.run_record_summary.get("final_player_health"),
        "axis_scores": axis_scores,
        "difficulty": a.verdict.difficulty.score if a.verdict else None,
        "bugs_noticed": list(a.verdict.bugs_noticed) if a.verdict else [],
        # Sub-bug (iii): verdict_failed/verdict_error let the aggregator count
        # silent verdict failures distinctly from never-finished runs.
        "verdict_failed": a.verdict_error is not None,
        "verdict_error": a.verdict_error,
        "flag_counts": flag_counts,
        "outcome_expectation": _outcome_counts(a.per_turn_outputs),
        "choice_spread": summarize_choice_spread(_choice_spread_turns(a)),
        "structural_oracles": a.per_run_oracles,
    }


def _mean_or_none(values: list[float]) -> float | None:
    return mean(values) if values else None


def _grouped_means(pairs: list[tuple[str, float]]) -> dict[str, float]:
    groups: dict[str, list[float]] = defaultdict(list)
    for key, v in pairs:
        groups[key].append(v)
    return {k: mean(vs) for k, vs in sorted(groups.items())}


def _verdict_axis_means(archives: list[PerRunArchive]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    judged = [a for a in archives if a.verdict is not None]
    for axis in AXIS_NAMES:
        rows = [(a, getattr(a.verdict.axes, axis).score) for a in judged]  # type: ignore[union-attr]
        out[axis] = {
            "mean": _mean_or_none([s for _, s in rows]),
            "n": len(rows),
            "by_persona": _grouped_means([(a.persona_id, s) for a, s in rows]),
            "by_setting": _grouped_means([(a.setting_id, s) for a, s in rows]),
        }
    return out


def _difficulty(archives: list[PerRunArchive]) -> dict[str, Any]:
    rows = [(a, a.verdict.difficulty.score) for a in archives if a.verdict is not None]
    dist = Counter(s for _, s in rows)
    return {
        "scale": "1 trivially easy, 2 easy, 3 well tuned, 4 hard, 5 unfairly hard",
        "mean": _mean_or_none([s for _, s in rows]),
        "n": len(rows),
        "distribution": {str(k): dist.get(k, 0) for k in range(1, 6)},
        "by_persona": _grouped_means([(a.persona_id, s) for a, s in rows]),
        "by_setting": _grouped_means([(a.setting_id, s) for a, s in rows]),
    }


def _outcome_expectation(archives: list[PerRunArchive]) -> dict[str, Any]:
    total = {"matched": 0, "mismatched": 0, "n_a": 0}
    by_persona: dict[str, dict[str, int]] = {}
    for a in archives:
        c = _outcome_counts(a.per_turn_outputs)
        bucket = by_persona.setdefault(a.persona_id, {"matched": 0, "mismatched": 0, "n_a": 0})
        for k, v in c.items():
            total[k] += v
            bucket[k] += v
    judged = total["matched"] + total["mismatched"]
    return {
        **total,
        "mismatch_rate": total["mismatched"] / judged if judged else None,
        "by_persona": by_persona,
    }


def _flagged_quotes(archives: list[PerRunArchive]) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {f: [] for f in QUOTED_FLAGS}
    for a in archives:
        for t in a.per_turn_outputs:
            for flag in QUOTED_FLAGS:
                if getattr(t.flags, flag) and flag in t.quotes:
                    out[flag].append(
                        {
                            "persona": a.persona_id,
                            "setting": a.setting_id,
                            "seed": a.seed,
                            "turn": t.turn,
                            "quote": t.quotes[flag],
                        }
                    )
    return out


def _end_states(archives: list[PerRunArchive]) -> dict[str, Any]:
    by_persona: dict[str, Counter[str]] = defaultdict(Counter)
    by_setting: dict[str, Counter[str]] = defaultdict(Counter)
    for a in archives:
        by_persona[a.persona_id][a.end_state] += 1
        by_setting[a.setting_id][a.end_state] += 1
    return {
        "overall": dict(Counter(a.end_state for a in archives)),
        "by_persona": {k: dict(v) for k, v in sorted(by_persona.items())},
        "by_setting": {k: dict(v) for k, v in sorted(by_setting.items())},
    }


def _corpus_choice_spread(archives: list[PerRunArchive]) -> dict[str, Any]:
    turns = [t for a in archives for t in _choice_spread_turns(a)]
    s = summarize_choice_spread(turns)
    n = s.get("n_turns", 0)
    if n:
        s["zero_spread_turn_share"] = s["zero_spread_turns"] / n
        s["run_ending_option_turn_share"] = s["turns_with_run_ending_option"] / n
        s["picked_rejected_rate"] = (
            s["picked_rejected_calls"] / s["picked_calls"] if s["picked_calls"] else None
        )
    return s


def corpus_aggregates(archives: list[PerRunArchive]) -> dict[str, Any]:
    """Corpus-level numbers for `runs.json` (see module docstring)."""
    flag_aggregates = dict.fromkeys(FLAG_NAMES, 0)
    for a in archives:
        for t in a.per_turn_outputs:
            for k, v in t.flags.model_dump().items():
                if v:
                    flag_aggregates[k] = flag_aggregates.get(k, 0) + 1

    judged = [a for a in archives if a.verdict is not None]
    paired_rows = [
        {
            "player": a.persona_id,
            "setting": a.setting_id,
            "seed": a.seed,
            "end_state": a.end_state,
            "final_legitimacy": a.final_legitimacy,
            "final_resource_frac": a.final_resource_frac,
            "turns_played": a.turns_played,
        }
        for a in archives
    ]
    return {
        "flag_aggregates": flag_aggregates,
        "verdict_axis_means": _verdict_axis_means(archives),
        "difficulty": _difficulty(archives),
        "continue_tomorrow_rate": (
            sum(a.verdict.continue_tomorrow.decision for a in judged) / len(judged)  # type: ignore[union-attr]
            if judged
            else None
        ),
        "outcome_expectation": _outcome_expectation(archives),
        "flagged_quotes": _flagged_quotes(archives),
        "bugs_noticed": [
            {"persona": a.persona_id, "setting": a.setting_id, "seed": a.seed, "bug": b}
            for a in judged
            for b in a.verdict.bugs_noticed  # type: ignore[union-attr]
        ],
        "end_states": _end_states(archives),
        "choice_spread": _corpus_choice_spread(archives),
        "paired": paired_comparison(paired_rows),
    }


def _compact_option(opt: dict[str, Any]) -> dict[str, Any]:
    return {
        "proposal_id": opt["proposal_id"],
        "name": opt["name"],
        "flavor": opt["flavor"],
        "resource_deltas": {k: v for k, v in opt["resource_deltas"].items() if v},
        "legitimacy_delta": opt["legitimacy_delta"],
        "player_health_delta": opt["player_health_delta"],
        "grievance_deltas": opt["grievance_deltas"],
        "status_changes": opt["status_changes"],
        "rejected": [f"{r['name']}:{r['reason']}" for r in opt["rejected_calls"]],
        "ends_run": opt["ends_run"],
    }


def build_turn_context(
    *,
    turn_inputs: list[dict[str, Any]],
    outputs: list[PersonaTurnOutput],
    choice_spread: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Join, per turn: what the persona saw, what it picked, what the engine
    would have done for each option. Feeds the markdown archive so the
    aggregator can check effects previews against engine ground truth."""
    inputs_by_turn = {t.get("turn"): t for t in turn_inputs}
    spread_by_turn = {s.get("turn"): s for s in choice_spread}
    out: list[dict[str, Any]] = []
    for o in outputs:
        view = inputs_by_turn.get(o.turn) or {}
        spread = spread_by_turn.get(o.turn) or {}
        chosen = next(
            (p for p in view.get("proposals") or [] if p.get("id") == o.chosen_proposal_id), {}
        )
        event = view.get("event") or {}
        out.append(
            {
                "turn": o.turn,
                "day": view.get("day"),
                "event_title": event.get("title"),
                "event_origin": event.get("origin"),
                "chosen": {
                    "id": o.chosen_proposal_id,
                    "name": chosen.get("name"),
                    "flavor": chosen.get("flavor"),
                    "effects_preview": list(chosen.get("effects_preview") or []),
                },
                "spread_l1_max": spread.get("spread_l1_max"),
                "identical_options": spread.get("identical_options"),
                "any_option_ends_run": spread.get("any_option_ends_run"),
                "options": [_compact_option(opt) for opt in spread.get("options") or []],
                "oracle_error": spread.get("error"),
            }
        )
    return out
