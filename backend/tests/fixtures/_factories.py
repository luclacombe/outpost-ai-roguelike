"""Shared respx factories for eval-pipeline tests.

Factored out of `tests/eval/test_eval_pipeline.py` so the run-simulator
hardening tests can reuse the same Haiku/Sonnet body shape without
duplicating call-routing logic.
"""

from __future__ import annotations

from typing import Any

from httpx import Response

from tests.conftest import fake_haiku_response, fake_sonnet_response


def make_haiku_debate_side_effect():
    """Return a respx side_effect callable that emits debate / autonomous /
    Sonnet bodies based on the request body shape.

    The debate body carries three `propose_action` tool_uses (DOMINANT,
    MORAL, WILDCARD) plus one XML reaction line per advisor. Each proposal
    carries a structured `tool_calls_on_pick` so picking the proposal
    actually moves a resource — without this, simulate_game runs would
    have a frozen dashboard regardless of policy. Autonomous calls return
    an empty body (zero tool_uses) and Sonnet narration returns a short-
    prose body.

    Resource ids are scoped to the arctic setting (fuel, food); tests
    using other settings should pass their own factory or extend this
    one. Deltas are modest so a 3-turn run never underflows.

    Identical to `_haiku_debate_factory` in test_eval_pipeline.py but
    importable so other test modules don't duplicate it.
    """

    # One nested call per flavor. Picked by SimulatedPlayer policy:
    #   "pragmatic" → DOMINANT (fuel -5)
    #   "moral"     → MORAL    (food +3)
    #   "random"    → any of the three
    _nested_per_flavor = {
        "DOMINANT": {
            "name": "modify_resource",
            "args": {"resource_id": "fuel", "delta": -5, "reason": "burn fuel"},
        },
        "MORAL": {
            "name": "modify_resource",
            "args": {"resource_id": "food", "delta": 3, "reason": "share fairly"},
        },
        "WILDCARD": {
            "name": "modify_resource",
            "args": {"resource_id": "fuel", "delta": -8, "reason": "risky play"},
        },
    }

    def _make_debate_body() -> dict[str, Any]:
        xml = (
            '<agent name="Advisor1">Steady on.</agent>'
            '<agent name="Advisor2">We must act.</agent>'
            '<agent name="Advisor3">Risky.</agent>'
            '<agent name="Advisor4">I object.</agent>'
        )
        flavors = ["DOMINANT", "MORAL", "WILDCARD"]
        tool_uses = [
            {
                "id": f"toolu_d_{i}",
                "name": "propose_action",
                "input": {
                    "name": f"Plan {i}",
                    "description": f"Option {i}.",
                    "flavor": flavors[i],
                    "effects_preview": [],
                    "tool_calls_on_pick": [_nested_per_flavor[flavors[i]]],
                },
            }
            for i in range(3)
        ]
        return fake_haiku_response(text_xml=xml, tool_uses=tool_uses)

    def _make_autonomous_body() -> dict[str, Any]:
        return fake_haiku_response(text_xml="")

    def _side_effect(request) -> Response:
        try:
            body = request.read()
            payload = body.decode("utf-8") if isinstance(body, bytes) else body
        except Exception:  # noqa: BLE001
            payload = ""

        if '"claude-sonnet' in payload:
            return Response(200, json=fake_sonnet_response("The colony endures."))

        is_debate = "<event id=" in payload
        if is_debate:
            return Response(200, json=_make_debate_body())
        return Response(200, json=_make_autonomous_body())

    return _side_effect


__all__ = ["make_haiku_debate_side_effect"]
