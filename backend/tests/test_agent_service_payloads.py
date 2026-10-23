from app.agent.service import _proposal_public, _proposals_ready_payload
from app.game.schemas import Proposal, ProposalFlavor


def _p(note: str | None) -> Proposal:
    return Proposal(
        id="p",
        proposed_by_agent_id=None,
        flavor=ProposalFlavor.MORAL,
        name="n",
        description="d",
        effects_preview=["fuel −5"],
        tool_calls_on_pick=[],
        risk_note=note,
    )


def test_proposal_public_carries_risk_note() -> None:
    assert _proposal_public(_p("Stores may not last."))["risk_note"] == "Stores may not last."
    assert _proposal_public(_p(None))["risk_note"] is None


def test_proposals_ready_payload_has_close_preview() -> None:
    payload = _proposals_ready_payload({"colony_state": None, "proposals": [_p(None)]})
    assert payload["close_preview"] == ["−1 legitimacy at day's end"]


def _frame_data(frame: str) -> dict:
    import json

    line = next(ln for ln in frame.splitlines() if ln.startswith("data:"))
    return json.loads(line[len("data:") :])


def test_turn_advanced_carries_resources_and_agents() -> None:
    """The dashboard's resource bars and advisor cards update from this frame;
    without these fields they froze at the run's starting values."""
    from app.agent.service import _translate_update
    from tests._factories import _agent, _colony, _resource

    agent = _agent()
    colony = _colony(
        agents=[agent.model_copy(update={"grievance": 6, "health": 72})],
        resources={"fuel": _resource(value=7)},
    )
    frames = _translate_update(
        "advance_turn", {"colony_state": colony}, pre_alive={}, agents_by_id={}
    )
    data = _frame_data(frames[0])
    assert data["resources"] == [
        {
            "id": "fuel",
            "name": data["resources"][0]["name"],
            "icon": data["resources"][0]["icon"],
            "value": 7,
            "max": 100,
            "critical_threshold": 20,
        }
    ]
    assert data["agents"][0]["grievance"] == 6
    assert data["agents"][0]["health"] == 72
    assert data["agents"][0]["id"] == str(agent.id)
    assert "ethics" not in data["agents"][0]  # private axes stay hidden
