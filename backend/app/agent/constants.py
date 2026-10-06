"""Pure constants for the agent layer.

Model IDs, pricing (cents per million tokens), and budget rails. No imports
from app internals — this module is the root of the agent dependency graph.

The models actually called are chosen by `settings.LLM_MODEL_FAST` /
`settings.LLM_MODEL_NARRATION`; the IDs below are the defaults plus every
model we have a price for.
"""

from dataclasses import dataclass

# Model IDs. Haiku 4.5 publishes a dated snapshot; Sonnet 4.6 and Opus 4.7
# do not — per Anthropic's models overview (platform.claude.com/docs/en/about-
# claude/models/overview, verified 2026-04-24), the "Claude API ID" for
# claude-sonnet-4-6 is the alias itself. Only Haiku 4.5 has a dated form.
# If Anthropic publishes a dated Sonnet 4.6 later, swap it in here.
MODEL_HAIKU = "claude-haiku-4-5-20251001"
MODEL_SONNET = "claude-sonnet-4-6"
# OpenRouter model slug (Anthropic-compatible Messages endpoint).
MODEL_DEEPSEEK_V4_1_FLASH = "deepseek/deepseek-v4.1-flash"
MODEL_DEEPSEEK_V4_PRO = "deepseek/deepseek-v4-pro-0813"


@dataclass(frozen=True)
class ModelPricing:
    """Cents per million tokens for each of the four `usage` buckets."""

    input: float  # fresh (non-cached) input
    cache_write: float  # cache_creation_input_tokens
    cache_read: float  # cache_read_input_tokens
    output: float


# Verify against provider pricing pages periodically. Unknown models raise in
# `cost_cents_of`; add an entry here or via settings.LLM_PRICING_OVERRIDES.
MODEL_PRICING: dict[str, ModelPricing] = {
    # Anthropic, as of Apr 2026. Cache write = input x 1.25 (5-min ephemeral),
    # cache read = input x 0.10.
    MODEL_HAIKU: ModelPricing(input=100, cache_write=125, cache_read=10, output=500),
    MODEL_SONNET: ModelPricing(input=300, cache_write=375, cache_read=30, output=1500),
    # OpenRouter /api/v1/models, fetched 2026-10-01: prompt $0.027/MTok,
    # completion $0.60/MTok, input_cache_read $0.027/MTok. No
    # input_cache_write price is published, so cache writes bill at the
    # prompt rate.
    MODEL_DEEPSEEK_V4_1_FLASH: ModelPricing(input=2.7, cache_write=2.7, cache_read=2.7, output=60),
    # OpenRouter, fetched 2026-10-06. Pinned via LLM_PROVIDER_ORDER to
    # StreamLake ($0.66 in / $1.98 out per MTok) with Novita as fallback
    # ($0.99 / $2.97). The table uses Novita's rates as the ceiling so the
    # budget rails never under-count. Cache discounts are not assumed:
    # cache reads and writes bill at the input rate.
    MODEL_DEEPSEEK_V4_PRO: ModelPricing(input=99, cache_write=99, cache_read=99, output=297),
}

# Budget rails per ARCHITECTURE §5.2.
MAX_COST_CENTS_PER_TURN = 15  # ~$0.15; if exceeded, skip Sonnet narration.
MAX_COST_CENTS_PER_RUN = 40  # ~$0.40 hard run cap.
