"""Unit tests for `cost_cents_of`.

We do not exercise `call_haiku` / `call_sonnet` here — those are trivial
pass-throughs to `client.messages.create`, and respx-mocking is deferred
to Task 4.4 where the debate node exercises them end-to-end.
"""

from unittest.mock import MagicMock

import pytest

from app.agent.anthropic_client import cost_cents_of
from app.agent.constants import MODEL_HAIKU, MODEL_SONNET


def _usage(
    *,
    input_tokens: int,
    output_tokens: int,
    cache_read: int | None = None,
    cache_creation: int | None = None,
) -> MagicMock:
    """Build a usage mock. Pass `None` for a cache field to simulate
    an entirely absent attribute (what happens when caching is off)."""
    spec = ["input_tokens", "output_tokens"]
    if cache_read is not None:
        spec.append("cache_read_input_tokens")
    if cache_creation is not None:
        spec.append("cache_creation_input_tokens")
    u = MagicMock(spec=spec)
    u.input_tokens = input_tokens
    u.output_tokens = output_tokens
    if cache_read is not None:
        u.cache_read_input_tokens = cache_read
    if cache_creation is not None:
        u.cache_creation_input_tokens = cache_creation
    return u


def _response(usage: MagicMock) -> MagicMock:
    r = MagicMock()
    r.usage = usage
    return r


def test_haiku_fresh_input_only():
    """10k fresh Haiku input, no output, no cache → 1.0 cents."""
    r = _response(_usage(input_tokens=10_000, output_tokens=0))
    assert cost_cents_of(r, MODEL_HAIKU) == pytest.approx(1.0, abs=0.01)


def test_haiku_all_cache_read():
    """10k Haiku input, entirely cache-read → 0.1 cents."""
    r = _response(
        _usage(
            input_tokens=10_000,
            output_tokens=0,
            cache_read=10_000,
            cache_creation=0,
        )
    )
    assert cost_cents_of(r, MODEL_HAIKU) == pytest.approx(0.1, abs=0.01)


def test_haiku_split_cache_read_and_fresh():
    """5k fresh + 5k cache_read, 0 cache_creation → 0.55 cents."""
    r = _response(
        _usage(
            input_tokens=10_000,
            output_tokens=0,
            cache_read=5_000,
            cache_creation=0,
        )
    )
    assert cost_cents_of(r, MODEL_HAIKU) == pytest.approx(0.55, abs=0.01)


def test_haiku_output_only():
    """0 input, 1k Haiku output → 0.5 cents."""
    r = _response(_usage(input_tokens=0, output_tokens=1_000))
    assert cost_cents_of(r, MODEL_HAIKU) == pytest.approx(0.5, abs=0.01)


def test_sonnet_input_and_output():
    """10k Sonnet input + 1k Sonnet output → 4.5 cents (3.0 + 1.5)."""
    r = _response(_usage(input_tokens=10_000, output_tokens=1_000))
    assert cost_cents_of(r, MODEL_SONNET) == pytest.approx(4.5, abs=0.01)


def test_unknown_model_raises():
    r = _response(_usage(input_tokens=100, output_tokens=10))
    with pytest.raises(ValueError, match="Unknown model"):
        cost_cents_of(r, "claude-opus-nonexistent")


def test_missing_cache_attributes_treated_as_zero():
    """When caching wasn't used, `usage` has no cache_* attributes at all.

    `spec` limits the MagicMock's attribute surface, so accessing the
    cache fields would AttributeError on a plain MagicMock — `cost_cents_of`
    must fall back via `getattr(..., 0)`.
    """
    u = _usage(input_tokens=10_000, output_tokens=0)  # no cache fields
    # Sanity: the mock truly lacks the attributes.
    assert not hasattr(u, "cache_read_input_tokens")
    assert not hasattr(u, "cache_creation_input_tokens")
    r = _response(u)
    # Same as fresh-only: 10k * 100 / 1e6 = 1.0 cent.
    assert cost_cents_of(r, MODEL_HAIKU) == pytest.approx(1.0, abs=0.01)
