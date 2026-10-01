"""Typed settings loaded from environment + backend/.env.

Constructed once at import time as `settings`. Fails fast (Pydantic
ValidationError) if any required key is missing, which is what we want — the
process should not start against a half-configured Supabase project.
"""

from typing import Literal, Self

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_DEV_ENVIRONMENTS = frozenset({"dev", "development", "local", "test"})


class Settings(BaseSettings):
    # env_ignore_empty: a blank `FOO=` line means "unset" (use the default),
    # so a half-filled copy of .env.example doesn't hand "" to the SDK as a
    # base URL or fail JSON-parsing LLM_PRICING_OVERRIDES.
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", env_ignore_empty=True)

    DATABASE_URL: str
    SUPABASE_URL: str
    SUPABASE_JWT_SECRET: str
    SUPABASE_SERVICE_ROLE_KEY: str
    FRONTEND_URL: str

    # --- LLM provider -------------------------------------------------------
    # Defaults = Anthropic direct, identical to the pre-provider-config
    # behaviour. For OpenRouter's Anthropic-compatible Messages endpoint set
    # LLM_BASE_URL=https://openrouter.ai/api, LLM_AUTH_SCHEME=bearer and
    # LLM_API_KEY. See app/agent/CLAUDE.md "Provider config".
    ANTHROPIC_API_KEY: str | None = None
    LLM_API_KEY: str | None = None  # falls back to ANTHROPIC_API_KEY
    LLM_BASE_URL: str | None = None  # None -> SDK default (https://api.anthropic.com)
    LLM_AUTH_SCHEME: Literal["x-api-key", "bearer"] = "x-api-key"
    LLM_MODEL_FAST: str = "claude-haiku-4-5-20251001"  # debate, autonomous, eval judges
    LLM_MODEL_NARRATION: str = "claude-sonnet-4-6"
    # Sends `thinking: {"type": "disabled"}` on every call. Needed for models
    # that reason by default (DeepSeek V4.1 Flash on OpenRouter).
    LLM_DISABLE_THINKING: bool = False
    # JSON {"<model id>": {"input": c, "cache_write": c, "cache_read": c,
    # "output": c}}, cents per million tokens. Extends / overrides
    # app.agent.constants.MODEL_PRICING without a code change.
    LLM_PRICING_OVERRIDES: dict[str, dict[str, float]] = Field(default_factory=dict)

    LANGFUSE_PUBLIC_KEY: str | None = None
    LANGFUSE_SECRET_KEY: str | None = None
    LANGFUSE_BASE_URL: str | None = None
    LANGSMITH_API_KEY: str | None = None

    ENVIRONMENT: str = Field(default="dev")
    LOG_LEVEL: str = Field(default="info")

    MAX_COST_CENTS_PER_TURN: int = 15
    MAX_COST_CENTS_PER_RUN: int = 40
    SONNET_NARRATION_ENABLED: bool = True

    # Dev only: skip the one-run-per-day gate so local testing doesn't need
    # DB edits. Rejected at startup unless ENVIRONMENT is a dev environment.
    DEV_BYPASS_DAILY_GATE: bool = False

    @model_validator(mode="after")
    def _require_llm_key(self) -> Self:
        if not (self.LLM_API_KEY or self.ANTHROPIC_API_KEY):
            raise ValueError("Set LLM_API_KEY or ANTHROPIC_API_KEY (blank does not count).")
        return self

    @model_validator(mode="after")
    def _bypass_only_in_dev(self) -> Self:
        if self.DEV_BYPASS_DAILY_GATE and self.ENVIRONMENT.lower() not in _DEV_ENVIRONMENTS:
            raise ValueError(
                f"DEV_BYPASS_DAILY_GATE=true is only allowed when ENVIRONMENT is one of "
                f"{sorted(_DEV_ENVIRONMENTS)}; got {self.ENVIRONMENT!r}."
            )
        return self

    @property
    def llm_api_key(self) -> str:
        """Key sent to the LLM provider: LLM_API_KEY, else ANTHROPIC_API_KEY."""
        key = self.LLM_API_KEY or self.ANTHROPIC_API_KEY
        if not key:  # unreachable after _require_llm_key; keeps the type narrow
            raise ValueError("no LLM API key configured")
        return key


settings = Settings()
