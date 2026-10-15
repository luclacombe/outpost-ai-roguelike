"""Engine-rendered option previews (spec §4.1).

`simulate_option` runs the exact path `nodes.resolve` runs (the picked
proposal's nested calls plus `derive_resolution_consequences`, phase
"resolve") on a deep copy. `outcome_from_calls` turns recorded calls into
the same `OptionOutcome`, so a preview and the audit of what happened go
through one renderer. End-of-turn decay is not part of an option; it is
shown once via `render_close`. Pure: no I/O, no LLM.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from app.game import engine
from app.game.resolution import OVERRULE_GRIEVANCE_REASON
from app.game.schemas import AdvisorStatus, ColonyState, Proposal, ToolCall
from app.game.setting_loader import Setting

_MINUS = "−"
_STATUS_WORD = {
    AdvisorStatus.DETAINED.value: "detained",
    AdvisorStatus.DISMISSED.value: "dismissed",
    AdvisorStatus.ACTIVE.value: "restored",
}


def fmt_delta(n: int) -> str:
    """Signed delta with a true minus sign and an explicit plus on gains."""
    if n > 0:
        return f"+{n}"
    if n < 0:
        return f"{_MINUS}{abs(n)}"
    return "0"


@dataclass(frozen=True)
class LegitimacyPart:
    reason: str
    applied: int


@dataclass(frozen=True)
class OptionOutcome:
    resource_deltas: dict[str, int]
    resources_capped: frozenset[str]
    legitimacy_parts: tuple[LegitimacyPart, ...]
    player_health_delta: int
    player_health_capped: bool
    advisor_health_deltas: dict[UUID, int]
    advisor_health_capped: frozenset[UUID]
    deaths: frozenset[UUID]
    grievance_deltas: dict[UUID, int]
    grievance_capped: frozenset[UUID]
    status_changes: tuple[tuple[UUID, str, str], ...]  # (agent id, from, to)
    rejected: tuple[ToolCall, ...]
    n_calls: int
    own_effect: bool
    ends_run: bool
    end_state: str | None
    agent_names: dict[UUID, str]
    status_before: dict[UUID, str]
    vector: tuple[float, ...]

    @property
    def legitimacy_delta(self) -> int:
        return sum(p.applied for p in self.legitimacy_parts)

    def to_spread_dict(self, picked: Proposal) -> dict[str, Any]:
        """The dict shape `app.eval.choice_spread` has always returned."""
        statuses = [
            {"agent": self.agent_names[aid], "from": old, "to": new}
            for aid, old, new in self.status_changes
        ] + [
            {"agent": self.agent_names[aid], "from": self.status_before[aid], "to": "DEAD"}
            for aid in self.deaths
        ]
        return {
            "proposal_id": picked.id,
            "flavor": str(picked.flavor.value),
            "name": picked.name,
            "resource_deltas": dict(self.resource_deltas),
            "legitimacy_delta": self.legitimacy_delta,
            "player_health_delta": self.player_health_delta,
            "grievance_deltas": {
                self.agent_names[aid]: d for aid, d in self.grievance_deltas.items() if d
            },
            "status_changes": statuses,
            "n_calls": self.n_calls,
            "rejected_calls": [
                {"name": tc.name, "reason": tc.rejection_reason} for tc in self.rejected
            ],
            "ends_run": self.end_state,
            "vector": list(self.vector),
        }


def simulate_option(
    state: ColonyState, picked: Proposal, proposals: list[Proposal]
) -> OptionOutcome:
    """Dry-run picking `picked` from `proposals`. Never mutates `state`."""
    before = state.model_copy(deep=True)
    derived = engine.derive_resolution_consequences(before, picked, proposals)
    calls = engine.stamp_phase([*picked.tool_calls_on_pick, *derived], "resolve")
    after = engine.apply_tool_calls(before, calls)
    return outcome_from_calls(after.turn_tool_calls[len(before.turn_tool_calls) :], before)


def outcome_from_calls(calls: list[ToolCall], state_before: ColonyState) -> OptionOutcome:
    """Build an OptionOutcome from recorded calls (carrying `applied_delta`).

    The after-state is rebuilt by re-applying the calls that were applied
    (rejected ones, including calls the graph refused by policy, are skipped)
    through the engine, so deaths and the end state are the engine's own.
    """
    applied = [tc for tc in calls if tc.rejection_reason is None]
    rebuilt = engine.apply_tool_calls(state_before.model_copy(deep=True), applied)
    after = engine.check_deaths(rebuilt)
    end_state = engine.compute_end_state(after)

    resource_deltas = dict.fromkeys(state_before.resources, 0)
    capped_res: set[str] = set()
    parts: list[LegitimacyPart] = []
    player_delta, player_capped = 0, False
    health: dict[UUID, int] = {}
    health_capped: set[UUID] = set()
    grievance: dict[UUID, int] = {}
    grievance_capped: set[UUID] = set()
    statuses: list[tuple[UUID, str, str]] = []
    status_before = {a.id: a.status.value for a in state_before.agents}
    own_effect = False

    for tc in applied:
        got = tc.applied_delta or 0
        asked = tc.args.get("delta")
        clamped_gain = isinstance(asked, int) and asked > 0 and got < asked
        if tc.name == "modify_resource":
            rid = tc.args["resource_id"]
            resource_deltas[rid] += got
            if clamped_gain:
                capped_res.add(rid)
            own_effect = own_effect or got != 0
        elif tc.name in ("modify_legitimacy", "back_leader"):
            parts.append(LegitimacyPart(str(tc.args.get("reason", "")), got))
        elif tc.name == "modify_health":
            own_effect = own_effect or got != 0
            if tc.args["target_id"] == "player":
                player_delta += got
                player_capped = player_capped or clamped_gain
            else:
                aid = UUID(tc.args["target_id"])
                health[aid] = health.get(aid, 0) + got
                if clamped_gain:
                    health_capped.add(aid)
        elif tc.name == "adjust_grievance":
            aid = UUID(tc.args["target_id"])
            grievance[aid] = grievance.get(aid, 0) + got
            if clamped_gain:
                grievance_capped.add(aid)
            if tc.args.get("reason") != OVERRULE_GRIEVANCE_REASON:
                own_effect = own_effect or got != 0
        elif tc.name == "set_advisor_status":
            aid = UUID(tc.args["target_id"])
            statuses.append((aid, status_before[aid], str(tc.args["new_status"])))
            own_effect = True

    alive_before = {a.id for a in state_before.agents if a.is_alive}
    deaths = frozenset(a.id for a in after.agents if a.id in alive_before and not a.is_alive)
    ends_run = end_state is not None and state_before.end_state is None

    rids = sorted(state_before.resources)
    legit_total = sum(p.applied for p in parts)
    vector = tuple(
        [resource_deltas[rid] / max(1, state_before.resources[rid].max) for rid in rids]
        + [legit_total / 100, player_delta / 100]
    )
    return OptionOutcome(
        resource_deltas=resource_deltas,
        resources_capped=frozenset(capped_res),
        legitimacy_parts=tuple(parts),
        player_health_delta=player_delta,
        player_health_capped=player_capped,
        advisor_health_deltas=health,
        advisor_health_capped=frozenset(health_capped),
        deaths=deaths,
        grievance_deltas=grievance,
        grievance_capped=frozenset(grievance_capped),
        status_changes=tuple(statuses),
        rejected=tuple(tc for tc in calls if tc.rejection_reason is not None),
        n_calls=len(calls),
        own_effect=own_effect,
        ends_run=ends_run,
        end_state=end_state.value if end_state is not None else None,
        agent_names={a.id: a.name for a in state_before.agents},
        status_before=status_before,
        vector=vector,
    )


def render_effects(setting: Setting, outcome: OptionOutcome) -> list[str]:
    """Player-facing preview lines. Order and wording: spec §4.1."""
    lines: list[str] = []
    for res in setting.resources:
        d = outcome.resource_deltas.get(res.id, 0)
        if d:
            cap = " (at cap)" if res.id in outcome.resources_capped else ""
            lines.append(f"{res.name.lower()} {fmt_delta(d)}{cap}")

    if outcome.legitimacy_parts:
        moving = [p for p in outcome.legitimacy_parts if p.applied != 0]
        if not moving:
            lines.append("legitimacy unchanged (at floor)")
        elif len(moving) == 1:
            lines.append(f"legitimacy {fmt_delta(moving[0].applied)} ({moving[0].reason})")
        else:
            total = sum(p.applied for p in moving)
            inner = ", ".join(f"{p.reason} {fmt_delta(p.applied)}" for p in moving)
            lines.append(f"legitimacy {fmt_delta(total)} ({inner})")

    if outcome.player_health_delta:
        cap = " (at cap)" if outcome.player_health_capped else ""
        lines.append(f"you {fmt_delta(outcome.player_health_delta)} health{cap}")

    for aid, d in outcome.advisor_health_deltas.items():
        if not d:
            continue
        name = outcome.agent_names[aid]
        suffix = ", dies" if aid in outcome.deaths else ""
        cap = " (at cap)" if aid in outcome.advisor_health_capped else ""
        lines.append(f"{name} {fmt_delta(d)} health{suffix}{cap}")

    for aid, d in outcome.grievance_deltas.items():
        if d:
            cap = " (at cap)" if aid in outcome.grievance_capped else ""
            lines.append(f"{outcome.agent_names[aid]} grievance {fmt_delta(d)}{cap}")

    for aid, _old, new in outcome.status_changes:
        lines.append(f"{outcome.agent_names[aid]} {_STATUS_WORD.get(new, new.lower())}")

    if outcome.ends_run:
        lines.append("run ends")
    if not outcome.own_effect:
        lines.insert(0, "no immediate effect")
    return lines


def render_close() -> list[str]:
    """The end-of-turn rule, shown once in the ChoosePanel header."""
    return [f"{fmt_delta(engine.CLOSE_LEGITIMACY_DELTA)} legitimacy at day's end"]


_COMPACT_EXCLUDE: dict[str, Any] = {
    "event_log": True,
    "consequence_seeds": True,
    "turn_tool_calls": True,
    "turn_proposals": True,
    "current_event": True,
    "agents": {"__all__": {"memory", "relationships"}},
}


def compact_state(state: ColonyState) -> dict[str, Any]:
    """Stats-only JSON of `state` for `turn_meta.pre_pick` (audit input)."""
    return state.model_dump(mode="json", exclude=_COMPACT_EXCLUDE)


def state_from_compact(data: dict[str, Any]) -> ColonyState:
    return ColonyState.model_validate(
        {
            **data,
            "event_log": [],
            "consequence_seeds": [],
            "turn_tool_calls": [],
            "turn_proposals": [],
            "current_event": None,
        }
    )
