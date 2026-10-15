"""Narration prompt builders (spec §4.2).

Narration gets its own system block: the debate preamble teaches
`<agent name=...>` output and leaked it into prose. The block is stable
per run (setting + roster), so it carries cache_control (invariant #5);
it is likely under the provider's cache minimum, which is accepted.
"""

from __future__ import annotations

from typing import Any

from app.game.schemas import Agent, Event, Proposal
from app.game.setting_loader import Setting

RETRY_NOTE = (
    "\n\n<retry_note>Your last draft broke these rules: {codes}. Write it again: "
    "two or three plain English sentences, second person, naming only people on "
    "the roster.</retry_note>"
)


def build_narration_system_block(setting: Setting, agents: list[Agent]) -> dict[str, Any]:
    roster = "\n".join(f"  {a.name}, {a.role}, {'alive' if a.is_alive else 'dead'}" for a in agents)
    text = (
        f"You write the closing lines of one day at {setting.name}.\n"
        'The reader runs the colony. Address them as "you". When a title is '
        f"needed, they are the {setting.player_title}.\n\n"
        f"VOICE:\n{setting.narrative_voice.strip()}\n\n"
        f"ROSTER (the only people you may name):\n{roster}\n\n"
        "RULES:\n"
        "  * Two or three sentences, in English.\n"
        "  * Name only people on the roster. Anyone else is a role or a "
        'description: "a trucker", "the trader\'s cousin". Never invent a name.\n'
        "  * Show what the choice did. Don't repeat the numbers from <effects>.\n"
        "  * Plain prose: no tags, no headings, no lists, no notes about the task.\n"
    )
    return {"type": "text", "text": text, "cache_control": {"type": "ephemeral"}}


def build_narration_user_message(
    event: Event | None,
    picked: Proposal | None,
    proposer_name: str | None,
    dead_names: list[str],
) -> str:
    parts: list[str] = []
    if event is not None:
        parts.append(
            f"<event>\n  <title>{event.title}</title>\n  <body>{event.body}</body>\n</event>"
        )
    if picked is not None:
        proposer = f' proposer="{proposer_name}"' if proposer_name else ""
        parts.append(
            f'<choice{proposer} flavor="{picked.flavor.value}">'
            f"{picked.name}: {picked.description}</choice>"
        )
        parts.append(f"<effects>{'; '.join(picked.effects_preview) or 'none'}</effects>")
    parts.append(f"<dead>{', '.join(dead_names) if dead_names else 'none'}</dead>")
    parts.append("Close out this day.")
    return "\n".join(parts)


def fallback_line(picked: Proposal | None, proposer_name: str | None) -> str:
    if picked is None:
        return "The day closes."
    if proposer_name:
        return f"You backed {proposer_name}: {picked.name}."
    return f"You chose: {picked.name}."
