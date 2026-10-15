"""Per-rule legitimacy consequences (spec 4.1)."""

from __future__ import annotations

from uuid import UUID

from app.game.resolution import OVERRULE_GRIEVANCE_REASON, derive_resolution_consequences
from app.game.schemas import Proposal, ProposalFlavor, ToolCall
from tests._factories import _agent, _colony, _resource


def _proposal(
    pid: str, flavor: ProposalFlavor, calls: list[ToolCall], by: UUID | None = None
) -> Proposal:
    return Proposal(
        id=pid,
        proposed_by_agent_id=by,
        flavor=flavor,
        name=pid,
        description="d",
        effects_preview=[],
        tool_calls_on_pick=calls,
    )


def _health(target: str, delta: int) -> ToolCall:
    return ToolCall(
        name="modify_health",
        caller_id=None,
        args={"target_id": target, "delta": delta, "reason": "x"},
    )


def test_one_legitimacy_call_per_triggered_rule_with_named_reasons() -> None:
    astrid = _agent(name="Astrid")
    jaya = _agent(name="Jaya")
    state = _colony(
        agents=[astrid, jaya, _agent(name="C")], resources={"fuel": _resource(max_=100)}
    )
    dominant = _proposal("d", ProposalFlavor.DOMINANT, [], by=astrid.id)
    picked = _proposal(
        "m",
        ProposalFlavor.MORAL,
        [
            _health(str(jaya.id), -60),
            ToolCall(
                name="modify_resource",
                caller_id=None,
                args={"resource_id": "fuel", "delta": -40, "reason": "x"},
            ),
        ],
    )
    derived = derive_resolution_consequences(state, picked, [dominant, picked])
    legit = [
        (tc.args["delta"], tc.args["reason"]) for tc in derived if tc.name == "modify_legitimacy"
    ]
    assert legit == [
        (-12, "severe harm to Jaya"),
        (-5, "heavy fuel drain"),
        (-2, "overrules Astrid"),
    ]
    grievance = [tc for tc in derived if tc.name == "adjust_grievance"]
    assert grievance[0].args["reason"] == OVERRULE_GRIEVANCE_REASON


def test_player_target_reads_you() -> None:
    picked = _proposal("w", ProposalFlavor.WILDCARD, [_health("player", -50)])
    derived = derive_resolution_consequences(_colony(), picked, [picked])
    assert derived[0].args["reason"] == "severe harm to you"


def test_no_rule_no_calls() -> None:
    picked = _proposal("w", ProposalFlavor.WILDCARD, [])
    assert derive_resolution_consequences(_colony(), picked, [picked]) == []
