"""Langfuse switch for harness runs.

Harness and sweep games go through `app/agent/service.py::with_observability`,
which traces whenever both `LANGFUSE_*` keys are set. A 42-game persona run
or a 90-game sweep would spend a large share of the free 50K obs/month.
Both CLIs therefore run with tracing off unless `--trace` is passed.

The override is in-process on the shared `app.config.settings` object (every
module bound that same object at import). Clearing the env var would not
work: config treats blank values as unset and falls back to `.env`.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

import app.config

_KEYS = ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY")


@contextmanager
def tracing_disabled(*, enabled: bool = True) -> Iterator[None]:
    """Blank the Langfuse keys for the block when `enabled`; restore after."""
    if not enabled:
        yield
        return
    s = app.config.settings
    saved = {k: getattr(s, k) for k in _KEYS}
    for k in _KEYS:
        setattr(s, k, None)
    try:
        yield
    finally:
        for k, v in saved.items():
            setattr(s, k, v)
