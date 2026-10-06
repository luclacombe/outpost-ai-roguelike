"""Unit tests for the conftest settings pin (tests/_settings_isolation.py)."""

import pytest

from app import config
from tests._settings_isolation import _PINNED, _markexpr, selects_real_llm


@pytest.mark.parametrize(
    ("markexpr", "expected"),
    [
        ("not eval and not integration", False),
        ("not integration", False),
        ("integration", True),
        ("eval", True),
        ("eval or integration", True),
        ("not eval and integration", True),
        ("", True),
    ],
)
def test_selects_real_llm(markexpr: str, expected: bool) -> None:
    assert selects_real_llm(markexpr) is expected


def test_markexpr_defaults_to_addopts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PYTEST_ADDOPTS", raising=False)
    assert _markexpr(["tests/", "-q"]) == "not eval and not integration"


def test_markexpr_last_flag_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PYTEST_ADDOPTS", "-m eval")
    assert _markexpr(["-m", "not eval", "-mintegration"]) == "integration"


def test_default_run_uses_pinned_settings() -> None:
    for name, value in _PINNED.items():
        assert getattr(config.settings, name) == value


def test_provider_order_is_pinned_empty() -> None:
    """A dev .env with LLM_PROVIDER_ORDER set would otherwise fail Settings()
    against the pinned Anthropic base URL (OpenRouter-only validation)."""
    assert _PINNED["LLM_PROVIDER_ORDER"] == []
