"""Internal helpers shared by Haiku-as-judge metrics.

Private — names start with underscore. Only `personality.py` and `coherence.py`
should import from here. If a third judge module ever needs the same helpers,
fold the duplication out then; don't promote prematurely.
"""

from __future__ import annotations

import json
from typing import Any


def _extract_text(response: Any) -> str:
    """Pull the first text block out of an Anthropic SDK message response."""
    for block in getattr(response, "content", []) or []:
        if getattr(block, "type", None) == "text":
            return getattr(block, "text", "") or ""
    return ""


def _strip_code_fence(raw: str) -> str:
    """Strip a leading ```json fence (and trailing fence) if Haiku adds one."""
    if raw.startswith("```"):
        raw = raw.strip("`")
        if "\n" in raw:
            raw = raw.split("\n", 1)[1]
    return raw


def _parse_judge_json(raw: str) -> tuple[float | None, str, str | None]:
    """Parse a judge response. Returns (score, rationale, error_msg).

    On success: (score, rationale, None) — score is the raw judge value, NOT
    yet clamped or snapped. On failure: (None, "", "judge response unparseable: <err>").
    """
    text = _strip_code_fence(raw.strip())
    try:
        parsed = json.loads(text)
        score = float(parsed["score"])
        rationale = str(parsed.get("reasoning", "")).strip() or "no rationale provided"
        return score, rationale, None
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as err:
        return None, "", f"judge response unparseable: {err}"
