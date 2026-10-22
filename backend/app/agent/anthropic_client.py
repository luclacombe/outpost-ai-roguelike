"""Anthropic SDK wrapper + typed cost accounting.

Every LLM call in the agent graph goes through `call_fast` (debate +
autonomous) or `call_narration`. Cost math is centralised in `cost_cents_of`
so budget rails (MAX_COST_CENTS_PER_TURN / _RUN) can be enforced uniformly.

Provider is configurable (see `Settings.LLM_*`): Anthropic direct by default,
or any Anthropic-compatible Messages endpoint such as OpenRouter's. The
`anthropic` SDK is used either way.

Direct `AsyncAnthropic` only. Do NOT wrap via LangChain's `ChatAnthropic` —
we need raw control over `cache_control`, tool schemas, and `usage` fields.
"""

from typing import Any

from anthropic import AsyncAnthropic, Omit

from app.config import Settings, settings

from .constants import MODEL_PRICING, ModelPricing


def build_client(s: Settings) -> AsyncAnthropic:
    """Construct the shared async client for the configured provider.

    `x-api-key` mode (Anthropic) sends `X-Api-Key`; `bearer` mode (OpenRouter)
    sends `Authorization: Bearer`. The other header is explicitly omitted:
    the SDK otherwise falls back to `ANTHROPIC_API_KEY` / `ANTHROPIC_AUTH_TOKEN`
    from the OS env and would send both, leaking an Anthropic key to a
    third-party endpoint.
    """
    key = s.llm_api_key
    if s.LLM_AUTH_SCHEME == "bearer":
        return AsyncAnthropic(
            api_key=None,
            auth_token=key,
            base_url=s.LLM_BASE_URL,
            default_headers={"X-Api-Key": Omit()},
        )
    return AsyncAnthropic(
        api_key=key,
        base_url=s.LLM_BASE_URL,
        default_headers={"Authorization": Omit()},
    )


def request_extras(*, fast: bool = False) -> dict[str, Any]:
    """Provider-specific kwargs merged into every `messages.create` call.

    Empty under defaults, so Anthropic requests are byte-identical to the
    pre-provider-config shape. `thinking: {"type": "disabled"}` is the native
    Messages API field; OpenRouter maps it to `reasoning: {enabled: false}`.

    `fast=True` (only `call_fast`) also pins OpenRouter providers when
    `LLM_PROVIDER_ORDER` is set: `extra_body` merges a top-level `provider`
    object into the JSON body, tried in order with no fallback to other hosts.
    Settings validation guarantees the base URL is OpenRouter in that case.
    """
    extras: dict[str, Any] = {}
    if settings.LLM_DISABLE_THINKING:
        extras["thinking"] = {"type": "disabled"}
    if fast and settings.LLM_PROVIDER_ORDER:
        extras["extra_body"] = {
            "provider": {"order": list(settings.LLM_PROVIDER_ORDER), "allow_fallbacks": False}
        }
    return extras


def pricing_for(model: str) -> ModelPricing:
    """Rates for `model`: settings override first, then the built-in table.

    Raises ValueError for unknown models so the budget rails never silently
    compute 0.
    """
    override = settings.LLM_PRICING_OVERRIDES.get(model)
    if override is not None:
        try:
            return ModelPricing(**override)
        except TypeError as exc:
            raise ValueError(
                f"LLM_PRICING_OVERRIDES[{model!r}] needs exactly input, cache_write, "
                f"cache_read, output: {exc}"
            ) from exc
    pricing = MODEL_PRICING.get(model)
    if pricing is None:
        raise ValueError(
            f"Unknown model for cost accounting: {model!r}. Add it to "
            "app.agent.constants.MODEL_PRICING or settings.LLM_PRICING_OVERRIDES."
        )
    return pricing


# Fail loud at startup if a configured model has no price.
pricing_for(settings.LLM_MODEL_FAST)
pricing_for(settings.LLM_MODEL_NARRATION)

# One shared async client for the whole process — the SDK handles pooling.
client = build_client(settings)


