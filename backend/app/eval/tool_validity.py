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


LLM_PHASES: frozenset[str] = frozenset({"debate", "prepare", "autonomy"})
"""Phases whose calls the LLM authored. `resolve` and `close` calls are
engine-derived and valid by construction; counting them would dilute the
gate. Calls with phase None predate phase tags and still count."""


def _counted(tc: ToolCall) -> bool:
    return tc.phase is None or tc.phase in LLM_PHASES


def tool_validity_rate(tool_calls: list[ToolCall]) -> float:
    """Fraction of LLM-originated calls with `rejection_reason is None`.

    An empty (or engine-only) list returns 1.0 (vacuously valid).
    """
    counted = [tc for tc in tool_calls if _counted(tc)]
    if not counted:
        return 1.0
    return sum(1 for tc in counted if tc.rejection_reason is None) / len(counted)


def tool_validity_by_phase(tool_calls: list[ToolCall]) -> dict[str, dict[str, int]]:
    """{phase: {"valid": n, "rejected": m}} over every call, engine phases included."""
    out: dict[str, dict[str, int]] = {}
    for tc in tool_calls:
        bucket = out.setdefault(tc.phase or "unknown", {"valid": 0, "rejected": 0})
        bucket["rejected" if tc.rejection_reason else "valid"] += 1
    return out


def tool_validity_breakdown(tool_calls: list[ToolCall]) -> dict[str, int]:
    """Count rejected tool calls by `rejection_reason`.

    Only invalid calls are counted; valid calls are ignored. Useful for
    triage — surfaces which validator branch is firing most often.
    """
    return dict(
        Counter(tc.rejection_reason for tc in tool_calls if tc.rejection_reason is not None)
    )
