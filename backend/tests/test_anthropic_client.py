"""Unit tests for `cost_cents_of`.

We do not exercise `call_fast` / `call_narration` here — those are trivial
pass-throughs to `client.messages.create`, and respx-mocking is deferred
to the agent-debate node tests which exercise them end-to-end.

Test data shape note (load-bearing): Anthropic's `usage.input_tokens`
already EXCLUDES tokens served from cache. So a call that read 4650
tokens from cache and 200 tokens of fresh input has `input_tokens=200`
and `cache_read_input_tokens=4650` — NOT `input_tokens=4850`. The cost
function relies on this; tests must use the SDK's actual shape.

Earlier versions of these tests used the inverted shape (input_tokens =
total) which made the buggy formula `fresh = input - cache_read - cache_creation`
look correct in isolation. The formula and tests were both wrong, in
the same way, so they passed together. Calibration against an Anthropic
dashboard delta on 2026-04-24 caught the discrepancy. See PLAN.md
"Errors & Learnings" Phase 4 for details.
"""

from typing import Any
from unittest.mock import MagicMock

import pytest

from app.agent import anthropic_client
from app.agent.anthropic_client import cost_cents_of, pricing_for, usage_record
from app.agent.constants import (
    MODEL_DEEPSEEK_V4_1_FLASH,
    MODEL_HAIKU,
    MODEL_PRICING,
    MODEL_SONNET,
    ModelPricing,
)


def _usage(
    *,
    input_tokens: int,
    output_tokens: int,
    cache_read: int | None = None,
    cache_creation: int | None = None,
    extra: dict[str, Any] | None = None,
) -> MagicMock:
    """Build a usage mock that mimics the SDK's `usage` object.

    Pass `None` for a cache field to simulate an entirely absent attribute
    (what the SDK does when caching wasn't used at all on the call).
    """
    spec = ["input_tokens", "output_tokens"]
    if cache_read is not None:
        spec.append("cache_read_input_tokens")
    if cache_creation is not None:
        spec.append("cache_creation_input_tokens")
    if extra is not None:
        spec.append("model_extra")
    u = MagicMock(spec=spec)
    if extra is not None:
        u.model_extra = extra
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


# ---------------------------------------------------------------------------
# Haiku — basic token-bucket coverage.
# ---------------------------------------------------------------------------


def test_haiku_fresh_input_only():
    """10k fresh Haiku input, no output, no cache → 1.0 cent."""
    r = _response(_usage(input_tokens=10_000, output_tokens=0))
    assert cost_cents_of(r, MODEL_HAIKU) == pytest.approx(1.0, abs=0.01)


def test_haiku_all_cache_read():
    """A call where everything is served from cache.

    SDK shape: input_tokens=0 (nothing fresh), cache_read_input_tokens=10_000.
    Cost = 10_000 * $0.10 / MTok = 0.1 cent.
    """
    r = _response(
        _usage(
            input_tokens=0,
            output_tokens=0,
            cache_read=10_000,
            cache_creation=0,
        )
    )
    assert cost_cents_of(r, MODEL_HAIKU) == pytest.approx(0.1, abs=0.01)


def test_haiku_split_cache_read_and_fresh():
    """5k fresh + 5k cache_read.

    SDK shape: input_tokens=5_000 (the fresh portion), cache_read=5_000.
    Cost = 5_000 * $1 / MTok + 5_000 * $0.10 / MTok = 0.5 + 0.05 = 0.55 cents.
    """
    r = _response(
        _usage(
            input_tokens=5_000,
            output_tokens=0,
            cache_read=5_000,
            cache_creation=0,
        )
    )
    assert cost_cents_of(r, MODEL_HAIKU) == pytest.approx(0.55, abs=0.01)


def test_haiku_cache_creation_priced_above_fresh_input():
    """Cache writes carry a 25% premium over fresh input.

    SDK shape: input_tokens=200 (small fresh), cache_creation=4_000.
    Cost = 200 * $1 / MTok + 4_000 * $1.25 / MTok = 0.02 + 0.50 = 0.52 cents.
    """
    r = _response(
        _usage(
            input_tokens=200,
            output_tokens=0,
            cache_read=0,
            cache_creation=4_000,
        )
    )
    assert cost_cents_of(r, MODEL_HAIKU) == pytest.approx(0.52, abs=0.01)


