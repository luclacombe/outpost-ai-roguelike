"""Preparation between debate and pause (spec §4.1)."""

from __future__ import annotations

import random
from typing import Any
from uuid import UUID

from app.agent.prepare import prepare_proposals
from app.game import engine
from app.game.generator import generate_run
from app.game.preview import outcome_from_calls, render_effects
from app.game.schemas import ColonyState, ToolCall
from app.game.setting_loader import load_setting

USER = UUID("bbbbbbbb-0000-0000-0000-000000000002")
ARCTIC = load_setting("arctic_station_v1")


def _colony(setting_id: str = "arctic_station_v1", seed: int = 7) -> ColonyState:
    return generate_run(user_id=USER, setting_id=setting_id, seed=seed).model_copy(
        update={"turn_number": 3, "legitimacy": 60}
    )


def _nested(name: str, **args: Any) -> dict[str, Any]:
    return {"name": name, "args": {"reason": "r", **args}}


def _propose(caller: UUID, name: str, flavor: str, nested: list[dict], **extra: Any) -> ToolCall:
    return ToolCall(
        name="propose_action",
        caller_id=caller,
        args={
            "name": name,
            "description": f"{name} now.",
            "flavor": flavor,
            "tool_calls_on_pick": nested,
            **extra,
        },
    )


def test_debate_whitelist_records_and_skips_non_proposals() -> None:
    colony = _colony()
    ambient = ToolCall(
        name="modify_resource",
        caller_id=colony.agents[0].id,
        args={"resource_id": "fuel", "delta": -5, "reason": "storm"},
    )
    out = prepare_proposals(colony, [ambient], ARCTIC)
    assert out.state.resources["fuel"].value == colony.resources["fuel"].value
    rec = out.state.turn_tool_calls[-1]
    assert (rec.name, rec.phase, rec.rejection_reason) == (
        "modify_resource",
        "debate",
        "not_allowed_in_debate",
    )


def test_strip_unknown_resource_and_degrade() -> None:
    colony = _colony()
    a = colony.agents[0]
    call = _propose(
        a.id,
        "Burn stores",
        "DOMINANT",
        [
            _nested("modify_resource", resource_id="fuel", delta=-5),
            _nested("modify_resource", resource_id="water", delta=-5),
        ],
        effects_preview=["+99 legitimacy"],
    )
    out = prepare_proposals(colony, [call], ARCTIC)
    (p,) = out.proposals
    assert [tc.args["resource_id"] for tc in p.tool_calls_on_pick] == ["fuel"]
    assert p.effects_preview == ["fuel −5"]
    assert out.classes == ["degraded"]
    assert out.valid_count == 0
    stripped = [
        tc for tc in out.state.turn_tool_calls if tc.rejection_reason == "stripped:unknown_resource"
    ]
    assert len(stripped) == 1 and stripped[0].phase == "prepare"


def test_kept_nested_calls_recorded_as_prepare_validation_records() -> None:
    colony = _colony()
    call = _propose(
        colony.agents[0].id,
        "Burn",
        "DOMINANT",
        [_nested("modify_resource", resource_id="fuel", delta=-5)],
    )
    out = prepare_proposals(colony, [call], ARCTIC)
    kept = [tc for tc in out.state.turn_tool_calls if tc.phase == "prepare"]
    assert len(kept) == 1
    assert kept[0].rejection_reason is None
    assert kept[0].applied_delta is None


def test_selection_prefers_valid_then_degraded_then_no_effect() -> None:
    colony = _colony()
    ids = [a.id for a in colony.agents]
    calls = [
        _propose(ids[0], "Nothing", "DOMINANT", []),
        _propose(
            ids[1],
            "Half",
            "MORAL",
            [
                _nested("modify_resource", resource_id="food", delta=2),
                _nested("modify_resource", resource_id="oxygen", delta=2),
            ],
        ),
        _propose(
            ids[2],
            "Good one",
            "WILDCARD",
            [_nested("modify_resource", resource_id="fuel", delta=-3)],
        ),
        _propose(
            ids[3], "Good two", "MORAL", [_nested("modify_resource", resource_id="food", delta=-3)]
        ),
    ]
    out = prepare_proposals(colony, calls, ARCTIC)
    assert [p.name for p in out.proposals] == ["Good one", "Good two", "Half"]
    assert out.classes == ["valid", "valid", "degraded"]
    assert out.valid_count == 2
    assert [p.name for p in out.state.turn_proposals] == ["Good one", "Good two", "Half"]


def test_prepare_handles_fewer_than_three_proposals() -> None:
    colony = _colony()
    call = _propose(
        colony.agents[0].id,
        "Lost cause",
        "DOMINANT",
        [_nested("modify_resource", resource_id="water", delta=-5)],
    )
    out = prepare_proposals(colony, [call], ARCTIC)
    assert len(out.proposals) == 1
    assert out.proposals[0].effects_preview == ["no immediate effect"]
    assert out.classes == ["no_effect"]


