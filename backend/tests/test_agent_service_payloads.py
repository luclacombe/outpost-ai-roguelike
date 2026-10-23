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
