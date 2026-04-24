"""Prompt assembly + debate response parsing for Outpost agents.

Three cache breakpoints on the system blocks keep input cost near ~10% of raw
after turn 2 (ARCHITECTURE §3.5 / plan role-senior-ai-agent-memoized-swing):

  1. Preamble     — static game rules + response contract. Never changes.
  2. Personas     — stable identity + axes + verbal tic per run.
  3. Colony state — per-turn volatile: resources, legitimacy, player health,
                    and per-agent memory / grievance / morale / health /
                    relationships / alive flag.

The user message (current event or turn summary) is NOT cached.

Everything here is sync and pure. No Anthropic calls, no DB, no file I/O.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from app.game.schemas import Agent, ColonyState, Event, ToolCall

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# System block builders
# ---------------------------------------------------------------------------

_PREAMBLE_TEXT = """\
You are one of four advisors running a fragile colony. Each turn a single
crisis, dilemma, or opportunity is presented. Every living advisor reacts
in character and proposes actions via tool calls. The human player
chooses one action; the engine validates and applies the effects. Dead
means dead: advisors removed on death never return.

RESPONSE CONTRACT (obey exactly):
  * Produce a single text block containing one XML-delimited reaction per
    living advisor, in the form:
        <agent name="EXACT_NAME">in-character reaction, at most 25 words</agent>
    Use the exact name from the persona block. Do not invent advisors.
  * For any action you want applied to the world, emit a separate tool_use
    block. Never embed numbers or state changes in the prose.
  * Every tool_use MUST include `caller_agent_id` set to the UUID of the
    agent emitting the call. Omitting it routes the call to the engine as
    if nobody claimed authorship.

AVAILABLE TOOLS:
  propose_action       — add one DOMINANT / MORAL / WILDCARD choice to the
                         player's three-option pool for this turn.
  modify_resource      — shift a colony resource (e.g. fuel -10).
  modify_health        — change an agent's or the player's health.
  adjust_grievance     — push another agent's grievance up or down.
  speak_to_agent       — private line to another advisor.
  adjust_relationship  — shift your own relationship score toward another.

DEBATE TARGETS (per-turn, aspirational — the engine caps at three):
  * Exactly three `propose_action` calls across the living advisors,
    one per flavor: one DOMINANT (the pragmatic / hard-edged option),
    one MORAL (principled / costly), one WILDCARD (unorthodox / risky).
  * If four advisors are alive, one of them sits this turn out rather
    than duplicating a flavor.

VOICE:
  Short lines. Concrete nouns. No softeners, no filler, no AI tells.
  Use your verbal tic sparingly — at most once per reaction.