def test_haiku_output_only():
    """0 input, 1k Haiku output → 0.5 cent."""
    r = _response(_usage(input_tokens=0, output_tokens=1_000))
    assert cost_cents_of(r, MODEL_HAIKU) == pytest.approx(0.5, abs=0.01)


# ---------------------------------------------------------------------------
# Sonnet — basic token-bucket coverage with cache rates.
# ---------------------------------------------------------------------------


def test_sonnet_input_and_output():
    """10k Sonnet input + 1k Sonnet output → 4.5 cents (3.0 + 1.5)."""
    r = _response(_usage(input_tokens=10_000, output_tokens=1_000))
    assert cost_cents_of(r, MODEL_SONNET) == pytest.approx(4.5, abs=0.01)


def test_sonnet_with_cache_read_and_creation():
    """Sonnet narration with cache: small fresh input + cache hit + output.

    SDK shape: input_tokens=109, cache_read=2_284, output_tokens=76 (a
    real call from the 2026-04-24 calibration run).
    Cost = 109 * $3 + 2_284 * $0.30 + 76 * $15, all per MTok.
         = 327 + 685.2 + 1140 cents/MTok-tokens
         = 2152.2 / 1_000_000 = 0.2152 cents.
    """
    r = _response(
        _usage(
            input_tokens=109,
            output_tokens=76,
            cache_read=2_284,
            cache_creation=0,
        )
    )
    assert cost_cents_of(r, MODEL_SONNET) == pytest.approx(0.215, abs=0.005)


# ---------------------------------------------------------------------------
# Regression: the prior buggy formula produced negative costs and
# under-counted by ~65% on cached calls. Pin the corrected behaviour
# against exact shapes captured during the 2026-04-24 calibration run.
# ---------------------------------------------------------------------------


def test_haiku_calibration_shape_cache_write():
    """First debate call of the run — the only Haiku cache_creation.

    Shape: input_tokens=913, cache_creation=4_650, cache_read=0,
    output_tokens=823. Real billed delta on the dashboard was 1.084¢.
    Cost = 913 * 100 + 4_650 * 125 + 823 * 500 = 91_300 + 581_250 + 411_500
         = 1_084_050 / 1_000_000 = 1.084¢.
    """
    r = _response(
        _usage(
            input_tokens=913,
            output_tokens=823,
            cache_read=0,
            cache_creation=4_650,
        )
    )
    assert cost_cents_of(r, MODEL_HAIKU) == pytest.approx(1.084, abs=0.005)


def test_haiku_calibration_shape_cache_hit():
    """Subsequent debate call — cache hits, no creation.

    Shape: input_tokens=1_085, cache_read=4_650, output_tokens=786.
    Cost = 1_085 * 100 + 4_650 * 10 + 786 * 500
         = 108_500 + 46_500 + 393_000 = 548_000 / 1_000_000 = 0.548¢.
    """
    r = _response(
        _usage(
            input_tokens=1_085,
            output_tokens=786,
            cache_read=4_650,
            cache_creation=0,
        )
    )
    assert cost_cents_of(r, MODEL_HAIKU) == pytest.approx(0.548, abs=0.005)


def test_sonnet_cached_call_is_never_negative():
    """Regression for the prior bug: small input_tokens + large cache_read
    used to compute fresh = -negative under the old formula, producing a
    spurious negative cost. With the corrected formula the result must be
    strictly positive.

    Shape mirrors the calibration run's per-turn Sonnet cache hit:
    input_tokens=120, cache_read=2_284, output_tokens=82.
    """
    r = _response(
        _usage(
            input_tokens=120,
            output_tokens=82,
            cache_read=2_284,
            cache_creation=0,
        )
    )
    cost = cost_cents_of(r, MODEL_SONNET)
    assert cost > 0, f"Sonnet cached call cost must be positive, got {cost}"
    # Exact: 120*300 + 2284*30 + 82*1500 = 36_000 + 68_520 + 123_000 = 227_520
    # → 0.22752¢.
    assert cost == pytest.approx(0.228, abs=0.005)


# ---------------------------------------------------------------------------
# Edge cases.
# ---------------------------------------------------------------------------


