"""Langfuse v3 observability wiring around LangGraph invocations.

The module under test (`app.agent.observability`) wraps the canonical
`thread_config` dict produced by `app.agent.graph.thread_config` and adds
LangChain `callbacks` plus per-trace `metadata` keys when Langfuse env
vars are populated. It is also responsible for the
`langfuse_session_id` / `langfuse_user_id` / `langfuse_tags` shape that
Langfuse v3 expects on the LangGraph config metadata.

Disabled-mode coverage matters because production tests run without
Langfuse keys and must not blow up at import or call time.
"""

from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock

import pytest

from app.agent.graph import thread_config
from app.config import settings


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
    monkeypatch: pytest.MonkeyPatch, fake_callback_handler: MagicMock
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
    monkeypatch: pytest.MonkeyPatch, fake_callback_handler: MagicMock
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
    monkeypatch: pytest.MonkeyPatch, fake_callback_handler: MagicMock
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

    md = observability.trace_metadata(
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
