"""Anthropic SDK wrapper + typed cost accounting.

Phase 4 foundation — every LLM call in the agent graph goes through
`call_haiku` or `call_sonnet`. Cost math is centralised in `cost_cents_of`
so budget rails (MAX_COST_CENTS_PER_TURN / _RUN) can be enforced uniformly.

Direct `AsyncAnthropic` only. Do NOT wrap via LangChain's `ChatAnthropic` —
we need raw control over `cache_control`, tool schemas, and `usage` fields.
"""

from typing import Any

from anthropic import AsyncAnthropic

from app.config import settings

from .constants import (
    CENTS_PER_MTOK_HAIKU_CACHE_READ,
    CENTS_PER_MTOK_HAIKU_CACHE_WRITE,
    CENTS_PER_MTOK_HAIKU_INPUT,
    CENTS_PER_MTOK_HAIKU_OUTPUT,
    CENTS_PER_MTOK_SONNET_INPUT,
    CENTS_PER_MTOK_SONNET_OUTPUT,
    MODEL_HAIKU,
    MODEL_SONNET,
)

# One shared async client for the whole process — the SDK handles pooling.
client = AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)


async def call_haiku(
    system_blocks: list,
    user_message: str,
    tools: list | None = None,
    max_tokens: int = 2000,
) -> Any:
    """Invoke Haiku with the provided system blocks and user message.

    `system_blocks` is a list of content blocks (not a plain string) so the
    caller can attach `cache_control: {"type": "ephemeral"}` to personality
    and colony-state blocks. See ARCHITECTURE §3.5 for the cache layout.
    """
    kwargs: dict[str, Any] = {
        "model": MODEL_HAIKU,
        "max_tokens": max_tokens,
        "system": system_blocks,
        "messages": [{"role": "user", "content": user_message}],
    }
    if tools is not None:
        kwargs["tools"] = tools
    return await client.messages.create(**kwargs)


async def call_sonnet(
    system_blocks: list,
    user_message: str,
    max_tokens: int = 1000,
) -> Any:
    """Invoke Sonnet for narration. No tools — narration is freeform prose."""
    return await client.messages.create(
        model=MODEL_SONNET,
        max_tokens=max_tokens,
        system=system_blocks,
        messages=[{"role": "user", "content": user_message}],
    )


def cost_cents_of(response: Any, model: str) -> float:
    """Compute the cost of a completed `messages.create` call in cents.

    Handles all four Anthropic token buckets:
      * `cache_read_input_tokens`     — cache hit, cheapest.
      * `cache_creation_input_tokens` — cache write, priced above fresh input.
      * `input_tokens - cache_read - cache_creation` — fresh (uncached) input.
      * `output_tokens`               — completion tokens.

    The cache fields are optional on `usage` — absent entirely (not `None`)
    when caching was not used on the call. `getattr(..., 0)` handles both
    "attribute missing" and "attribute present but zero" uniformly.
    """
    u = response.usage
    input_tokens: int = u.input_tokens
    output_tokens: int = u.output_tokens
    cache_read: int = getattr(u, "cache_read_input_tokens", 0) or 0
    cache_creation: int = getattr(u, "cache_creation_input_tokens", 0) or 0
    fresh_input = input_tokens - cache_read - cache_creation

    if model == MODEL_HAIKU:
        cents = (
            fresh_input * CENTS_PER_MTOK_HAIKU_INPUT
            + cache_read * CENTS_PER_MTOK_HAIKU_CACHE_READ
            + cache_creation * CENTS_PER_MTOK_HAIKU_CACHE_WRITE
            + output_tokens * CENTS_PER_MTOK_HAIKU_OUTPUT
        )
        return cents / 1_000_000

    if model == MODEL_SONNET:
        # Cache not used for narration in our plan, but account for it
        # defensively in case that ever changes — fresh_input already
        # subtracts any cache tokens, so we just apply the input rate.
        cents = (
            fresh_input * CENTS_PER_MTOK_SONNET_INPUT + output_tokens * CENTS_PER_MTOK_SONNET_OUTPUT
        )
        return cents / 1_000_000

    raise ValueError(f"Unknown model for cost accounting: {model!r}")
