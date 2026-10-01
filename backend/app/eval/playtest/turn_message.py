"""Render a `turn_view` dict into the markdown user message a persona reads.

Section order: Setting → Dashboard → Log → Last turn → Event → Advisor
reactions → Proposals → "Pick one". This follows the dashboard's reading
order: header + colony panel, the log, the AFTER narration the Continue
gate holds before the next event, then the event, debate and choices.

Reactions deliberately precede proposals: the earlier order (proposals
first) caused Sonnet personas to skim past the reactions section and
fabricate `(silent)` allegations against advisors whose reactions WERE
rendered (24/0 false-positive ratio in the 2026-04-29 smoke run).

`(silent)` renders only for a living advisor with no reaction line.
`_validate_quotes_against_dashboard` keys off that marker, so no other
section may print it. Dead advisors appear in the roster as KIA and are
left out of the reactions list.

Field-by-field UI parity lives in `turn_view.py`. Markdown, not JSON:
LLMs pattern-match narrative better in prose layout.
"""

from __future__ import annotations

from typing import Any

from app.eval.playtest.turn_view import legitimacy_tone, resource_tone

_SEVERITY_LABEL = {1: "minor", 2: "notable", 3: "serious", 4: "severe", 5: "catastrophic"}


def _signed(n: int) -> str:
    return f"+{n}" if n > 0 else str(n)


def _day(view: dict[str, Any]) -> str:
    day = view.get("day")
    day_txt = f"{max(0, int(day)):02d}" if isinstance(day, int) else "??"
    return f"- Day {day_txt} / {view['max_turns']}"


def _dashboard_lines(view: dict[str, Any]) -> list[str]:
    dash = view["dashboard"]
    legit = dash["legitimacy"]
    lines = [
        "## Dashboard",
        _day(view),
        f"- Legitimacy: {legit} ({legitimacy_tone(legit)})",
        "- Colony:",
    ]
    for r in dash["resources"]:
        tone = resource_tone(value=r["value"], critical_threshold=r["critical_threshold"])
        suffix = "" if tone == "ok" else f" ({tone})"
        lines.append(f"  - {r['name']} {r['value']}/{r['max']}{suffix}")
    lines.append("- Advisors:")
    for a in dash["advisors"]:
        lines.append(f"  - {a['name']} ({a['role']}): {a['status_label']}")
    return lines


def _log_lines(view: dict[str, Any]) -> list[str]:
    entries = view.get("event_log") or []
    lines = ["## Log"]
    if not entries:
        return [*lines, "- no entries yet."]
    for e in entries:
        turn = e.get("turn")
        tag = f"d{int(turn):02d}" if isinstance(turn, int) else "d??"
        lines.append(f"- {tag} {e['text']}")
    return lines


def _last_turn_lines(last: dict[str, Any]) -> list[str]:
    chosen = last["chosen"]
    effects = "; ".join(chosen.get("effects_preview") or []) or "(none shown)"
    changes = (
        [f"Legitimacy {_signed(last['legitimacy_delta'])}"] if last["legitimacy_delta"] else []
    )
    changes += [f"{name} {_signed(d)}" for name, d in last["resource_deltas"].items()]
    lines = [
        "## Last turn",
        f"- You chose: **{chosen['name']}** ({chosen['flavor']}). Effects shown: {effects}",
        f"- Aftermath: {last.get('narration') or '(no narration shown)'}",
        "- Changes since your last decision: " + ("; ".join(changes) or "none visible"),
    ]
    if last["deaths"]:
        lines.append("- Deaths: " + ", ".join(last["deaths"]))
    if last["status_changes"]:
        lines.append(
            "- Advisor status changes: "
            + "; ".join(f"{c['name']}: {c['from']} → {c['to']}" for c in last["status_changes"])
        )
    return lines


def _event_lines(event: dict[str, Any]) -> list[str]:
    meta = [str(event.get("category", "")).strip()]
    sev = event.get("severity")
    if isinstance(sev, int):
        meta.append(_SEVERITY_LABEL.get(sev, f"sev {sev}"))
    if event.get("origin") == "SEED_FIRED":
        meta.append("CONSEQUENCE")
    meta_txt = " · ".join(m for m in meta if m)
    return [
        "## Event",
        f"**{event.get('title', '')}**" + (f" ({meta_txt})" if meta_txt else ""),
        str(event.get("body", "")),
    ]


def format_turn_user_message(*, turn_input: dict[str, Any], displayed_order: list[str]) -> str:
    """Render one turn. See module docstring for section order and parity."""
    view = turn_input
    proposals_by_id = {p["id"]: p for p in view["proposals"]}
    advisors = view["dashboard"]["advisors"]
    advisor_by_id = {a["id"]: a for a in advisors}
    reactions_by_aid = {r["agent_id"]: r["text"] for r in view["advisor_reactions"]}

    setting_header = f"**{view['setting_name']}**" if view.get("setting_name") else ""
    lines: list[str] = [
        f"# Turn {view['turn']}",
        "",
        "## Setting",
        *([setting_header] if setting_header else []),
        view.get("setting_lore", ""),
        "",
        *_dashboard_lines(view),
        "",
        *_log_lines(view),
        "",
    ]
    if view.get("last_turn"):
        lines += [*_last_turn_lines(view["last_turn"]), ""]
    lines += [*_event_lines(view["event"]), "", "## Advisor reactions"]
    for a in advisors:
        if not a.get("is_alive", True):
            continue
        text = reactions_by_aid.get(a["id"], "(silent)")
        lines.append(f"- **{a['name']}** ({a['role']}): {text}")
    lines += ["", "## Proposals (in display order)"]
    for idx, pid in enumerate(displayed_order):
        p = proposals_by_id[pid]
        proposer = advisor_by_id.get(p.get("proposed_by_agent_id") or "")
        proposer_name = proposer["name"] if proposer else "(unknown)"
        effects = "; ".join(p.get("effects_preview") or []) or "(none shown)"
        lines.append(
            f"{idx}. **{p['name']}** ({p['flavor']}, by {proposer_name}) — {p['description']}"
        )
        lines.append(f"   Effects: {effects}")
    lines += [
        "",
        f"Pick one of the three proposals above. Your `displayed_order` field "
        f"in the response must be exactly: {displayed_order}",
    ]
    return "\n".join(lines)
