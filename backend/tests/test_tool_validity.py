from app.eval.tool_validity import tool_validity_by_phase, tool_validity_rate
from app.game.schemas import ToolCall


def _tc(phase: str | None, rejected: bool = False) -> ToolCall:
    return ToolCall(
        name="modify_resource",
        caller_id=None,
        args={},
        phase=phase,
        rejection_reason="stripped:unknown_resource" if rejected else None,
    )


def test_rate_counts_llm_phases_only() -> None:
    calls = [
        _tc("debate"),
        _tc("prepare", rejected=True),
        _tc("prepare"),
        _tc("autonomy"),
        _tc("resolve"),
        _tc("close"),
    ]
    assert tool_validity_rate(calls) == 3 / 4


def test_legacy_calls_without_phase_still_count() -> None:
    assert tool_validity_rate([_tc(None), _tc(None, rejected=True)]) == 0.5


def test_by_phase_breakdown() -> None:
    out = tool_validity_by_phase([_tc("prepare", rejected=True), _tc("prepare"), _tc("close")])
    assert out == {"prepare": {"valid": 1, "rejected": 1}, "close": {"valid": 1, "rejected": 0}}
