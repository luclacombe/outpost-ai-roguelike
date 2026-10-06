"""Provider configuration: Anthropic default vs OpenRouter's Anthropic skin.

No real LLM calls. Every request is intercepted by respx; we assert on the
outgoing URL, auth headers, and body, then push an OpenRouter-shaped
response through the real parse + cost path.
"""

import json
from typing import Any
from uuid import uuid4

import pytest
import respx
from httpx import Response
from pydantic import ValidationError

from app.agent import anthropic_client
from app.agent.anthropic_client import build_client, call_fast, call_narration, cost_cents_of
from app.agent.constants import (
    MODEL_DEEPSEEK_V4_1_FLASH,
    MODEL_DEEPSEEK_V4_PRO,
    MODEL_HAIKU,
    MODEL_SONNET,
    ModelPricing,
)
from app.agent.nodes import agent_debate
from app.config import Settings
from app.game.schemas import Event, EventOrigin
from tests.test_nodes import make_colony_state

_BASE_ENV = {
    "DATABASE_URL": "postgresql+psycopg://u:p@h/db",
    "SUPABASE_URL": "https://x.supabase.co",
    "SUPABASE_JWT_SECRET": "secret",
    "SUPABASE_SERVICE_ROLE_KEY": "svc",
    "FRONTEND_URL": "http://localhost:3000",
}
_LLM_ENV_KEYS = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "LLM_API_KEY",
    "LLM_BASE_URL",
    "LLM_AUTH_SCHEME",
    "LLM_MODEL_FAST",
    "LLM_MODEL_NARRATION",
    "LLM_DISABLE_THINKING",
    "LLM_PRICING_OVERRIDES",
    "LLM_PROVIDER_ORDER",
)
_OPENROUTER_BASE = "https://openrouter.ai/api"
_OPENROUTER_URL = "https://openrouter.ai/api/v1/messages"
_ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"


def _settings(monkeypatch: pytest.MonkeyPatch, **llm_env: str) -> Settings:
    for key in _LLM_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    for key, value in {**_BASE_ENV, **llm_env}.items():
        monkeypatch.setenv(key, value)
    return Settings(_env_file=None)


def _openrouter_settings(monkeypatch: pytest.MonkeyPatch, **extra: str) -> Settings:
    env = {
        "LLM_BASE_URL": _OPENROUTER_BASE,
        "LLM_API_KEY": "sk-or-v1-test",
        "LLM_AUTH_SCHEME": "bearer",
        "LLM_MODEL_FAST": MODEL_DEEPSEEK_V4_1_FLASH,
        "LLM_MODEL_NARRATION": MODEL_DEEPSEEK_V4_1_FLASH,
        "LLM_DISABLE_THINKING": "true",
    }
    return _settings(monkeypatch, **{**env, **extra})


def _text_response(text: str = "ok", model: str = MODEL_HAIKU) -> dict[str, Any]:
    return {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": [{"type": "text", "text": text}],
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 10, "output_tokens": 5},
    }


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


def test_defaults_match_previous_anthropic_behaviour(monkeypatch):
    s = _settings(monkeypatch, ANTHROPIC_API_KEY="sk-ant-x")
    assert s.LLM_BASE_URL is None
    assert s.LLM_AUTH_SCHEME == "x-api-key"
    assert s.LLM_MODEL_FAST == MODEL_HAIKU
    assert s.LLM_MODEL_NARRATION == MODEL_SONNET
    assert s.LLM_DISABLE_THINKING is False
    assert s.LLM_PRICING_OVERRIDES == {}
    assert s.llm_api_key == "sk-ant-x"


def test_llm_api_key_falls_back_to_anthropic_key(monkeypatch):
    s = _settings(monkeypatch, ANTHROPIC_API_KEY="sk-ant-x")
    assert s.LLM_API_KEY is None
    assert s.llm_api_key == "sk-ant-x"


