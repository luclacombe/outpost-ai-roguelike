"""Pin env-sensitive settings so unit tests don't depend on `backend/.env`.

`app.config.settings` is built from `.env` at import, and
`app.agent.anthropic_client` builds its module-level client from it. A dev
`.env` pointed at OpenRouter (or with `DEV_BYPASS_DAILY_GATE=true`, Langfuse
keys, ...) would otherwise make the respx `api.anthropic.com` mocks miss,
flip gate assertions, and ship test spans to Langfuse cloud.

Importing this module replaces `app.config.settings` with a copy whose
provider / budget / gate / tracing fields are pinned to the test defaults.
It must run before any other `app.*` import, since modules bind `settings`
with `from app.config import settings`. Init kwargs outrank both the shell
and `.env`, so `None` really means unset (a blank env var would not).

Secrets and Supabase URLs still come from env/`.env`. `DATABASE_URL` does
too, but only if it points at a local host: a remote URL (e.g. the prod
pooler in a dev `.env`) is redirected to local Supabase unless
`OUTPOST_ALLOW_REMOTE_DB_TESTS=1`. This guard applies to every run,
including `integration`/`eval`. Without it, DB tests silently wrote fixture
games into the production project.

Skipped when the `-m` expression selects `integration` or `eval`: those
runs exist to exercise the real provider from `.env`.
"""

import os
import re
import shlex
import sys
from typing import Any
from urllib.parse import urlsplit

import app.config

# pyproject addopts: "-m 'not eval and not integration'".
_DEFAULT_MARKEXPR = "not eval and not integration"

_PINNED: dict[str, Any] = {
    # Explicit URL: with None the SDK falls back to the ANTHROPIC_BASE_URL env var.
    "LLM_BASE_URL": "https://api.anthropic.com",
    "LLM_AUTH_SCHEME": "x-api-key",
    "LLM_MODEL_FAST": "claude-haiku-4-5-20251001",
    "LLM_MODEL_NARRATION": "claude-sonnet-4-6",
    "LLM_DISABLE_THINKING": False,
    "LLM_PRICING_OVERRIDES": {},
    "LLM_PROVIDER_ORDER": [],
    "MAX_COST_CENTS_PER_TURN": 15,
    "MAX_COST_CENTS_PER_RUN": 40,
    "SONNET_NARRATION_ENABLED": True,
    "DEV_BYPASS_DAILY_GATE": False,
    "LANGFUSE_PUBLIC_KEY": None,
    "LANGFUSE_SECRET_KEY": None,
    "LANGFUSE_BASE_URL": None,
}


LOCAL_TEST_DATABASE_URL = "postgresql+psycopg://postgres:postgres@localhost:54322/postgres"
_LOCAL_DB_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "host.docker.internal"})


def guard_database_url(url: str, *, allow_remote: bool) -> str:
    """Return `url` if its host is local (or remote is allowed), else local Supabase."""
    if allow_remote or urlsplit(url).hostname in _LOCAL_DB_HOSTS:
        return url
    return LOCAL_TEST_DATABASE_URL


def _markexpr(argv: list[str]) -> str:
    """Effective `-m` value: last one wins, PYTEST_ADDOPTS before the CLI."""
    args = shlex.split(os.environ.get("PYTEST_ADDOPTS", "")) + argv
    expr = _DEFAULT_MARKEXPR
    for i, arg in enumerate(args):
        if arg == "-m" and i + 1 < len(args):
            expr = args[i + 1]
        elif arg.startswith("-m") and len(arg) > 2:
            expr = arg[2:].lstrip("=")
    return expr


def selects_real_llm(markexpr: str) -> bool:
    """True if the expression can select an `integration` or `eval` test.

    An empty expression selects everything, real-LLM tests included.
    """
    if not markexpr.strip():
        return True
    positive = re.sub(r"\bnot\s+(integration|eval)\b", "", markexpr)
    return re.search(r"\b(integration|eval)\b", positive) is not None


if not selects_real_llm(_markexpr(sys.argv[1:])):
    app.config.settings = app.config.Settings(**_PINNED)

_db_url = app.config.settings.DATABASE_URL
_guarded_db_url = guard_database_url(
    _db_url, allow_remote=os.environ.get("OUTPOST_ALLOW_REMOTE_DB_TESTS") == "1"
)
if _guarded_db_url != _db_url:
    sys.stderr.write(
        f"[tests] DATABASE_URL host {urlsplit(_db_url).hostname!r} is not local; "
        "DB tests use local Supabase (localhost:54322) instead. "
        "Set OUTPOST_ALLOW_REMOTE_DB_TESTS=1 to override.\n"
    )
    app.config.settings = app.config.settings.model_copy(update={"DATABASE_URL": _guarded_db_url})
