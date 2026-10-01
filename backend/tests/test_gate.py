"""Daily-gate unit tests.

Pure-function checks — no DB, no clock injection. The gate asks "is
now past the next 00:00 UTC after the last run started?" so the tests
lean on `datetime.now(timezone.utc)` directly: a run "now" blocks, a run
from >24h ago with the midnight boundary crossed unblocks.
"""

from datetime import UTC, datetime, timedelta, timezone

from app.auth.gate import can_start_run, next_run_at


def test_can_start_when_no_prior_run():
    assert can_start_run(None) is True


def test_cannot_start_same_day():
    now = datetime.now(UTC)
    assert can_start_run(now) is False


def test_can_start_next_day():
    # A run that started yesterday >1h past midnight is always unblocked
    # because the gate rolls over at the next 00:00 UTC, not at 24h elapsed.
    yesterday = datetime.now(UTC) - timedelta(days=1, hours=1)
    assert can_start_run(yesterday) is True


def test_next_run_at_returns_midnight_utc_after_last():
    last = datetime(2026, 4, 23, 14, 30, tzinfo=UTC)
    expected = datetime(2026, 4, 24, 0, 0, tzinfo=UTC)
    assert next_run_at(last) == expected


def test_next_run_at_normalizes_non_utc_input():
    """Input in a non-UTC timezone still resolves to the same UTC wall-clock
    day boundary — the gate is always in UTC regardless of caller tz."""
    paris = timezone(timedelta(hours=2))
    last = datetime(2026, 4, 23, 23, 30, tzinfo=paris)  # 21:30 UTC
    # 21:30 UTC on 04-23 → next midnight UTC is 04-24 00:00
    expected = datetime(2026, 4, 24, 0, 0, tzinfo=UTC)
    assert next_run_at(last) == expected


def test_bypass_allows_same_day_run():
    assert can_start_run(datetime.now(UTC), bypass=True) is True


def test_bypass_off_by_default_keeps_gate():
    assert can_start_run(datetime.now(UTC), bypass=False) is False
