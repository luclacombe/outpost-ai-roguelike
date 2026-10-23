"""Engine-derived consequences of a pick (formerly engine.py Section 7).

Pure. Emits validated tool calls (caller_id=None); `nodes.resolve` and
`app.game.preview.simulate_option` fold them through engine.apply_tool_calls.
One modify_legitimacy call per triggered rule so the preview can show each
part (spec 4.1). Does not import engine (engine re-exports this function).
"""

from __future__ import annotations

from uuid import UUID

from app.game.schemas import Agent, ColonyState, Proposal, ProposalFlavor, ToolCall

# Rule constants — taste-tunable, in band with SPEC §5.3 ranges (small
# orders 1-2, medium 5-8, extreme 15-25). Each rule fires ONCE PER
# QUALIFYING NESTED CALL — a bigger atrocity should hurt more.
# Tuning signals:
#   _LEGIT_PER_50_HEALTH_DRAIN: raise toward -15 if severe-health proposals
#     don't visibly hurt the dashboard in playtests.
#   _LEGIT_PER_30PCT_RESOURCE_DRAIN: lower toward -3 if mid-game resource
#     reshuffles get penalised more than they should.
#   _LEGIT_OVERRULE_DOMINANT: lower toward -1 if the player feels punished
#     for non-dominant picks too often.
#   _GRIEVANCE_OVERRULE_DOMINANT: raise toward 3 if dominant-proposer coups
#     never trigger; lower toward 1 if they trigger too easily.
_LEGIT_PER_50_HEALTH_DRAIN = -12
_LEGIT_PER_30PCT_RESOURCE_DRAIN = -5
_LEGIT_OVERRULE_DOMINANT = -2
_GRIEVANCE_OVERRULE_DOMINANT = 2

OVERRULE_GRIEVANCE_REASON = "their dominant proposal was overruled"


def _agent(state: ColonyState, agent_id: UUID) -> Agent | None:
    return next((a for a in state.agents if a.id == agent_id), None)


def _resolve_target_name(state: ColonyState, target_id: object) -> str:
    """UUID → agent name, "player" → "you", anything else → "an advisor"."""
    if target_id == "player":
        return "you"
    if not isinstance(target_id, str):
        return "an advisor"
    try:
        agent = _agent(state, UUID(target_id))
    except ValueError:
        return "an advisor"
    return agent.name if agent is not None else "an advisor"


def _legitimacy(delta: int, reason: str) -> ToolCall:
    return ToolCall(
        name="modify_legitimacy", caller_id=None, args={"delta": delta, "reason": reason}
    )


def derive_resolution_consequences(
    state: ColonyState,
    picked: Proposal,
    all_proposals: list[Proposal],
) -> list[ToolCall]:
    """Pure: the engine's consequences of picking `picked` from `all_proposals`.

    Order: rule 1 (severe health) and rule 2 (heavy resource drain) per nested
    call in proposal order, then rule 3 (overrule) per DOMINANT proposer —
    its grievance call first, then its legitimacy call.
    """
    derived: list[ToolCall] = []

    for nested in picked.tool_calls_on_pick:
        if nested.name == "modify_health":
            delta = nested.args.get("delta", 0)
            if isinstance(delta, int) and delta <= -50:
                target = _resolve_target_name(state, nested.args.get("target_id"))
                derived.append(_legitimacy(_LEGIT_PER_50_HEALTH_DRAIN, f"severe harm to {target}"))

    for nested in picked.tool_calls_on_pick:
        if nested.name == "modify_resource":
            rid = nested.args.get("resource_id")
            delta = nested.args.get("delta", 0)
            res = state.resources.get(rid) if isinstance(rid, str) else None
            if (
                res is not None
                and isinstance(delta, int)
                and delta < 0
                and abs(delta) >= 0.30 * res.max
            ):
                derived.append(_legitimacy(_LEGIT_PER_30PCT_RESOURCE_DRAIN, f"heavy {rid} drain"))

    if picked.flavor != ProposalFlavor.DOMINANT:
        for prop in all_proposals:
            if prop.flavor != ProposalFlavor.DOMINANT or prop.proposed_by_agent_id is None:
                continue
            proposer = _agent(state, prop.proposed_by_agent_id)
            if proposer is None or not proposer.is_alive:
                continue
            derived.append(
                ToolCall(
                    name="adjust_grievance",
                    caller_id=None,
                    args={
                        "target_id": str(proposer.id),
                        "delta": _GRIEVANCE_OVERRULE_DOMINANT,
                        "reason": OVERRULE_GRIEVANCE_REASON,
                    },
                )
            )
            derived.append(_legitimacy(_LEGIT_OVERRULE_DOMINANT, f"overrules {proposer.name}"))

    return derived