def test_unknown_model_raises():
    r = _response(_usage(input_tokens=100, output_tokens=10))
    with pytest.raises(ValueError, match="Unknown model"):
        cost_cents_of(r, "claude-opus-nonexistent")


def test_missing_cache_attributes_treated_as_zero():
    """When caching wasn't used at all, `usage` has no `cache_*` attributes.

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


# ---------------------------------------------------------------------------
# Pricing table (model id -> rates) + env overrides.
# ---------------------------------------------------------------------------


def test_pricing_table_covers_default_and_openrouter_models():
    for model in (MODEL_HAIKU, MODEL_SONNET, MODEL_DEEPSEEK_V4_1_FLASH):
        assert model in MODEL_PRICING


def test_deepseek_v4_1_flash_four_bucket_math():
    """OpenRouter list price 2026-10-01: $0.027/MTok prompt, $0.60/MTok
    completion, $0.027/MTok cache read; no cache-write price published, so
    cache writes bill at the prompt rate."""
    r = _response(
        _usage(
            input_tokens=200,
            output_tokens=1_000,
            cache_read=4_650,
            cache_creation=100,
        )
    )
    expected = (200 * 2.7 + 4_650 * 2.7 + 100 * 2.7 + 1_000 * 60) / 1_000_000
    assert cost_cents_of(r, MODEL_DEEPSEEK_V4_1_FLASH) == pytest.approx(expected)


def test_pricing_override_from_settings(monkeypatch):
    monkeypatch.setattr(
        anthropic_client.settings,
        "LLM_PRICING_OVERRIDES",
        {"vendor/new-model": {"input": 10, "cache_write": 20, "cache_read": 1, "output": 50}},
    )
    assert pricing_for("vendor/new-model") == ModelPricing(
        input=10, cache_write=20, cache_read=1, output=50
    )
    r = _response(_usage(input_tokens=100_000, output_tokens=10_000))
    # 100k * 10 + 10k * 50 = 1.5M cent-tokens / 1e6 = 1.5 cents.
    assert cost_cents_of(r, "vendor/new-model") == pytest.approx(1.5)


def test_pricing_override_replaces_builtin(monkeypatch):
    monkeypatch.setattr(
        anthropic_client.settings,
        "LLM_PRICING_OVERRIDES",
        {MODEL_HAIKU: {"input": 0, "cache_write": 0, "cache_read": 0, "output": 1000}},
    )
    r = _response(_usage(input_tokens=10_000, output_tokens=1_000))
    assert cost_cents_of(r, MODEL_HAIKU) == pytest.approx(1.0)


def test_malformed_pricing_override_raises_value_error(monkeypatch):
    monkeypatch.setattr(
        anthropic_client.settings,
        "LLM_PRICING_OVERRIDES",
        {"vendor/bad": {"input": 1}},
    )
    with pytest.raises(ValueError, match="vendor/bad"):
        pricing_for("vendor/bad")


def test_cost_prefers_billed_cost_when_provider_reports_it():
    usage = _usage(input_tokens=1000, output_tokens=500, extra={"cost": 0.0033})
    assert cost_cents_of(_response(usage), "deepseek/deepseek-v4-pro-0813") == pytest.approx(0.33)


def test_cost_falls_back_to_price_table_without_billed_cost():
    usage = _usage(input_tokens=1_000_000, output_tokens=0, extra={})
    assert cost_cents_of(_response(usage), "xiaomi/mimo-v2.6-flash") == pytest.approx(14.0)


def test_usage_record_fields():
    usage = _usage(input_tokens=100, output_tokens=50, cache_read=4000, extra={"cost": 0.001})
    rec = usage_record(_response(usage), "openai/gpt-6-luna", "debate")
    assert rec == {
        "purpose": "debate",
        "model": "openai/gpt-6-luna",
        "input_tokens": 100,
        "cache_read_tokens": 4000,
        "cache_write_tokens": 0,
        "output_tokens": 50,
        "cost_cents": pytest.approx(0.1),
        "billed": True,
    }


@pytest.mark.parametrize(
    "model",
    ["xiaomi/mimo-v2.6-flash", "deepseek/deepseek-v4.1-flash-1017", "openai/gpt-6-luna"],
)
def test_new_models_have_prices(model):
    assert pricing_for(model) is not None
