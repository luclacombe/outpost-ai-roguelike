"""Settings loads the required Supabase/Anthropic env vars and fails fast when missing."""

import pytest
from pydantic import ValidationError

REQUIRED_ENV = {
    "DATABASE_URL": "postgresql+psycopg://u:p@h/db",
    "SUPABASE_URL": "https://x.supabase.co",
    "SUPABASE_JWT_SECRET": "secret",
    "SUPABASE_SERVICE_ROLE_KEY": "svc",
    "ANTHROPIC_API_KEY": "sk-ant-x",
    "FRONTEND_URL": "http://localhost:3000",
}


def _load_fresh_settings(monkeypatch, env: dict[str, str] | None = None):
    """Force Pydantic to re-read env by constructing a fresh Settings() each call.

    monkeypatch.setenv adjusts os.environ; Settings reads from os.environ at
    construction time, and we pass _env_file=None to bypass .env entirely so
    tests don't accidentally inherit developer secrets.
    """
    from app.config import Settings

    for key, value in (env or {}).items():
        monkeypatch.setenv(key, value)
    return Settings(_env_file=None)


def test_settings_require_critical_env_vars(monkeypatch):
    for key in REQUIRED_ENV:
        monkeypatch.delenv(key, raising=False)
    from app.config import Settings

    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_settings_load_from_env(monkeypatch):
    for key in REQUIRED_ENV:
        monkeypatch.delenv(key, raising=False)
    s = _load_fresh_settings(monkeypatch, REQUIRED_ENV)

    assert s.DATABASE_URL == "postgresql+psycopg://u:p@h/db"
    assert s.SUPABASE_URL == "https://x.supabase.co"
    assert s.ENVIRONMENT == "dev"
    assert s.LOG_LEVEL == "info"
    assert s.LANGFUSE_PUBLIC_KEY is None
    assert s.SONNET_NARRATION_ENABLED is True
