"""Langfuse v3 observability wiring around LangGraph invocations.

The module under test (`app.agent.observability`) wraps the canonical
`thread_config` dict produced by `app.agent.graph.thread_config` and adds
LangChain `callbacks` plus per-trace `metadata` keys when Langfuse env
vars are populated. It is also responsible for the
`langfuse_session_id` / `langfuse_user_id` / `langfuse_tags` shape that
Langfuse v3 expects on the LangGraph config metadata.

Disabled-mode coverage matters because production tests run without
Langfuse keys and must not blow up at import or call time.

The enabled-path tests stub BOTH the langchain `CallbackHandler` AND
`_ensure_langfuse_client` so no real `Langfuse(...)` constructor runs —
the real one would spawn a background flush thread and (when keys are
placeholders) noisily fail auth.
"""

from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock

import pytest

from app.agent.graph import thread_config
from app.config import settings


@pytest.fixture(autouse=True)
def reset_langfuse_client_cache() -> None:
    """Reset the module-level `_langfuse_client` cache between tests.

    The cache is a singleton by design (one Langfuse client per process),
    but tests that monkeypatch settings keys or stub init must not see a
    client constructed by an earlier test. Runs around every test so no
    test forgets to clear it.
    """
    from app.agent import observability

    observability._langfuse_client = None
    yield
    observability._langfuse_client = None


@pytest.fixture
def stub_client_init(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make `_ensure_langfuse_client` a no-op success.

    Prevents the enabled-path tests from instantiating the real
    `langfuse.Langfuse` class with placeholder keys (which would spawn a
    background flush thread and emit auth failures).
    """
    from app.agent import observability

    monkeypatch.setattr(observability, "_ensure_langfuse_client", lambda: True)
    # Also park a sentinel so any code that reads the cache directly sees
    # a non-None value.
    monkeypatch.setattr(observability, "_langfuse_client", object(), raising=False)


@pytest.fixture
def fake_callback_handler(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Replace `langfuse.langchain.CallbackHandler` with a lightweight fake.

    The real handler hits Langfuse cloud on construction (and requires
    the `langchain` umbrella package). The fake is enough because we
    only ever assert "the builder returned an instance of this thing".
    """
    fake_module = types.ModuleType("langfuse.langchain")
    fake_handler = MagicMock(name="CallbackHandler")
    fake_module.CallbackHandler = fake_handler  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "langfuse.langchain", fake_module)
    return fake_handler


def test_langfuse_disabled_when_keys_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    """Empty/None keys → fully disabled, no callbacks, no metadata.

    `with_observability` must return the SAME logical config (deep-equal)
    so callers don't have to branch on enable state.
    """
    monkeypatch.setattr(settings, "LANGFUSE_PUBLIC_KEY", "", raising=False)
    monkeypatch.setattr(settings, "LANGFUSE_SECRET_KEY", "", raising=False)

    from app.agent import observability

    assert observability.langfuse_enabled() is False
    assert observability.build_langfuse_handler() is None

    cfg = thread_config("game-abc")
    enriched = observability.with_observability(
        cfg,
        game_id="game-abc",
        user_id="user-xyz",
        setting_id="arctic_station_v1",
        turn_number=1,
    )
    assert enriched == cfg
    assert "callbacks" not in enriched
    assert "metadata" not in enriched


def test_langfuse_disabled_when_keys_whitespace(monkeypatch: pytest.MonkeyPatch) -> None:
    """Whitespace-only keys count as disabled — operator typo guard."""
    monkeypatch.setattr(settings, "LANGFUSE_PUBLIC_KEY", "   ", raising=False)
    monkeypatch.setattr(settings, "LANGFUSE_SECRET_KEY", "   ", raising=False)

    from app.agent import observability

    assert observability.langfuse_enabled() is False
    assert observability.build_langfuse_handler() is None


def test_langfuse_enabled_with_keys(
    monkeypatch: pytest.MonkeyPatch,
    fake_callback_handler: MagicMock,
    stub_client_init: None,
) -> None:
    """Both keys set → enabled, callbacks + metadata wired in."""
    monkeypatch.setattr(settings, "LANGFUSE_PUBLIC_KEY", "pk-test", raising=False)
    monkeypatch.setattr(settings, "LANGFUSE_SECRET_KEY", "sk-test", raising=False)
    monkeypatch.setattr(settings, "LANGFUSE_BASE_URL", "https://cloud.langfuse.com", raising=False)
    monkeypatch.setattr(settings, "ENVIRONMENT", "dev", raising=False)

    from app.agent import observability

    assert observability.langfuse_enabled() is True

    handler = observability.build_langfuse_handler()
    assert handler is not None
    fake_callback_handler.assert_called_once()

    cfg = thread_config("game-abc")
    enriched = observability.with_observability(
        cfg,
        game_id="game-abc",
        user_id="user-xyz",
        setting_id="arctic_station_v1",
        turn_number=2,
    )

    assert "callbacks" in enriched
    assert isinstance(enriched["callbacks"], list)
    assert len(enriched["callbacks"]) == 1

    metadata = enriched["metadata"]
    assert metadata["langfuse_session_id"] == "outpost-run-game-abc"
    assert metadata["langfuse_user_id"] == "user-xyz"
    tags = metadata["langfuse_tags"]
    assert "env:dev" in tags
    assert "setting:arctic_station_v1" in tags
    # Turn number should be on metadata so spans group cleanly.
    assert metadata.get("turn_number") == 2