def test_llm_api_key_wins_over_anthropic_key(monkeypatch):
    s = _settings(monkeypatch, ANTHROPIC_API_KEY="sk-ant-x", LLM_API_KEY="sk-or-y")
    assert s.llm_api_key == "sk-or-y"


def test_anthropic_key_optional_when_llm_key_set(monkeypatch):
    s = _settings(monkeypatch, LLM_API_KEY="sk-or-y")
    assert s.ANTHROPIC_API_KEY is None
    assert s.llm_api_key == "sk-or-y"


def test_settings_fail_without_any_llm_key(monkeypatch):
    with pytest.raises(ValidationError):
        _settings(monkeypatch)


def test_blank_keys_count_as_missing(monkeypatch):
    with pytest.raises(ValidationError):
        _settings(monkeypatch, ANTHROPIC_API_KEY="", LLM_API_KEY="")


def test_blank_optional_llm_values_mean_unset(monkeypatch):
    """A copied .env.example with blank `FOO=` lines falls back to defaults."""
    s = _settings(
        monkeypatch,
        ANTHROPIC_API_KEY="k",
        LLM_API_KEY="",
        LLM_BASE_URL="",
        LLM_PRICING_OVERRIDES="",
        LLM_MODEL_FAST="",
    )
    assert s.LLM_API_KEY is None
    assert s.LLM_BASE_URL is None
    assert s.LLM_PRICING_OVERRIDES == {}
    assert s.LLM_MODEL_FAST == MODEL_HAIKU


def test_blank_values_in_dotenv_file_mean_unset(monkeypatch, tmp_path):
    for key in (*_LLM_ENV_KEYS, *_BASE_ENV):
        monkeypatch.delenv(key, raising=False)
    env_file = tmp_path / ".env"
    lines = [f"{k}={v}" for k, v in _BASE_ENV.items()]
    lines += [
        "LLM_API_KEY=sk-or-x",
        "ANTHROPIC_API_KEY=",
        "LLM_BASE_URL=",
        "LLM_PRICING_OVERRIDES=",
    ]
    env_file.write_text("\n".join(lines) + "\n")
    s = Settings(_env_file=env_file)
    assert s.ANTHROPIC_API_KEY is None
    assert s.LLM_BASE_URL is None
    assert s.LLM_PRICING_OVERRIDES == {}
    assert s.llm_api_key == "sk-or-x"


def test_auth_scheme_rejects_unknown_value(monkeypatch):
    with pytest.raises(ValidationError):
        _settings(monkeypatch, ANTHROPIC_API_KEY="k", LLM_AUTH_SCHEME="basic")


def test_pricing_overrides_parse_from_json_env(monkeypatch):
    s = _settings(
        monkeypatch,
        ANTHROPIC_API_KEY="k",
        LLM_PRICING_OVERRIDES=json.dumps(
            {"vendor/model": {"input": 1, "cache_write": 2, "cache_read": 3, "output": 4}}
        ),
    )
    assert s.LLM_PRICING_OVERRIDES["vendor/model"]["output"] == 4


# ---------------------------------------------------------------------------
# Client construction + auth headers
# ---------------------------------------------------------------------------


@respx.mock
async def test_default_client_hits_anthropic_with_x_api_key(monkeypatch):
    s = _settings(monkeypatch, ANTHROPIC_API_KEY="sk-ant-x")
    route = respx.post(_ANTHROPIC_URL).mock(return_value=Response(200, json=_text_response()))

    client = build_client(s)
    await client.messages.create(
        model=MODEL_HAIKU, max_tokens=10, messages=[{"role": "user", "content": "hi"}]
    )

    req = route.calls.last.request
    assert req.headers["x-api-key"] == "sk-ant-x"
    assert "authorization" not in req.headers