def test_full_clamp_counts_as_no_effect() -> None:
    colony = _colony()
    fuel = colony.resources["fuel"]
    colony = colony.model_copy(
        update={
            "resources": {**colony.resources, "fuel": fuel.model_copy(update={"value": fuel.max})}
        }
    )
    call = _propose(
        colony.agents[0].id,
        "Top up",
        "DOMINANT",
        [_nested("modify_resource", resource_id="fuel", delta=5)],
    )
    assert prepare_proposals(colony, [call], ARCTIC).classes == ["no_effect"]


def test_cancelling_calls_count_as_no_effect() -> None:
    colony = _colony()
    call = _propose(
        colony.agents[0].id,
        "Churn",
        "DOMINANT",
        [
            _nested("modify_resource", resource_id="fuel", delta=5),
            _nested("modify_resource", resource_id="fuel", delta=-5),
        ],
    )
    out = prepare_proposals(colony, [call], ARCTIC)
    assert out.classes == ["no_effect"]
    assert out.valid_count == 0
    assert out.proposals[0].effects_preview[0] == "no immediate effect"


def test_clamp_cancel_counts_as_no_effect() -> None:
    colony = _colony()
    fuel = colony.resources["fuel"]
    colony = colony.model_copy(
        update={
            "resources": {
                **colony.resources,
                "fuel": fuel.model_copy(update={"value": fuel.max - 5}),
            }
        }
    )
    call = _propose(
        colony.agents[0].id,
        "Churn",
        "DOMINANT",
        [
            _nested("modify_resource", resource_id="fuel", delta=10),
            _nested("modify_resource", resource_id="fuel", delta=-5),
        ],
    )
    out = prepare_proposals(colony, [call], ARCTIC)
    assert out.classes == ["no_effect"]
    assert out.proposals[0].effects_preview[0] == "no immediate effect"


def test_risk_notes_sanitized_and_counted() -> None:
    colony = _colony()
    ids = [a.id for a in colony.agents]
    calls = [
        _propose(
            ids[0],
            "A",
            "DOMINANT",
            [_nested("modify_resource", resource_id="fuel", delta=-1)],
            risk_note="Fuel drops 10.",
        ),
        _propose(
            ids[1],
            "B",
            "MORAL",
            [_nested("modify_resource", resource_id="food", delta=-1)],
            risk_note="The cold could get worse.",
        ),
    ]
    out = prepare_proposals(colony, calls, ARCTIC)
    assert [p.risk_note for p in out.proposals] == [None, "The cold could get worse."]
    assert out.risk_notes_dropped == 1


def _random_turn(rng: random.Random, colony: ColonyState) -> list[ToolCall]:
    rids = [*colony.resources, "water", "oxygen"]
    flavors = ["DOMINANT", "MORAL", "WILDCARD"]
    calls = []
    for i, agent in enumerate(colony.agents[: rng.randint(2, 4)]):
        nested = []
        for _ in range(rng.randint(0, 3)):
            kind = rng.choice(["res", "res", "health", "grievance"])
            if kind == "res":
                nested.append(
                    _nested(
                        "modify_resource", resource_id=rng.choice(rids), delta=rng.randint(-45, 45)
                    )
                )
            elif kind == "health":
                target = rng.choice([*(str(a.id) for a in colony.agents), "player"])
                nested.append(
                    _nested("modify_health", target_id=target, delta=rng.randint(-70, 20))
                )
            else:
                target = str(rng.choice(colony.agents).id)
                nested.append(
                    _nested("adjust_grievance", target_id=target, delta=rng.randint(-3, 3))
                )
        calls.append(_propose(agent.id, f"Plan {i}", flavors[i % 3], nested))
    return calls


def test_property_preview_equals_resolve_outcome() -> None:
    """Spec §7 property test: what is previewed is what resolve applies."""
    rng = random.Random(20261015)
    for setting_id in ("arctic_station_v1", "desert_outpost_v1", "orbital_colony_v1"):
        setting = load_setting(setting_id)
        for seed in range(15):
            colony = _colony(setting_id, seed).model_copy(
                update={"legitimacy": rng.randint(0, 100)}
            )
            prepared = prepare_proposals(colony, _random_turn(rng, colony), setting)
            for picked in prepared.proposals:
                derived = engine.derive_resolution_consequences(
                    prepared.state, picked, prepared.proposals
                )
                calls = engine.stamp_phase([*picked.tool_calls_on_pick, *derived], "resolve")
                after = engine.apply_tool_calls(prepared.state, calls)
                recorded = after.turn_tool_calls[len(prepared.state.turn_tool_calls) :]
                assert all(tc.rejection_reason is None for tc in recorded), recorded
                rendered = render_effects(setting, outcome_from_calls(recorded, prepared.state))
                assert rendered == picked.effects_preview, (setting_id, seed, picked.name)
