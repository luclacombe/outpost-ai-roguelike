"""Langfuse v3 wiring for LangGraph invocations.

Adds LangChain `callbacks` and per-trace `metadata` to the canonical
thread_config dict so every node's LLM call surfaces in Langfuse with the
right session/user/tag context. When `LANGFUSE_PUBLIC_KEY` or
`LANGFUSE_SECRET_KEY` is missing, the wiring is a no-op — the package
must not import the langchain extra at module load and must not emit
network traffic. Importing this module is side-effect-free; the global
Langfuse client is constructed lazily inside `build_langfuse_handler`
with explicit credentials (no `os.environ` mutation).

Invariant #12 (thread_id discipline) is preserved: `with_observability`
returns a NEW dict that copies the caller's `configurable` block
verbatim, so `thread_id` survives.

### v3 metadata contract

Langfuse v3 reads three special keys from `config["metadata"]`:

  - `langfuse_session_id` → groups spans across `/turn` and `/decide`
    invocations of the same run. We use `outpost-run-{game_id}` so a
    single permadeath run is one Langfuse session.
  - `langfuse_user_id`    → propagates to traces; we use the Supabase
    user id (a UUID string, scoped per project).
  - `langfuse_tags`       → free-form filterable tags. We always emit
    `env:{environment}` and (when set) `setting:{setting_id}`.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from app.config import settings

logger = logging.getLogger(__name__)
_warned_missing_langchain = False

if TYPE_CHECKING:  # pragma: no cover — typing-only import
    from langfuse.langchain import CallbackHandler


def _truthy(value: str | None) -> bool:
    """Empty / None / whitespace-only → False. Operator typo guard."""
    return bool(value and value.strip())


def langfuse_enabled() -> bool:
    """True iff both Langfuse keys are non-empty after stripping whitespace."""
    return _truthy(settings.LANGFUSE_PUBLIC_KEY) and _truthy(settings.LANGFUSE_SECRET_KEY)


def _ensure_langfuse_client() -> bool:
    """Construct the global Langfuse client with our explicit credentials.

    `langfuse.get_client()` returns the already-initialized singleton; if
    one hasn't been created yet, it falls back to env vars. Constructing
    `Langfuse(public_key=, secret_key=, host=)` here registers the
    singleton with our `Settings` values directly — no `os.environ`
    mutation, so unit tests don't see surprise pollution.

    Returns False if the langfuse package itself can't import.
    """
    try:
        from langfuse import Langfuse  # lazy
    except ModuleNotFoundError:  # pragma: no cover — langfuse is a hard dep
        return False
    Langfuse(
        public_key=settings.LANGFUSE_PUBLIC_KEY,
        secret_key=settings.LANGFUSE_SECRET_KEY,
        host=settings.LANGFUSE_BASE_URL,
    )
    return True


def build_langfuse_handler() -> CallbackHandler | None:
    """Construct a Langfuse v3 CallbackHandler, or None when disabled.

    Lazy-imports `langfuse.langchain` so the disabled path never requires
    Langfuse's optional `langchain` umbrella package to be installed.
    Initializes the global `Langfuse` client first (idempotent) so the
    handler's internal `get_client()` call finds our credentials without
    relying on `os.environ`.
    """
    if not langfuse_enabled():
        return None
    if not _ensure_langfuse_client():
        return None
    try:
        from langfuse.langchain import CallbackHandler  # lazy: optional dep
    except ModuleNotFoundError:
        # Langfuse v4's `langfuse.langchain` requires the `langchain`
        # umbrella package (not just `langchain-core`). If it's missing,
        # disable Langfuse cleanly and warn once — never crash the run.
        global _warned_missing_langchain
        if not _warned_missing_langchain:
            logger.warning(
                "Langfuse keys are set but `langchain` package is missing; "
                "install it to enable Langfuse tracing. Disabling for this process."
            )
            _warned_missing_langchain = True
        return None

    return CallbackHandler()


def trace_metadata(
    *,
    game_id: str,
    user_id: str,
    setting_id: str | None,
    turn_number: int | None,
    environment: str,
) -> dict[str, Any]:
    """Build the Langfuse-v3 metadata dict for a LangGraph config.

    `langfuse_session_id` follows the `outpost-run-{game_id}` convention so
    a permadeath run's spans group cleanly. Tags always include the env
    label, plus `setting:{setting_id}` when known.
    """
    tags: list[str] = [f"env:{environment}"]
    if setting_id:
        tags.append(f"setting:{setting_id}")

    md: dict[str, Any] = {
        "langfuse_session_id": f"outpost-run-{game_id}",
        "langfuse_user_id": user_id,
        "langfuse_tags": tags,
    }
    if turn_number is not None:
        md["turn_number"] = turn_number
    return md


def with_observability(
    cfg: dict,
    *,
    game_id: str,
    user_id: str,
    setting_id: str | None,
    turn_number: int | None,
) -> dict:
    """Return `cfg` with Langfuse callbacks + metadata attached when enabled.

    Disabled mode returns the input dict unchanged (cheap no-op; nothing
    is added so caller behavior is identical). Enabled mode returns a
    NEW dict so caller-shared `cfg` instances don't pick up callbacks
    from a prior call. Invariant #12: the original `configurable.thread_id`
    is preserved verbatim either way. The input dict is never mutated.
    """
    if not langfuse_enabled():
        return cfg

    handler = build_langfuse_handler()
    if handler is None:  # defensive — keys flipped between checks
        return cfg

    enriched: dict[str, Any] = {**cfg}
    # Copy the configurable block so callers can't accidentally mutate ours.
    if "configurable" in enriched:
        enriched["configurable"] = dict(enriched["configurable"])

    enriched["callbacks"] = [handler]
    enriched["metadata"] = trace_metadata(
        game_id=game_id,
        user_id=user_id,
        setting_id=setting_id,
        turn_number=turn_number,
        environment=settings.ENVIRONMENT,
    )
    return enriched