@respx.mock
async def test_openrouter_client_uses_bearer_and_openrouter_url(monkeypatch):
    s = _openrouter_settings(monkeypatch)
    # A stray Anthropic key in the OS env must NOT leak to OpenRouter: the SDK
    # would otherwise pick it up from ANTHROPIC_API_KEY and send x-api-key.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-should-not-leak")
    route = respx.post(_OPENROUTER_URL).mock(
        return_value=Response(200, json=_text_response(model=MODEL_DEEPSEEK_V4_1_FLASH))
    )

    client = build_client(s)
    await client.messages.create(
        model=MODEL_DEEPSEEK_V4_1_FLASH,
        max_tokens=10,
        messages=[{"role": "user", "content": "hi"}],
    )

    req = route.calls.last.request
    assert str(req.url) == _OPENROUTER_URL
    assert req.headers["authorization"] == "Bearer sk-or-v1-test"
    assert "x-api-key" not in req.headers


@respx.mock
async def test_x_api_key_mode_does_not_send_stray_auth_token(monkeypatch):
    s = _settings(monkeypatch, ANTHROPIC_API_KEY="sk-ant-x")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "stray-token")
    route = respx.post(_ANTHROPIC_URL).mock(return_value=Response(200, json=_text_response()))

    await build_client(s).messages.create(
        model=MODEL_HAIKU, max_tokens=10, messages=[{"role": "user", "content": "hi"}]
    )

    assert "authorization" not in route.calls.last.request.headers


# ---------------------------------------------------------------------------
# Thinking / reasoning off
# ---------------------------------------------------------------------------


@respx.mock
async def test_thinking_param_absent_by_default(monkeypatch):
    s = _settings(monkeypatch, ANTHROPIC_API_KEY="sk-ant-x")
    monkeypatch.setattr(anthropic_client, "settings", s)
    monkeypatch.setattr(anthropic_client, "client", build_client(s))
    route = respx.post(_ANTHROPIC_URL).mock(return_value=Response(200, json=_text_response()))

    await call_fast([{"type": "text", "text": "sys"}], "hi")
    await call_narration([{"type": "text", "text": "sys"}], "hi")

    for call in route.calls:
        body = json.loads(call.request.content)
        assert "thinking" not in body
    assert json.loads(route.calls[0].request.content)["model"] == MODEL_HAIKU
    assert json.loads(route.calls[1].request.content)["model"] == MODEL_SONNET


@respx.mock
async def test_thinking_disabled_sent_when_configured(monkeypatch):
    s = _openrouter_settings(monkeypatch)
    monkeypatch.setattr(anthropic_client, "settings", s)
    monkeypatch.setattr(anthropic_client, "client", build_client(s))
    route = respx.post(_OPENROUTER_URL).mock(
        return_value=Response(200, json=_text_response(model=MODEL_DEEPSEEK_V4_1_FLASH))
    )

    await call_fast([{"type": "text", "text": "sys"}], "hi")
    await call_narration([{"type": "text", "text": "sys"}], "hi")

    for call in route.calls:
        body = json.loads(call.request.content)
        assert body["thinking"] == {"type": "disabled"}
        assert body["model"] == MODEL_DEEPSEEK_V4_1_FLASH


def test_request_defaults_helper(monkeypatch):
    s = _settings(monkeypatch, ANTHROPIC_API_KEY="k")
    monkeypatch.setattr(anthropic_client, "settings", s)
    assert anthropic_client.request_extras() == {}
    s2 = _openrouter_settings(monkeypatch)
    monkeypatch.setattr(anthropic_client, "settings", s2)
    assert anthropic_client.request_extras() == {"thinking": {"type": "disabled"}}


# ---------------------------------------------------------------------------
# OpenRouter provider pinning (fast model only)
# ---------------------------------------------------------------------------


def test_provider_order_defaults_to_empty(monkeypatch):
    s = _openrouter_settings(monkeypatch)
    assert s.LLM_PROVIDER_ORDER == []


def test_provider_order_parses_comma_list_with_whitespace(monkeypatch):
    s = _openrouter_settings(monkeypatch, LLM_PROVIDER_ORDER=" StreamLake , Novita,, ")
    assert s.LLM_PROVIDER_ORDER == ["StreamLake", "Novita"]


