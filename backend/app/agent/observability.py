"""Langfuse v3 wiring for LangGraph invocations.

Adds LangChain `callbacks` and per-trace `metadata` to the canonical
thread_config dict so every node's LLM call surfaces in Langfuse with the
right session/user/tag context. When `LANGFUSE_PUBLIC_KEY` or
`LANGFUSE_SECRET_KEY` is missing, the wiring is a no-op — the package
must not import the langchain extra at module load and must not emit
network traffic. Importing this module is side-effect-free; the global
Langfuse client is constructed lazily and cached at module scope, so a
fresh background flush thread is NOT spawned per turn.

Invariant #12 (thread_id discipline) is preserved: `with_observability`
returns a NEW dict that copies the caller's `configurable` block
verbatim, so `thread_id` survives.

### Fail-closed contract

All observability wiring runs with try/except guards. A Langfuse outage,
auth failure, or any other exception during client init or callback
construction MUST log a warning and return None — it must NEVER raise
into the SSE generator. Observability is best-effort.

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
import threading
from typing import TYPE_CHECKING, Any

from app.config import settings

logger = logging.getLogger(__name__)

if TYPE_CHECKING:  # pragma: no cover — typing-only import
    from langfuse import Langfuse
    from langfuse.langchain import CallbackHandler


# Module-level cache for the Langfuse client. Constructing `Langfuse(...)`
# spawns a background flush thread; doing so per turn would leak threads.
# Tests reset this to None between cases (see conftest fixture).
_langfuse_client: Langfuse | None = None
_client_lock = threading.Lock()


def _truthy(value: str | None) -> bool:
    """Empty / None / whitespace-only → False. Operator typo guard."""
    return bool(value and value.strip())


def langfuse_enabled() -> bool:
    """True iff both Langfuse keys are non-empty after stripping whitespace."""
    return _truthy(settings.LANGFUSE_PUBLIC_KEY) and _truthy(settings.LANGFUSE_SECRET_KEY)


def _ensure_langfuse_client() -> bool:
    """Construct (once) the global Langfuse client with our explicit credentials.

    Idempotent: returns immediately if a client already exists. The double-
    checked locking protects against two concurrent /turn requests racing
    to instantiate the singleton on cold start.

    Returns False (and logs a warning) if construction fails for any
    reason — auth failure, network blip, package bug. Observability is
    best-effort; never raises into callers.
    """
    global _langfuse_client
    if _langfuse_client is not None:
        return True
    with _client_lock:
        if _langfuse_client is not None:
            return True
        try:
            from langfuse import Langfuse  # lazy

            _langfuse_client = Langfuse(
                public_key=settings.LANGFUSE_PUBLIC_KEY,
                secret_key=settings.LANGFUSE_SECRET_KEY,
                host=settings.LANGFUSE_BASE_URL or "https://cloud.langfuse.com",
            )
            return True
        except Exception:
            logger.warning("langfuse client init failed; tracing disabled", exc_info=True)
            return False


def build_langfuse_handler() -> CallbackHandler | None:
    """Construct a Langfuse v3 CallbackHandler, or None when disabled.

    Lazy-imports `langfuse.langchain` so the disabled path doesn't pay
    the import cost. Initializes the cached global `Langfuse` client
    first (idempotent) so the handler's internal `get_client()` call
    finds our credentials without relying on `os.environ`.

    Any exception during import or construction is logged and swallowed —
    the contract is that observability MUST NOT raise into the SSE
    generator.
    """
    if not langfuse_enabled():
        return None
    if not _ensure_langfuse_client():
        return None
    try:
        from langfuse.langchain import CallbackHandler  # lazy

        return CallbackHandler()
    except Exception:
        logger.warning("langfuse CallbackHandler construction failed", exc_info=True)
        return None


def _trace_metadata(
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
    if handler is None:  # defensive — keys flipped between checks, or init failed
        return cfg

    enriched: dict[str, Any] = {**cfg}
    # Copy the configurable block so callers can't accidentally mutate ours.
    if "configurable" in enriched:
        enriched["configurable"] = dict(enriched["configurable"])

    enriched["callbacks"] = [handler]
    enriched["metadata"] = _trace_metadata(
        game_id=game_id,
        user_id=user_id,
        setting_id=setting_id,
        turn_number=turn_number,
        environment=settings.ENVIRONMENT,
    )
    return enriched
