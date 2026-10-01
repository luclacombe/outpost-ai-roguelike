"""Balance-sweep summary: scripted policies × stable seeds × settings.

Input is one dict per finished game, as written to `raw/*.json` by
`scripts/sweep.py`. Output is `summary.json` (dict) and `summary.md`.

Rejected-call rates come in two flavours, both kept because each misses
something:
  - `picked_rejected_rate`: resolve-time rejections on the picked option,
    from the choice-spread oracle. `turn_logs` never persists these.
  - `turn_log_rejected_rate`: from `RunRecord.tool_calls` (persisted picked
    calls + applied autonomous calls). Autonomous rejections are not
    persisted, so this undercounts (see app/eval/CLAUDE.md).
"""

from __future__ import annotations

from collections import defaultdict
from statistics import mean
from typing import Any

from app.eval.end_state import END_STATE_TARGETS, END_STATE_TOLERANCE_PP
from app.eval.paired import paired_comparison

_STATES = ("ENDURED", "COLLAPSED", "OUSTED", "KILLED")
_TARGETS = {s.value: [lo, hi] for s, (lo, hi) in END_STATE_TARGETS.items()}


def _mean(values: list[Any]) -> float | None:
    present = [v for v in values if v is not None]
    return mean(present) if present else None


def _ratio(num: int, den: int) -> float | None:
    return num / den if den else None


def _end_states(games: list[dict[str, Any]]) -> dict[str, Any]:
    counts = {s: sum(1 for g in games if g["end_state"] == s) for s in _STATES}
    n = len(games)
    share = {s: (c / n if n else 0.0) for s, c in counts.items()}
    out_of_band = [
        s
        for s in _STATES
        if n
        and not (
            _TARGETS[s][0] - END_STATE_TOLERANCE_PP
            <= share[s]
            <= _TARGETS[s][1] + END_STATE_TOLERANCE_PP
        )
    ]
    return {"counts": counts, "share": share, "out_of_band": out_of_band}


def _stats(games: list[dict[str, Any]]) -> dict[str, Any]:
    turns = [g["turns_played"] for g in games]
    spreads = [g.get("choice_spread_summary") or {} for g in games]
    spread_turns = sum(s.get("n_turns", 0) for s in spreads)
    picked_calls = sum(s.get("picked_calls", 0) for s in spreads)
    costs = [g["cost_cents"] for g in games if g.get("cost_cents") is not None]
    return {
        "n": len(games),
        "end_states": _end_states(games),
        "resolved_by_harness": sum(1 for g in games if g.get("end_state_resolved_by_harness")),
        "turns_survived": {
            "mean": _mean(turns),
            "min": min(turns) if turns else None,
            "max": max(turns) if turns else None,
        },
        "final_legitimacy_mean": _mean([g.get("final_legitimacy") for g in games]),
        "final_player_health_mean": _mean([g.get("final_player_health") for g in games]),
        "final_resource_frac_mean": _mean([g.get("final_resource_frac") for g in games]),
        "picked_rejected_rate": _ratio(
            sum(s.get("picked_rejected_calls", 0) for s in spreads), picked_calls
        ),
        "turn_log_rejected_rate": _ratio(
            sum(g.get("tool_calls_rejected", 0) for g in games),
            sum(g.get("tool_calls_total", 0) for g in games),
        ),
        "choice_spread": {
            "n_turns": spread_turns,
            "spread_l1_max_mean": _mean(
                [s.get("spread_l1_max_mean") for s in spreads if s.get("n_turns")]
            ),
            "zero_spread_turn_share": _ratio(
                sum(s.get("zero_spread_turns", 0) for s in spreads), spread_turns
            ),
            "run_ending_option_turn_share": _ratio(
                sum(s.get("turns_with_run_ending_option", 0) for s in spreads), spread_turns
            ),
            "oracle_errors": sum(s.get("oracle_errors", 0) for s in spreads),
        },
        "cost_cents": {"mean": _mean(costs), "total": sum(costs)},
    }


def build_sweep_summary(
    games: list[dict[str, Any]], *, aborted: list[dict[str, Any]], meta: dict[str, Any]
) -> dict[str, Any]:
    """Aggregate finished games; aborted ones are listed, not summarized."""
    by_policy: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_ps: dict[str, dict[str, list[dict[str, Any]]]] = defaultdict(lambda: defaultdict(list))
    for g in games:
        by_policy[g["policy"]].append(g)
        by_ps[g["policy"]][g["setting"]].append(g)
    paired_rows = [{**g, "player": g["policy"]} for g in games]
    return {
        "meta": meta,
        "n_games": len(games),
        "n_aborted": len(aborted),
        "aborted": aborted,
        "targets": _TARGETS,
        "tolerance_pp": END_STATE_TOLERANCE_PP,
        "overall": _stats(games),
        "by_policy": {p: _stats(gs) for p, gs in sorted(by_policy.items())},
        "by_policy_setting": {
            p: {s: _stats(gs) for s, gs in sorted(per.items())} for p, per in sorted(by_ps.items())
        },
        "paired": paired_comparison(paired_rows),
    }


