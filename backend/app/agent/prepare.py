"""Turn preparation between the debate and the pause (spec §4.1).

Shared by `nodes.count_valid_proposals` (retry decision, throwaway result)
and the `apply_tool_calls` node (kept result). Pure.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

from app.agent.narration_guard import build_allowed_terms, sanitize_risk_note
from app.game import engine
from app.game.preview import render_effects, simulate_option
from app.game.schemas import ColonyState, Proposal, ToolCall
from app.game.setting_loader import Setting

DEBATE_TOOL_NAMES: frozenset[str] = frozenset({"propose_action"})
SURFACED_PROPOSALS = 3

ProposalClass = Literal["valid", "degraded", "no_effect"]
_CLASS_ORDER: dict[str, int] = {"valid": 0, "degraded": 1, "no_effect": 2}


@dataclass(frozen=True)
class PreparedTurn:
    state: ColonyState
    proposals: list[Proposal]
    classes: list[ProposalClass]
    valid_count: int
    risk_notes_dropped: int


def prepare_proposals(
    state: ColonyState,
    debate_tool_calls: list[ToolCall],
    setting: Setting,
    extra_texts: Iterable[str] = (),
) -> PreparedTurn:
    folded = state
    for tc in debate_tool_calls:
        stamped = tc.model_copy(update={"phase": "debate"})
        if tc.name in DEBATE_TOOL_NAMES:
            folded = engine.apply_tool_call(folded, stamped)
        else:
            folded = engine.record_rejected(folded, stamped, "not_allowed_in_debate")

    new_proposals = folded.turn_proposals[len(state.turn_proposals) :]
    candidates: list[tuple[int, Proposal, ProposalClass]] = []
    records: list[ToolCall] = []
    for idx, proposal in enumerate(new_proposals):
        kept, proposal_records, changed = _strip(folded, proposal)
        records += proposal_records
        stripped = len(kept) < len(proposal.tool_calls_on_pick)
        cls: ProposalClass = "no_effect" if not changed else "degraded" if stripped else "valid"
        candidates.append((idx, proposal.model_copy(update={"tool_calls_on_pick": kept}), cls))

    selected = sorted(candidates, key=lambda c: (_CLASS_ORDER[c[2]], c[0]))[:SURFACED_PROPOSALS]
    surfaced = [p for _, p, _ in selected]

    allowed = build_allowed_terms(
        setting,
        folded,
        [*extra_texts, *(t for p in new_proposals for t in (p.name, p.description))],
    )
    roster = frozenset(a.name for a in folded.agents)
    rendered: list[Proposal] = []
    dropped = 0
    for proposal in surfaced:
        outcome = simulate_option(folded, proposal, surfaced)
        note = sanitize_risk_note(proposal.risk_note, setting, allowed, roster)
        if proposal.risk_note and note is None:
            dropped += 1
        rendered.append(
            proposal.model_copy(
                update={"effects_preview": render_effects(setting, outcome), "risk_note": note}
            )
        )

    out_state = folded.model_copy(
        update={
            "turn_proposals": rendered,
            "turn_tool_calls": [*folded.turn_tool_calls, *records],
        }
    )
    return PreparedTurn(
        state=out_state,
        proposals=rendered,
        classes=[c for _, _, c in selected],
        valid_count=sum(1 for _, _, c in candidates if c == "valid"),
        risk_notes_dropped=dropped,
    )


def _strip(state: ColonyState, proposal: Proposal) -> tuple[list[ToolCall], list[ToolCall], bool]:
    """Apply nested calls one by one; drop rejects. Returns (kept, records, changed)."""
    running = state
    kept: list[ToolCall] = []
    records: list[ToolCall] = []
    changed = False
    for nested in proposal.tool_calls_on_pick:
        after = engine.apply_tool_call(running, nested)
        logged = after.turn_tool_calls[-1]
        if logged.rejection_reason is not None:
            records.append(
                logged.model_copy(
                    update={
                        "phase": "prepare",
                        "rejection_reason": f"stripped:{logged.rejection_reason}",
                    }
                )
            )
            continue
        kept.append(nested)
        changed = changed or nested.name == "set_advisor_status" or bool(logged.applied_delta)
        records.append(logged.model_copy(update={"phase": "prepare", "applied_delta": None}))
        running = after
    return kept, records, changed
