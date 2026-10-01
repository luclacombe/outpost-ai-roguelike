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

Secrets and DB/Supabase URLs still come from env/`.env`, so DB tests keep
hitting local Postgres.

Skipped when the `-m` expression selects `integration` or `eval`: those
runs exist to exercise the real provider from `.env`.
"""

import os
import re
import shlex
import sys
from typing import Any

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
    "MAX_COST_CENTS_PER_TURN": 15,
    "MAX_COST_CENTS_PER_RUN": 40,
    "SONNET_NARRATION_ENABLED": True,
    "DEV_BYPASS_DAILY_GATE": False,
    "LANGFUSE_PUBLIC_KEY": None,
    "LANGFUSE_SECRET_KEY": None,
    "LANGFUSE_BASE_URL": None,
}


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