def _pct(v: float | None) -> str:
    return "—" if v is None else f"{v * 100:.0f}%"


def _num(v: float | None, digits: int = 1) -> str:
    return "—" if v is None else f"{v:.{digits}f}"


_ROW_HEAD = (
    "| {key} | n | Endured | Collapsed | Ousted | Killed | turns | legit | health | res | "
    "spread | zero-spread | rejected (picked) | ¢/game |"
)


def _row(label: str, st: dict[str, Any]) -> str:
    sh = st["end_states"]["share"]
    oob = set(st["end_states"]["out_of_band"])
    cells = [f"{_pct(sh[s])}{'*' if s in oob else ''}" for s in _STATES]
    cs = st["choice_spread"]
    return (
        f"| {label} | {st['n']} | "
        + " | ".join(cells)
        + f" | {_num(st['turns_survived']['mean'])} | {_num(st['final_legitimacy_mean'])}"
        f" | {_num(st['final_player_health_mean'])} | {_num(st['final_resource_frac_mean'], 2)}"
        f" | {_num(cs['spread_l1_max_mean'], 3)} | {_pct(cs['zero_spread_turn_share'])}"
        f" | {_pct(st['picked_rejected_rate'])} | {_num(st['cost_cents']['mean'])} |"
    )


def _table(key: str, rows: list[tuple[str, dict[str, Any]]]) -> list[str]:
    head = _ROW_HEAD.format(key=key)
    sep = "|" + "---|" * (head.count("|") - 1)
    return [head, sep, *(_row(label, st) for label, st in rows)]


def render_sweep_markdown(summary: dict[str, Any]) -> str:
    """Human-readable `summary.md`."""
    t = summary["targets"]
    tol = summary["tolerance_pp"]
    lines = [
        "# Balance sweep",
        "",
        f"- Games: {summary['n_games']} finished, {summary['n_aborted']} aborted",
        f"- Meta: `{summary['meta']}`",
        "- SPEC §10 end-state targets: "
        + ", ".join(f"{s} {t[s][0] * 100:.0f}-{t[s][1] * 100:.0f}%" for s in _STATES)
        + f" (±{tol * 100:.0f}pp; `*` = out of band)",
        "- Columns: turns = turns survived (mean); legit/health = final means; res = final "
        "mean resource fraction; spread = mean max pairwise L1 between the three options; "
        "zero-spread = turns where all options are identical in the engine; rejected = "
        "resolve-time rejected calls on the picked option.",
        "",
        "## Overall",
        "",
        *_table("all", [("all", summary["overall"])]),
        "",
        "## By policy",
        "",
        *_table("policy", list(summary["by_policy"].items())),
        "",
        "## By policy × setting",
        "",
        *_table(
            "policy / setting",
            [
                (f"{p} / {s}", st)
                for p, per in summary["by_policy_setting"].items()
                for s, st in per.items()
            ],
        ),
        "",
        "## Do decisions matter? (paired cells)",
        "",
    ]
    ps = summary["paired"]["summary"]
    lines += [
        f"- Paired cells: {ps['n_paired_cells']}",
        f"- Cells where policies reached different end states: "
        f"{_pct(ps['share_end_state_differs'])}",
        f"- Mean range across policies: final legitimacy {_num(ps['mean_legitimacy_range'])}, "
        f"resource fraction {_num(ps['mean_resource_frac_range'], 2)}, "
        f"turns {_num(ps['mean_turns_range'])}",
        "",
    ]
    cells = summary["paired"]["cells"]
    if cells:
        policies = sorted({p for c in cells for p in c["end_states"]})
        lines += [
            "| setting | seed | " + " | ".join(policies) + " | differs | legit range |",
            "|" + "---|" * (len(policies) + 4),
        ]
        for c in cells:
            states = [str(c["end_states"].get(p, "—")) for p in policies]
            lines.append(
                f"| {c['setting']} | {c['seed']} | "
                + " | ".join(states)
                + f" | {'yes' if c['end_states_differ'] else 'no'} | "
                f"{_num(c['legitimacy_range'], 0)} |"
            )
        lines.append("")
    lines += ["## Aborted games", ""]
    if summary["aborted"]:
        lines += [
            f"- {a['policy']} / {a['setting']} / seed {a['seed']}: {a['error']}"
            for a in summary["aborted"]
        ]
    else:
        lines.append("_(none)_")
    return "\n".join(lines) + "\n"