async def call_fast(
    system_blocks: list,
    user_message: str,
    tools: list | None = None,
    max_tokens: int = 2000,
    tool_choice: dict[str, Any] | None = None,
) -> Any:
    """Invoke the fast model (`settings.LLM_MODEL_FAST`).

    `tool_choice` (e.g. `{"type": "tool", "name": "submit_debate"}`) is sent
    only when given, so default requests stay byte-identical.

    `system_blocks` is a list of content blocks (not a plain string) so the
    caller can attach `cache_control: {"type": "ephemeral"}` to personality
    and colony-state blocks. See ARCHITECTURE §3.5 for the cache layout.
    """
    kwargs: dict[str, Any] = {
        "model": settings.LLM_MODEL_FAST,
        "max_tokens": max_tokens,
        "system": system_blocks,
        "messages": [{"role": "user", "content": user_message}],
        **request_extras(fast=True),
    }
    if tools is not None:
        kwargs["tools"] = tools
    if tool_choice is not None:
        kwargs["tool_choice"] = tool_choice
    return await client.messages.create(**kwargs)


async def call_narration(
    system_blocks: list,
    user_message: str,
    max_tokens: int = 1000,
) -> Any:
    """Invoke the narration model. No tools — narration is freeform prose."""
    return await client.messages.create(
        model=settings.LLM_MODEL_NARRATION,
        max_tokens=max_tokens,
        system=system_blocks,
        messages=[{"role": "user", "content": user_message}],
        **request_extras(),
    )


def cost_cents_of(response: Any, model: str) -> float:
    """Compute the cost of a completed `messages.create` call in cents.

    Anthropic's `usage` object reports four disjoint token buckets:
      * `input_tokens`                 — fresh (non-cached) input billable at
                                         the standard input rate. CRITICAL:
                                         this field already EXCLUDES cached
                                         tokens; do not subtract `cache_*`
                                         from it.
      * `cache_read_input_tokens`      — cache-hit input, billed at ~10% of
                                         standard input.
      * `cache_creation_input_tokens`  — cache-write input, billed at ~125%
                                         of standard input (5-min ephemeral).
      * `output_tokens`                — completion tokens.

    OpenRouter's Anthropic-compatible endpoint reports the same four fields.

    Calibrated against an Anthropic dashboard delta on 2026-04-24: this
    formula matched billed Haiku spend within 5% and Sonnet spend within
    rounding on a 31-call run. See PLAN.md "Errors & Learnings" Phase 4
    for the prior bug (subtracting cache tokens from `input_tokens`) which
    caused 60-70% under-counting and produced negative-cost Sonnet calls.

    `cache_*` fields are optional on `usage` — absent entirely (not `None`)
    when caching was not used. `getattr(..., 0) or 0` handles both
    "attribute missing" and "attribute present but zero" uniformly.

    The provider-billed cost wins when present: OpenRouter returns USD in
    `usage.cost`, which the SDK keeps in `usage.model_extra`. That figure is
    exact (real provider, real cache discounts), so it replaces the table
    estimate. Without it, rates come from `pricing_for(model)`; unknown
    models raise ValueError.
    """
    billed = _billed_cost_cents(response)
    if billed is not None:
        return billed
    p = pricing_for(model)
    u = response.usage
    input_tokens: int = u.input_tokens
    output_tokens: int = u.output_tokens
    cache_read: int = getattr(u, "cache_read_input_tokens", 0) or 0
    cache_creation: int = getattr(u, "cache_creation_input_tokens", 0) or 0

    cents = (
        input_tokens * p.input
        + cache_read * p.cache_read
        + cache_creation * p.cache_write
        + output_tokens * p.output
    )
    return cents / 1_000_000


def _billed_cost_cents(response: Any) -> float | None:
    """Provider-reported cost in cents, or None when the response has none."""
    extra = getattr(response.usage, "model_extra", None)
    if not isinstance(extra, dict):
        return None
    cost = extra.get("cost")
    if isinstance(cost, bool) or not isinstance(cost, int | float):
        return None
    return float(cost) * 100


def usage_record(response: Any, model: str, purpose: str) -> dict[str, Any]:
    """Per-call usage record for `turn_meta["llm_calls"]`.

    `billed` is True when `cost_cents` came from the provider, False when it
    is a price-table estimate.
    """
    u = response.usage
    return {
        "purpose": purpose,
        "model": model,
        "input_tokens": u.input_tokens,
        "cache_read_tokens": getattr(u, "cache_read_input_tokens", 0) or 0,
        "cache_write_tokens": getattr(u, "cache_creation_input_tokens", 0) or 0,
        "output_tokens": u.output_tokens,
        "cost_cents": cost_cents_of(response, model),
        "billed": _billed_cost_cents(response) is not None,
    }