"""


def build_preamble_block() -> dict[str, Any]:
    """Static, run-independent preamble. Cache breakpoint 1."""
    return {
        "type": "text",
        "text": _PREAMBLE_TEXT,
        "cache_control": {"type": "ephemeral"},
    }


def _render_persona(agent: Agent) -> str:
    tic = agent.verbal_tic or ""
    return (
        f'<persona id="{agent.id}" name="{agent.name}" role="{agent.role}" '
        f'archetype_quadrant="{agent.archetype_quadrant.value}" '
        f'ethics="{agent.ethics:.2f}" temperament="{agent.temperament:.2f}" '
        f'loyalty="{agent.loyalty:.2f}" verbal_tic="{tic}">\n'
        f"  {_persona_core_voice(agent)}\n"
        f"</persona>"
    )


def _persona_core_voice(agent: Agent) -> str:
    """Synthesize a short voice description from quadrant + axes.

    Kept deterministic so this block remains cache-stable for a run.
    Do NOT include memory, grievance, morale, health, relationships here —
    those are per-turn volatile state.
    """
    ethics_label = "idealist" if agent.ethics > 0 else "pragmatist"
    temperament_label = "bold" if agent.temperament > 0 else "cautious"
    loyalty_label = "collectivist" if agent.loyalty > 0 else "individualist"
    return (
        f"Quadrant: {agent.archetype_quadrant.value}. "
        f"Voice: {ethics_label}, {temperament_label}, {loyalty_label}. "
        f"Speaks in the register of a {agent.role}, never breaks character."
    )


def build_personas_block(agents: list[Agent]) -> dict[str, Any]:
    """All 4 personas in one block, stable for the run. Cache breakpoint 2."""
    body = "\n".join(_render_persona(a) for a in agents)
    text = f"<personas>\n{body}\n</personas>"
    return {
        "type": "text",
        "text": text,
        "cache_control": {"type": "ephemeral"},
    }


def _render_agent_state(agent: Agent) -> str:
    alive_flag = "true" if agent.is_alive else "false"
    lines = [
        f'<agent_state id="{agent.id}" name="{agent.name}" '
        f'grievance="{agent.grievance}" morale="{agent.morale}" '
        f'health="{agent.health}" is_alive="{alive_flag}">',
    ]
    if agent.memory:
        lines.append("  memories:")
        for m in agent.memory:
            lines.append(f"    - {m}")
    else:
        lines.append("  memories: (none)")
    if agent.relationships:
        rel_parts = [f"→{tgt}: {score:+d}" for tgt, score in agent.relationships.items()]
        lines.append("  relationships: " + ", ".join(rel_parts))
    else:
        lines.append("  relationships: (none)")
    lines.append("</agent_state>")
    return "\n".join(lines)


def build_colony_state_block(state: ColonyState) -> dict[str, Any]:
    """Per-turn volatile state. Cache breakpoint 3.

    Deliberately omits `consequence_seeds`, `event_log`, and `turn_cost_cents`
    — those are engine bookkeeping, not character-facing.
    """
    lines: list[str] = [
        "<colony_state>",
        f"  turn_number: {state.turn_number} / {state.max_turns}",
        f"  legitimacy: {state.legitimacy}/100",
        f"  player_health: {state.player_health}/100",
        "  resources:",
    ]
    for rid, r in state.resources.items():
        lines.append(f"    {rid}: {r.value}/{r.max} (critical: {r.critical_threshold})")
    lines.append("  agents:")
    for agent in state.agents:
        rendered = _render_agent_state(agent)
        # Indent every line for nesting under <colony_state>.
        lines.append("    " + rendered.replace("\n", "\n    "))
    lines.append("</colony_state>")
    return {
        "type": "text",
        "text": "\n".join(lines),
        "cache_control": {"type": "ephemeral"},
    }


# ---------------------------------------------------------------------------
# User message builders (not cached)
# ---------------------------------------------------------------------------


def build_debate_user_message(event: Event) -> str:
    """Plain user-role string for the batched debate call."""
    return (
        f'<event id="{event.id}" category="{event.category}" '
        f'severity="{event.severity}" origin="{event.origin.value}">\n'
        f"  <title>{event.title}</title>\n"
        f"  <body>{event.body}</body>\n"
        f"</event>\n\n"
        "Each living advisor: react in character (≤25 words) inside an "
        '<agent name="..."> tag. Then emit the tool_use blocks you want '
        "applied. Remember: exactly three `propose_action` calls across "
        "the group (one DOMINANT, one MORAL, one WILDCARD), and every "
        "tool_use MUST set `caller_agent_id` to your UUID."
    )


def build_autonomous_user_message(turn_summary: str) -> str:
    """User message for off-turn introspective agent actions.

    The autonomous tool whitelist (`adjust_own_grievance`, `remember`,
    `speak_to_agent`, `adjust_relationship`, `initiate_coup`, `back_leader`)
    is enforced upstream by passing only those schemas to the model; this
    prose just nudges the model toward appropriate introspective behavior.
    """
    return (
        f"<turn_summary>{turn_summary}</turn_summary>\n\n"
        "The player has decided. You are off-turn: no crisis to solve, "
        "just room to react. Each living advisor may (optionally) emit "
        "introspective tool_use blocks — adjust your own grievance, write "
        "a memory, speak privately to another advisor, shift a "
        "relationship, back the leader, or initiate a coup if the "
        "conditions feel right. Stay silent if nothing is warranted. "
        "Every tool_use MUST include `caller_agent_id`."
    )


# ---------------------------------------------------------------------------
# Debate response parser
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DebateParse:
    """Structured result of parsing an Anthropic debate response."""

    per_agent_text: dict[UUID, str] = field(default_factory=dict)
    tool_calls: list[ToolCall] = field(default_factory=list)


# Non-greedy, DOTALL so reactions may span lines. Missing-closing-tag
# sections are simply not matched — by design, the parser is resilient.
_AGENT_TAG_RE = re.compile(
    r'<agent\s+name\s*=\s*"([^"]+)"\s*>(.*?)</agent\s*>',
    re.DOTALL,
)


def parse_debate_response(response: Any, alive_agents: list[Agent]) -> DebateParse:
    """Split the response into per-agent reactions and typed tool calls.

    - Text blocks: extract `<agent name="X">...</agent>` sections; map
      name → agent.id using `alive_agents`. Unknown names are logged and
      skipped, not errored.
    - Tool-use blocks: build a `ToolCall` for each; pull `caller_agent_id`
      out of `input` into `ToolCall.caller_id` (UUID or None), and strip
      it from the persisted args.
    - Malformed XML, missing closing tags, or unparseable UUIDs are
      tolerated — we return whatever was parseable.
    """
    name_to_id: dict[str, UUID] = {a.name: a.id for a in alive_agents}
    per_agent_text: dict[UUID, str] = {}
    tool_calls: list[ToolCall] = []

    for block in getattr(response, "content", []) or []:
        btype = getattr(block, "type", None)
        if btype == "text":
            text = getattr(block, "text", "") or ""
            for match in _AGENT_TAG_RE.finditer(text):
                name = match.group(1).strip()
                body = match.group(2).strip()
                agent_id = name_to_id.get(name)
                if agent_id is None:
                    logger.warning(
                        "parse_debate_response: unknown agent name %r in response; skipping",
                        name,
                    )
                    continue
                per_agent_text[agent_id] = body
        elif btype == "tool_use":
            name = getattr(block, "name", None)
            raw_input = getattr(block, "input", None) or {}
            if not isinstance(raw_input, dict) or not isinstance(name, str):
                logger.warning(
                    "parse_debate_response: malformed tool_use block (name=%r, input-type=%r); skipping",
                    name,
                    type(raw_input).__name__,
                )
                continue
            args = dict(raw_input)
            caller_raw = args.pop("caller_agent_id", None)
            caller_id: UUID | None = None
            if caller_raw is not None:
                try:
                    caller_id = UUID(str(caller_raw))
                except (ValueError, TypeError):
                    logger.warning(
                        "parse_debate_response: unparseable caller_agent_id %r; "
                        "treating as engine-emitted",
                        caller_raw,
                    )
                    caller_id = None
            tool_calls.append(ToolCall(name=name, caller_id=caller_id, args=args))
        # Unknown block types (thinking, etc.) are silently ignored.

    return DebateParse(per_agent_text=per_agent_text, tool_calls=tool_calls)
