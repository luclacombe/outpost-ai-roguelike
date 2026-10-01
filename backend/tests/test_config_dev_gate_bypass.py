"""DEV_BYPASS_DAILY_GATE must never be enableable outside dev environments."""

import pytest
from pydantic import ValidationError

from app.config import Settings

_BASE = {
    "DATABASE_URL": "postgresql+psycopg://u:p@localhost:5432/db",
    "SUPABASE_URL": "https://x.supabase.co",
    "SUPABASE_JWT_SECRET": "x",
    "SUPABASE_SERVICE_ROLE_KEY": "x",
    "LLM_API_KEY": "x",
    "FRONTEND_URL": "http://localhost:3000",
}


def _settings(**overrides: object) -> Settings:
    return Settings(_env_file=None, **{**_BASE, **overrides})


def test_bypass_defaults_off():
    assert _settings(ENVIRONMENT="dev").DEV_BYPASS_DAILY_GATE is False


@pytest.mark.parametrize("env", ["dev", "development", "local", "test"])
def test_bypass_allowed_in_dev_environments(env: str):
    assert _settings(ENVIRONMENT=env, DEV_BYPASS_DAILY_GATE=True).DEV_BYPASS_DAILY_GATE is True


@pytest.mark.parametrize("env", ["prod", "production", "staging", "PROD"])
def test_bypass_rejected_outside_dev(env: str):
    with pytest.raises(ValidationError, match="DEV_BYPASS_DAILY_GATE"):
        _settings(ENVIRONMENT=env, DEV_BYPASS_DAILY_GATE=True)