def test_provider_order_single_entry(monkeypatch):
    s = _openrouter_settings(monkeypatch, LLM_PROVIDER_ORDER="StreamLake")
    assert s.LLM_PROVIDER_ORDER == ["StreamLake"]


def test_provider_order_blank_means_empty(monkeypatch):
    s = _openrouter_settings(monkeypatch, LLM_PROVIDER_ORDER="")
    assert s.LLM_PROVIDER_ORDER == []


def test_provider_order_blank_in_dotenv_means_empty(monkeypatch, tmp_path):
    for key in (*_LLM_ENV_KEYS, *_BASE_ENV):
        monkeypatch.delenv(key, raising=False)
    env_file = tmp_path / ".env"
    lines = [f"{k}={v}" for k, v in _BASE_ENV.items()]
    lines += ["LLM_API_KEY=sk-or-x", f"LLM_BASE_URL={_OPENROUTER_BASE}", "LLM_PROVIDER_ORDER="]
    env_file.write_text("\n".join(lines) + "\n")
    assert Settings(_env_file=env_file).LLM_PROVIDER_ORDER == []


def test_provider_order_from_dotenv_file(monkeypatch, tmp_path):
    for key in (*_LLM_ENV_KEYS, *_BASE_ENV):
        monkeypatch.delenv(key, raising=False)
    env_file = tmp_path / ".env"
    lines = [f"{k}={v}" for k, v in _BASE_ENV.items()]
    lines += [
        "LLM_API_KEY=sk-or-x",
        f"LLM_BASE_URL={_OPENROUTER_BASE}",
        "LLM_PROVIDER_ORDER=StreamLake,Novita",
    ]
    env_file.write_text("\n".join(lines) + "\n")
    assert Settings(_env_file=env_file).LLM_PROVIDER_ORDER == ["StreamLake", "Novita"]


def test_provider_order_rejected_without_openrouter_base_url(monkeypatch):
    with pytest.raises(ValidationError, match="LLM_PROVIDER_ORDER"):
        _settings(monkeypatch, ANTHROPIC_API_KEY="k", LLM_PROVIDER_ORDER="StreamLake")


def test_request_extras_pin_provider_only_for_fast(monkeypatch):
    s = _openrouter_settings(monkeypatch, LLM_PROVIDER_ORDER="StreamLake,Novita")
    monkeypatch.setattr(anthropic_client, "settings", s)
    provider = {"order": ["StreamLake", "Novita"], "allow_fallbacks": False}
    assert anthropic_client.request_extras(fast=True) == {
        "thinking": {"type": "disabled"},
        "extra_body": {"provider": provider},
    }
    assert anthropic_client.request_extras() == {"thinking": {"type": "disabled"}}


def test_request_extras_no_provider_block_when_order_empty(monkeypatch):
    s = _openrouter_settings(monkeypatch)
    monkeypatch.setattr(anthropic_client, "settings", s)
    assert "extra_body" not in anthropic_client.request_extras(fast=True)


@respx.mock
async def test_provider_order_sent_on_call_fast_not_call_narration(monkeypatch):
    s = _openrouter_settings(
        monkeypatch,
        LLM_PROVIDER_ORDER="StreamLake,Novita",
        LLM_MODEL_FAST=MODEL_DEEPSEEK_V4_PRO,
    )
    monkeypatch.setattr(anthropic_client, "settings", s)
    monkeypatch.setattr(anthropic_client, "client", build_client(s))
    route = respx.post(_OPENROUTER_URL).mock(
        return_value=Response(200, json=_text_response(model=MODEL_DEEPSEEK_V4_PRO))
    )

    await call_fast([{"type": "text", "text": "sys"}], "hi", tools=[])
    await call_narration([{"type": "text", "text": "sys"}], "hi")

    fast_body = json.loads(route.calls[0].request.content)
    narration_body = json.loads(route.calls[1].request.content)
    assert fast_body["provider"] == {"order": ["StreamLake", "Novita"], "allow_fallbacks": False}
    assert fast_body["model"] == MODEL_DEEPSEEK_V4_PRO
    assert fast_body["tools"] == []
    assert "provider" not in narration_body
    assert narration_body["model"] == MODEL_DEEPSEEK_V4_1_FLASH


