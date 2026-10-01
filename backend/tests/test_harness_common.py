"""Shared CLI helpers for play_test and the balance sweep."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.agent import observability
from app.config import settings
from app.eval.playtest.tracing import tracing_disabled
from scripts._harness import next_free_dir, require_local_database


def test_tracing_disabled_by_default_and_restored(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "LANGFUSE_PUBLIC_KEY", "pk-test")
    monkeypatch.setattr(settings, "LANGFUSE_SECRET_KEY", "sk-test")
    assert observability.langfuse_enabled() is True
    with tracing_disabled(enabled=True):
        assert observability.langfuse_enabled() is False
    assert observability.langfuse_enabled() is True
    assert settings.LANGFUSE_PUBLIC_KEY == "pk-test"


def test_tracing_opt_in_leaves_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "LANGFUSE_PUBLIC_KEY", "pk-test")
    monkeypatch.setattr(settings, "LANGFUSE_SECRET_KEY", "sk-test")
    with tracing_disabled(enabled=False):
        assert observability.langfuse_enabled() is True


def test_tracing_restored_on_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "LANGFUSE_PUBLIC_KEY", "pk-test")
    monkeypatch.setattr(settings, "LANGFUSE_SECRET_KEY", "sk-test")
    with pytest.raises(RuntimeError), tracing_disabled(enabled=True):
        raise RuntimeError("boom")
    assert settings.LANGFUSE_SECRET_KEY == "sk-test"


@pytest.mark.parametrize(
    "url",
    [
        "postgresql+psycopg://postgres:postgres@localhost:54322/postgres",
        "postgresql+psycopg://postgres:postgres@127.0.0.1:54322/postgres",
        "postgresql+psycopg://postgres:postgres@host.docker.internal:54322/postgres",
    ],
)
def test_local_database_accepted(url: str) -> None:
    require_local_database(url)


def test_remote_database_refused() -> None:
    remote = "postgresql+psycopg://postgres:pw@db.abcdefgh.supabase.co:5432/postgres"
    with pytest.raises(SystemExit, match="db.abcdefgh.supabase.co"):
        require_local_database(remote)
    require_local_database(remote, allow_remote=True)


def test_next_free_dir_suffixes_same_day(tmp_path: Path) -> None:
    first = next_free_dir(tmp_path, "2026-10-01")
    assert first == tmp_path / "2026-10-01"
    first.mkdir()
    second = next_free_dir(tmp_path, "2026-10-01")
    assert second == tmp_path / "2026-10-01-2"
    second.mkdir()
    assert next_free_dir(tmp_path, "2026-10-01") == tmp_path / "2026-10-01-3"
