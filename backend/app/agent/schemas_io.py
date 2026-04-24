"""HTTP request + SSE event-envelope shapes for the /turn and /decide routes.

The SSE stream is plain `StreamingResponse` + an `async def event_generator`
— no dedicated SSE library. Framing is:

    event: <event_name>
    data: <JSON-encoded payload>

(blank line between events). The final sentinel frame uses the literal
string `[DONE]` as `data`, not a JSON body.

Content-Type is `text/event-stream` with `Cache-Control: no-cache` and
`X-Accel-Buffering: no` so proxies don't buffer mid-stream.

### Event payloads carried in `data:`

`proposals_ready`
    {"turn_number": int,
     "current_event": Event.model_dump(mode="json"),
     "proposals": [ProposalPublic, ...]}

`decision_applied`
    {"chosen_proposal_id": str, "turn_number": int}

`agent_spoke`
    {"agent_id": str, "text": str}

`autonomous_action`
    {"agent_id": str, "tool_name": str, "args": {...}}   # caller_agent_id stripped

`death`
    {"agent_id": str, "agent_name": str, "turn": int}

`narration`
    {"text": str}

`turn_advanced`
    {"turn_number": int,
     "legitimacy": int,
     "player_health": int,
     "cost_cents": int}

`run_over`
    {"end_state": str, "turn_number": int, "cost_cents": int}

`error`
    {"code": str, "message": str}
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class DecideRequest(BaseModel):
    """POST /game/{game_id}/decide body.

    `extra="forbid"` so a client passing stray keys (e.g. a legacy
    `proposal_id` spelling) fails loudly instead of silently dropping.
    """

    model_config = ConfigDict(extra="forbid")

    chosen_proposal_id: str