def test_v4_pro_priced_at_novita_ceiling():
    assert anthropic_client.pricing_for(MODEL_DEEPSEEK_V4_PRO) == ModelPricing(
        input=99, cache_write=99, cache_read=99, output=297
    )


# ---------------------------------------------------------------------------
# OpenRouter-shaped response end to end through agent_debate
# ---------------------------------------------------------------------------


@respx.mock
async def test_openrouter_debate_response_parses_and_costs(monkeypatch):
    """An OpenRouter Anthropic-skin response (tool_use + cache usage fields)
    flows through parse_debate_response and cost_cents_of unchanged."""
    s = _openrouter_settings(monkeypatch)
    monkeypatch.setattr(anthropic_client, "settings", s)
    monkeypatch.setattr(anthropic_client, "client", build_client(s))
    from app.agent import nodes

    monkeypatch.setattr(nodes, "settings", s)

    colony = make_colony_state()
    nadia = colony.agents[0]
    event = Event(
        id="evt_storm",
        category="weather",
        title="Ice storm",
        body="Wind.",
        severity=2,
        origin=EventOrigin.NATURAL,
    )
    usage = {
        "input_tokens": 1_000,
        "output_tokens": 500,
        "cache_read_input_tokens": 4_000,
        "cache_creation_input_tokens": 200,
    }
    body = {
        # OpenRouter echoes its own id prefix and the canonical slug.
        "id": "gen-1789-abc",
        "type": "message",
        "role": "assistant",
        "model": "deepseek/deepseek-v4.1-flash-20260910",
        "content": [
            {"type": "text", "text": f'<agent name="{nadia.name}">Burn it.</agent>'},
            {
                "type": "tool_use",
                "id": "toolu_or_" + uuid4().hex[:8],
                "name": "propose_action",
                "input": {
                    "name": "Burn fuel",
                    "description": "Run the heater hard.",
                    "flavor": "DOMINANT",
                    "effects_preview": ["-10 fuel"],
                    "tool_calls_on_pick": [],
                    "caller_agent_id": str(nadia.id),
                },
            },
        ],
        "stop_reason": "tool_use",
        "stop_sequence": None,
        "usage": usage,
    }
    route = respx.post(_OPENROUTER_URL).mock(return_value=Response(200, json=body))

    result = await agent_debate(
        {"colony_state": colony, "current_event": event, "cost_cents_this_turn": 0.0}
    )

    # One proposal < 3, so the debate retries once; the retry returns the
    # same body (not better), so the first parse is kept. Request shape on
    # both calls: thinking off, cache_control on the last system block.
    assert route.call_count == 2
    for call in route.calls:
        sent = json.loads(call.request.content)
        assert sent["model"] == MODEL_DEEPSEEK_V4_1_FLASH
        assert sent["thinking"] == {"type": "disabled"}
        assert sent["system"][-1]["cache_control"] == {"type": "ephemeral"}

    # Parse path.
    assert result["debate_per_agent_text"][nadia.id] == "Burn it."
    [tc] = result["debate_tool_calls"]
    assert tc.name == "propose_action"
    assert tc.caller_id == nadia.id
    assert "caller_agent_id" not in tc.args

    # Cost path: DeepSeek V4.1 Flash rates (cents / MTok): 2.7 in, 2.7 cache
    # write, 2.7 cache read, 60 out. Both calls are billed.
    expected = (1_000 * 2.7 + 200 * 2.7 + 4_000 * 2.7 + 500 * 60) / 1_000_000
    assert result["cost_cents_this_turn"] == pytest.approx(2 * expected)
    assert expected > 0


def test_cost_cents_of_unknown_model_still_raises():
    class _U:
        input_tokens = 1
        output_tokens = 1

    class _R:
        usage = _U()

    with pytest.raises(ValueError):
        cost_cents_of(_R(), "vendor/unpriced-model")