def test_with_observability_does_not_mutate_input(
    monkeypatch: pytest.MonkeyPatch,
    fake_callback_handler: MagicMock,
    stub_client_init: None,
) -> None:
    """The enriched dict must be a NEW dict — caller's cfg stays clean.

    Otherwise a shared `cfg` could pick up callbacks from a prior call
    and re-emit duplicate spans.
    """
    monkeypatch.setattr(settings, "LANGFUSE_PUBLIC_KEY", "pk-test", raising=False)
    monkeypatch.setattr(settings, "LANGFUSE_SECRET_KEY", "sk-test", raising=False)

    from app.agent import observability

    cfg = thread_config("game-abc")
    cfg_snapshot = {"configurable": dict(cfg["configurable"])}

    enriched = observability.with_observability(
        cfg,
        game_id="game-abc",
        user_id="user-xyz",
        setting_id="arctic_station_v1",
        turn_number=1,
    )

    assert cfg == cfg_snapshot, "with_observability mutated the input cfg in place"
    assert enriched is not cfg


def test_thread_id_preserved(
    monkeypatch: pytest.MonkeyPatch,
    fake_callback_handler: MagicMock,
    stub_client_init: None,
) -> None:
    """Invariant #12 — the enriched cfg must carry the original thread_id."""
    monkeypatch.setattr(settings, "LANGFUSE_PUBLIC_KEY", "pk-test", raising=False)
    monkeypatch.setattr(settings, "LANGFUSE_SECRET_KEY", "sk-test", raising=False)

    from app.agent import observability

    cfg = thread_config("11111111-2222-3333-4444-555555555555")
    enriched = observability.with_observability(
        cfg,
        game_id="11111111-2222-3333-4444-555555555555",
        user_id="user-xyz",
        setting_id=None,
        turn_number=None,
    )

    assert enriched["configurable"]["thread_id"] == "11111111-2222-3333-4444-555555555555"


def test_trace_metadata_omits_setting_tag_when_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No setting_id → no `setting:*` tag (still includes env tag)."""
    monkeypatch.setattr(settings, "ENVIRONMENT", "prod", raising=False)

    from app.agent import observability

    md = observability._trace_metadata(
        game_id="g1",
        user_id="u1",
        setting_id=None,
        turn_number=None,
        environment="prod",
    )
    assert md["langfuse_session_id"] == "outpost-run-g1"
    assert md["langfuse_user_id"] == "u1"
    assert "env:prod" in md["langfuse_tags"]
    assert not any(t.startswith("setting:") for t in md["langfuse_tags"])


def test_callback_handler_construction_failure_returns_none(
    monkeypatch: pytest.MonkeyPatch,
    stub_client_init: None,
) -> None:
    """`CallbackHandler()` raising must NOT bubble up — return None instead.

    Contract: observability MUST NOT raise into the SSE generator. If the
    Langfuse package is upgraded with a breaking change in `CallbackHandler`,
    we degrade to no-op tracing rather than break every /turn request.
    """
    monkeypatch.setattr(settings, "LANGFUSE_PUBLIC_KEY", "pk-test", raising=False)
    monkeypatch.setattr(settings, "LANGFUSE_SECRET_KEY", "sk-test", raising=False)

    fake_module = types.ModuleType("langfuse.langchain")

    def _boom(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("simulated handler init failure")

    fake_module.CallbackHandler = _boom  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "langfuse.langchain", fake_module)

    from app.agent import observability

    # Builder swallows the error and returns None.
    assert observability.build_langfuse_handler() is None

    # `with_observability` must therefore degrade to returning the input
    # cfg unchanged, with no callbacks/metadata attached.
    cfg = thread_config("game-abc")
    enriched = observability.with_observability(
        cfg,
        game_id="game-abc",
        user_id="user-xyz",
        setting_id="arctic_station_v1",
        turn_number=1,
    )
    assert enriched is cfg
    assert "callbacks" not in enriched
    assert "metadata" not in enriched


def test_client_init_failure_returns_false_without_raising(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`Langfuse(...)` raising at construction must NOT bubble up.

    `_ensure_langfuse_client` swallows the exception, logs a warning, and
    returns False. The cached client stays None so a future call can
    retry (e.g. once Langfuse cloud comes back).
    """
    monkeypatch.setattr(settings, "LANGFUSE_PUBLIC_KEY", "pk-test", raising=False)
    monkeypatch.setattr(settings, "LANGFUSE_SECRET_KEY", "sk-test", raising=False)

    fake_module = types.ModuleType("langfuse")

    def _boom(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("simulated client init failure")

    fake_module.Langfuse = _boom  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "langfuse", fake_module)

    from app.agent import observability

    assert observability._ensure_langfuse_client() is False
    assert observability._langfuse_client is None
    # And the public builder degrades cleanly.
    assert observability.build_langfuse_handler() is None


def test_client_init_is_cached_across_calls(
    monkeypatch: pytest.MonkeyPatch,
    fake_callback_handler: MagicMock,
) -> None:
    """`Langfuse(...)` runs once even across many `build_langfuse_handler` calls.

    Re-instantiating per turn would leak background flush threads.
    """
    monkeypatch.setattr(settings, "LANGFUSE_PUBLIC_KEY", "pk-test", raising=False)
    monkeypatch.setattr(settings, "LANGFUSE_SECRET_KEY", "sk-test", raising=False)

    construct_count = 0

    class _FakeLangfuse:
        def __init__(self, **_kwargs: object) -> None:
            nonlocal construct_count
            construct_count += 1

    fake_module = types.ModuleType("langfuse")
    fake_module.Langfuse = _FakeLangfuse  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "langfuse", fake_module)

    from app.agent import observability

    for _ in range(5):
        assert observability.build_langfuse_handler() is not None

    assert construct_count == 1, "Langfuse client should be constructed at most once"
