"""Markdown rendering of one run's archive (the LLM-friendly format).

The meta-aggregator reads these. Persona output per turn is followed by the
choice-spread oracle's engine result for every option, which the persona
never saw, so effects previews can be checked against engine ground truth.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover
    from app.eval.playtest.output import PerRunArchive


_OUTCOME_LABEL = {True: "yes", False: "NO", None: "n/a"}


def _fmt(v: Any) -> str:
    if v is None:
        return "?"
    if isinstance(v, float):
        return f"{v:.2f}"
    return str(v)


def _chosen_suffix(ctx: dict[str, Any] | None) -> str:
    if not ctx or not ctx["chosen"].get("name"):
        return ""
    c = ctx["chosen"]
    effects = "; ".join(c["effects_preview"]) or "(none shown)"
    return f" — **{c['name']}** ({c['flavor']}); effects shown: {effects}"


def _option_line(opt: dict[str, Any], picked: str) -> str:
    parts = [f"{k} {v:+d}" for k, v in opt["resource_deltas"].items()]
    if opt["legitimacy_delta"]:
        parts.append(f"legitimacy {opt['legitimacy_delta']:+d}")
    if opt["player_health_delta"]:
        parts.append(f"player health {opt['player_health_delta']:+d}")
    parts += [f"{n} grievance {d:+d}" for n, d in opt["grievance_deltas"].items()]
    parts += [f"{c['agent']} {c['from']}→{c['to']}" for c in opt["status_changes"]]
    if opt["rejected"]:
        parts.append("rejected " + ", ".join(opt["rejected"]))
    if opt["ends_run"]:
        parts.append(f"ENDS RUN ({opt['ends_run']})")
    mark = "→" if opt["proposal_id"] == picked else " "
    body = "; ".join(parts) or "no engine change"
    return f"- {mark} `{opt['proposal_id']}` {opt['name']} ({opt['flavor']}): {body}"


def _engine_lines(ctx: dict[str, Any]) -> list[str]:
    """Choice-spread oracle ground truth (not shown to the persona)."""
    if ctx.get("oracle_error"):
        return [f"**Engine options (oracle):** unavailable ({ctx['oracle_error']})"]
    if not ctx.get("options"):
        return []
    head = (
        f"**Engine options (oracle, immediate resolution only):** spread "
        f"{_fmt(ctx['spread_l1_max'])}"
        + ("; options identical" if ctx.get("identical_options") else "")
        + ("; an option ends the run" if ctx.get("any_option_ends_run") else "")
    )
    return [head, *[_option_line(o, ctx["chosen"]["id"]) for o in ctx["options"]]]


def render_per_run_markdown(a: PerRunArchive) -> str:
    """Markdown rendering of one run — the LLM-friendly archive format."""
    lines: list[str] = [
        f"# Run: {a.persona_id} / {a.setting_id} / seed {a.seed}",
        "",
        f"**Persona:** {a.persona_id}",
        f"**Setting:** {a.setting_id}",
        f"**End state:** {a.end_state}",
        f"**Turns played:** {a.turns_played}",
        f"**Final legitimacy:** {_fmt(a.final_legitimacy)}",
        f"**Final mean resource fraction:** {_fmt(a.final_resource_frac)}",
        "",
        "---",
        "",
    ]
    context_by_turn = {c["turn"]: c for c in a.turn_context}
    for t in a.per_turn_outputs:
        ctx = context_by_turn.get(t.turn)
        lines.extend([f"## Turn {t.turn}", ""])
        if ctx and ctx.get("event_title"):
            lines.append(f"**Event:** {ctx['event_title']} (day {_fmt(ctx.get('day'))})")
        lines.extend(
            [
                f"**Displayed order:** {t.displayed_order}",
                f"**Chose:** `{t.chosen_proposal_id}` (position {t.shown_position_of_chosen})"
                + _chosen_suffix(ctx),
                "",
                f"**Reaction:** {t.reaction}",
                "",
                "**Previous pick matched expectation:** "
                + _OUTCOME_LABEL[t.outcome_matched_expectation],
                "**Flags:** "
                + ", ".join(f"{k}={'✓' if v else '·'}" for k, v in t.flags.model_dump().items()),
            ]
        )
        if t.quotes:
            lines.append("")
            lines.append("**Quotes:**")
            for k, v in t.quotes.items():
                lines.append(f"- *{k}*: {v}")
        if ctx:
            lines.extend(["", *_engine_lines(ctx)])
        lines.append("")
        lines.append("---")
        lines.append("")

    if a.verdict is not None:
        lines.extend(["## Run-level verdict", ""])
        for axis_name, axis in a.verdict.axes.model_dump().items():
            human_name = axis_name.replace("_", " ").capitalize()
            lines.append(f"### {human_name} — {axis['score']}/5")
            lines.append("")
            lines.append(axis["rationale"])
            if axis["turn_citations"]:
                lines.append("")
                lines.append(f"_Cited turns: {axis['turn_citations']}_")
            lines.append("")
        d = a.verdict.difficulty
        lines.extend(
            [
                f"### Difficulty — {d.score}/5 (1 trivially easy · 3 well tuned · 5 unfairly hard)",
                "",
                d.rationale,
                "",
                "### Bugs noticed",
                "",
                *([f"- {b}" for b in a.verdict.bugs_noticed] or ["_(none)_"]),
                "",
            ]
        )
        lines.extend(
            [
                f"### Continue tomorrow — **{'YES' if a.verdict.continue_tomorrow.decision else 'NO'}**",
                "",
                f"_Reason: {a.verdict.continue_tomorrow.reason}_",
                "",
                "### Closing",
                "",
                a.verdict.closing_paragraph,
                "",
            ]
        )
    elif a.verdict_error is not None:
        # Verdict step ran but failed after both attempts. Surface the
        # error so the meta-aggregator (and the dev re-running the harness)
        # can distinguish silent verdict failures from never-finished runs.
        lines.extend(
            [
                "## Run-level verdict",
                "",
                "**verdict_failed** — the per-run verdict step did not produce a "
                "valid response after the harness's single retry (sub-bug iii).",
                "",
                f"_Failure: {a.verdict_error}_",
                "",
            ]
        )
    else:
        lines.extend(
            [
                "## Run-level verdict",
                "",
                "_(no verdict — game did not reach a terminal state)_",
                "",
            ]
        )

    return "\n".join(lines)
