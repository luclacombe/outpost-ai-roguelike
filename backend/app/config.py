"""Typed settings loaded from environment + backend/.env.

Constructed once at import time as `settings`. Fails fast (Pydantic
ValidationError) if any required key is missing, which is what we want — the
process should not start against a half-configured Supabase project.
"""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    DATABASE_URL: str
    SUPABASE_URL: str
    SUPABASE_JWT_SECRET: str
    SUPABASE_SERVICE_ROLE_KEY: str
    ANTHROPIC_API_KEY: str
    FRONTEND_URL: str

    LANGFUSE_PUBLIC_KEY: str | None = None
    LANGFUSE_SECRET_KEY: str | None = None
    LANGFUSE_BASE_URL: str | None = None
    LANGSMITH_API_KEY: str | None = None

    ENVIRONMENT: str = Field(default="dev")
    LOG_LEVEL: str = Field(default="info")

    MAX_COST_CENTS_PER_TURN: int = 15
    MAX_COST_CENTS_PER_RUN: int = 40
    SONNET_NARRATION_ENABLED: bool = True


settings = Settings()
