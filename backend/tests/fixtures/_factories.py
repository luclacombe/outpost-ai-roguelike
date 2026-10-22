"""Shared respx factories for eval-pipeline tests.

Factored out of `tests/eval/test_eval_pipeline.py` so the run-simulator
hardening tests can reuse the same Haiku/Sonnet body shape without
duplicating call-routing logic.
"""

from __future__ import annotations

import json
from typing import Any

from httpx import Response

from tests.conftest import fake_haiku_response, fake_sonnet_response


def _tool_choice(payload: str) -> str:
    """The request's `tool_choice` re-serialised, or "" when absent or unparsable."""
    try:
        return json.dumps(json.loads(payload).get("tool_choice") or "")
    except ValueError:
        return ""


def make_haiku_debate_side_effect():
    """Return a respx side_effect callable that emits debate / autonomous /
    Sonnet bodies based on the request body shape.

    The debate body is one `submit_debate` tool_use: three options (DOMINANT,
    MORAL, WILDCARD) plus one reaction per advisor. Each option carries a
    resource effect so picking it actually moves a resource — without this,
    simulate_game runs would have a frozen dashboard regardless of policy.
    Autonomous calls return a `submit_offturn` with no actions and Sonnet
    narration returns a short-prose body.

    Resource ids are scoped to the arctic setting (fuel, food); tests
    using other settings should pass their own factory or extend this
    one. Deltas are modest so a 3-turn run never underflows.

    Importable so test modules don't duplicate it.
    """

    # One effect per flavor. Picked by SimulatedPlayer policy:
    #   "pragmatic" → DOMINANT (fuel -5)
    #   "moral"     → MORAL    (food +3)
    #   "random"    → any of the three
    _effect_per_flavor = {
        "DOMINANT": {"kind": "resource", "resource": "fuel", "delta": -5},
        "MORAL": {"kind": "resource", "resource": "food", "delta": 3},
        "WILDCARD": {"kind": "resource", "resource": "fuel", "delta": -8},
    }

    def _make_debate_body() -> dict[str, Any]:
        flavors = ["DOMINANT", "MORAL", "WILDCARD"]
        options = [
            {
                "flavor": flavors[i],
                "advisor": f"Advisor{i + 1}",
                "title": f"Plan {i}",
                "description": f"Option {i}.",
                "effects": [_effect_per_flavor[flavors[i]]],
            }
            for i in range(3)
        ]
        reactions = [
            {"advisor": "Advisor1", "line": "Steady on."},
            {"advisor": "Advisor2", "line": "We must act."},
            {"advisor": "Advisor3", "line": "Risky."},
            {"advisor": "Advisor4", "line": "I object."},
        ]
        return fake_haiku_response(
            tool_uses=[
                {"name": "submit_debate", "input": {"reactions": reactions, "options": options}}
            ]
        )

    def _make_autonomous_body() -> dict[str, Any]:
        return fake_haiku_response(tool_uses=[{"name": "submit_offturn", "input": {"actions": []}}])

    def _side_effect(request) -> Response:
        try:
            body = request.read()
            payload = body.decode("utf-8") if isinstance(body, bytes) else body
        except Exception:  # noqa: BLE001
            payload = ""

        if '"claude-sonnet' in payload:
            return Response(200, json=fake_sonnet_response("The colony endures."))

        # The debate and autonomous calls each force their own tool.
        if '"name": "submit_debate"' in _tool_choice(payload):
            return Response(200, json=_make_debate_body())
        return Response(200, json=_make_autonomous_body())

    return _side_effect


__all__ = ["make_haiku_debate_side_effect"]
