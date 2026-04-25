"""Tool-Call Validity Rate metric.

Pure-Python, $0 LLM. The engine stamps `rejection_reason` on every
invalid tool call (see `app/game/CLAUDE.md` invariant #11), so the metric
is just a fraction over the supplied list.

Threshold (root CLAUDE.md invariant #10): 0.85 — runs/PRs below this fail
the merge gate.
"""

from __future__ import annotations

from collections import Counter

from app.game.schemas import ToolCall

TOOL_VALIDITY_THRESHOLD = 0.85
"""Minimum acceptable fraction of valid tool calls (see ARCHITECTURE §12)."""


def tool_validity_rate(tool_calls: list[ToolCall]) -> float:
    """Fraction of tool calls with `rejection_reason is None`.

    An empty list returns 1.0 (vacuously valid — nothing to reject).
    """
    if not tool_calls:
        return 1.0
    valid = sum(1 for tc in tool_calls if tc.rejection_reason is None)
    return valid / len(tool_calls)


def tool_validity_breakdown(tool_calls: list[ToolCall]) -> dict[str, int]:
    """Count rejected tool calls by `rejection_reason`.

    Only invalid calls are counted; valid calls are ignored. Useful for
    triage — surfaces which validator branch is firing most often.
    """
    return dict(
        Counter(tc.rejection_reason for tc in tool_calls if tc.rejection_reason is not None)
    )
