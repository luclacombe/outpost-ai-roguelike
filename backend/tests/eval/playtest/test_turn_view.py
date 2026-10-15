"""UI-parity turn view: what the persona sees must match the dashboard."""

from __future__ import annotations

from typing import Any
from uuid import UUID

import pytest

from app.eval.playtest.turn_view import TurnViewTracker, legitimacy_tone, resource_tone, status_pill
from app.game.generator import generate_run
from app.game.schemas import ColonyState

USER = UUID("bbbbbbbb-0000-0000-0000-000000000002")


def _event(eid: str, title: str) -> dict[str, Any]:
    return {
        "id": eid,
        "category": "infrastructure",
        "title": title,
        "body": "Body.",
        "severity": 3,
        "origin": "NATURAL",
    }


def _proposals(colony: ColonyState) -> list[dict[str, Any]]:
    a = colony.agents
    return [
        {
            "id": "p1",
            "proposed_by_agent_id": str(a[0].id),
            "flavor": "DOMINANT",
            "name": "Cut heat",
            "description": "d",
            "effects_preview": ["-10 fuel"],
        },
        {
            "id": "p2",
            "proposed_by_agent_id": str(a[1].id),
            "flavor": "MORAL",
            "name": "Share rations",
            "description": "d",
            "effects_preview": ["+5 food"],
        },
        {
            "id": "p3",
            "proposed_by_agent_id": None,
            "flavor": "WILDCARD",
            "name": "Gamble",
            "description": "d",
            "effects_preview": [],
        },
    ]


def _turn_frames(colony: ColonyState, event: dict[str, Any], day: int) -> list[dict[str, Any]]:
    return [
        {"event": "agent_spoke", "data": {"agent_id": str(colony.agents[0].id), "text": "Cut it."}},
        {
            "event": "proposals_ready",
            "data": {"turn_number": day, "current_event": event, "proposals": _proposals(colony)},
        },
    ]


@pytest.fixture
def colony() -> ColonyState:
    return generate_run(user_id=USER, setting_id="arctic_station_v1", seed=3)


def test_status_pill_mirrors_agent_roster(colony: ColonyState) -> None:
    a = colony.agents[0]
    assert status_pill(a) == "STEADY"
    assert status_pill(a.model_copy(update={"is_alive": False})) == "KIA"
    assert status_pill(a.model_copy(update={"health": 20, "grievance": 9})) == "WOUNDED"
    assert status_pill(a.model_copy(update={"grievance": 7, "morale": 10})) == "STEWING"
    assert status_pill(a.model_copy(update={"morale": 29})) == "TIRED"


def test_tones_mirror_meters() -> None:
    assert legitimacy_tone(60) == "ok"
    assert legitimacy_tone(30) == "warning"
    assert legitimacy_tone(29) == "danger"
    assert resource_tone(value=21, critical_threshold=20) == "ok"
    assert resource_tone(value=20, critical_threshold=20) == "low"
    assert resource_tone(value=10, critical_threshold=20) == "critical"


def test_first_view_has_ui_fields_and_no_last_turn(colony: ColonyState) -> None:
    tracker = TurnViewTracker()
    frames = _turn_frames(colony, _event("e1", "Coil Cracks"), day=0)
    tracker.absorb(frames)
    view = tracker.build(turn=1, colony=colony, turn_frames=frames, proposals=_proposals(colony))

    assert view["turn"] == 1
    assert view["day"] == 0
    assert view["max_turns"] == colony.max_turns
    assert view["setting_name"] == "Vorgrad Station"
    assert view["last_turn"] is None
    assert view["event"]["severity"] == 3
    assert view["event_log"] == [{"turn": 0, "text": "coil cracks"}]
    assert view["proposals"][0]["effects_preview"] == ["-10 fuel"]
    res = view["dashboard"]["resources"]
    assert {"id", "name", "value", "max", "critical_threshold"} <= set(res[0])
    assert all("status_label" in a for a in view["dashboard"]["advisors"])
    # The live UI never renders player health.
    assert "player_health" not in view["dashboard"]


def test_second_view_reports_last_turn_outcome(colony: ColonyState) -> None:
    tracker = TurnViewTracker()
    f1 = _turn_frames(colony, _event("e1", "Coil Cracks"), day=0)
    tracker.absorb(f1)
    tracker.build(turn=1, colony=colony, turn_frames=f1, proposals=_proposals(colony))
    tracker.record_choice(_proposals(colony)[0])

    dead = colony.agents[2]
    fuel = colony.resources["fuel"]
    after = colony.model_copy(
        update={
            "legitimacy": colony.legitimacy - 4,
            "resources": {
                **colony.resources,
                "fuel": fuel.model_copy(update={"value": fuel.value - 10}),
            },
            "agents": [
                a.model_copy(update={"is_alive": False}) if a.id == dead.id else a
                for a in colony.agents
            ],
        }
    )
    decide_frames = [
        {"event": "decision_applied", "data": {"chosen_proposal_id": "p1", "turn_number": 0}},
        {"event": "death", "data": {"agent_id": str(dead.id), "agent_name": dead.name, "turn": 0}},
        {"event": "narration", "data": {"text": "The heat died at midnight."}},
        *_turn_frames(after, _event("e2", "Storm"), day=1),
    ]
    tracker.absorb(decide_frames)
    f2 = _turn_frames(after, _event("e2", "Storm"), day=1)
    tracker.absorb(f2)  # /turn short-circuit re-emits; must not duplicate log entries
    view = tracker.build(turn=2, colony=after, turn_frames=f2, proposals=_proposals(after))

    last = view["last_turn"]
    assert last["chosen"] == {
        "name": "Cut heat",
        "flavor": "DOMINANT",
        "effects_preview": ["-10 fuel"],
        "risk_note": None,
    }
    assert last["narration"] == "The heat died at midnight."
    assert last["deaths"] == [dead.name]
    assert last["legitimacy_delta"] == -4
    assert last["resource_deltas"] == {fuel.name: -10}
    assert {"name": dead.name, "from": "STEADY", "to": "KIA"} in last["status_changes"]
    assert view["event_log"] == [
        {"turn": 0, "text": "coil cracks"},
        {"turn": 0, "text": f"{dead.name} died."},
        {"turn": 1, "text": "storm"},
    ]


def test_view_carries_close_preview_and_risk_note(colony: ColonyState) -> None:
    tracker = TurnViewTracker()
    props = _proposals(colony)
    props[0]["risk_note"] = "Stores may not last."
    frames = [
        {
            "event": "proposals_ready",
            "data": {
                "turn_number": 0,
                "current_event": _event("e1", "Coil Cracks"),
                "proposals": props,
                "close_preview": ["−1 legitimacy at day's end"],
            },
        }
    ]
    tracker.absorb(frames)
    view = tracker.build(turn=1, colony=colony, turn_frames=frames, proposals=props)
    assert view["close_preview"] == ["−1 legitimacy at day's end"]
    tracker.record_choice(props[0])
    assert tracker._last_choice["risk_note"] == "Stores may not last."
