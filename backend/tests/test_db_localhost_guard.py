"""Tests must never hit a remote database unless explicitly allowed."""

import os

import pytest

from app.config import settings
from tests._settings_isolation import LOCAL_TEST_DATABASE_URL, guard_database_url

_REMOTE = "postgresql+psycopg://postgres.ref:pw@aws-0-eu-west-1.pooler.supabase.com:5432/postgres"


@pytest.mark.parametrize(
    "url",
    [
        "postgresql+psycopg://postgres:postgres@localhost:54322/postgres",
        "postgresql+psycopg://postgres:postgres@127.0.0.1:54322/postgres",
        "postgresql+psycopg://postgres:postgres@[::1]:54322/postgres",
        "postgresql+psycopg://postgres:postgres@host.docker.internal:54322/postgres",
    ],
)
def test_local_urls_pass_through(url: str):
    assert guard_database_url(url, allow_remote=False) == url


def test_remote_url_redirected_to_local():
    assert guard_database_url(_REMOTE, allow_remote=False) == LOCAL_TEST_DATABASE_URL


def test_remote_url_kept_when_explicitly_allowed():
    assert guard_database_url(_REMOTE, allow_remote=True) == _REMOTE


@pytest.mark.skipif(
    os.environ.get("OUTPOST_ALLOW_REMOTE_DB_TESTS") == "1", reason="remote DB explicitly allowed"
)
def test_active_settings_point_at_a_local_database():
    # The guard runs at conftest import; whatever .env says, this session is local.
    assert guard_database_url(settings.DATABASE_URL, allow_remote=False) == settings.DATABASE_URL
