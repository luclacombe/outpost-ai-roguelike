"""Daily gate — server-authoritative limit of one run per user per UTC day.

Called from /me (to report whether the user is eligible now and, if not,
when) and from the game session creation router (to reject a second run
request). Keeping this in pure functions means the game router will own
the atomic "check and set last_run_started_at" inside a single DB
transaction — the gate here just answers yes/no given a prior timestamp.
"""

from datetime import UTC, datetime, timedelta


def next_run_at(last_started: datetime) -> datetime:
    """Return the first 00:00 UTC strictly after `last_started`.

    Normalizes the input to UTC first so callers can pass any tz-aware
    datetime (a Postgres `timestamptz` comes back in UTC by default, but
    we don't want to rely on that).
    """
    as_utc = last_started.astimezone(UTC)
    midnight = as_utc.replace(hour=0, minute=0, second=0, microsecond=0)
    return midnight + timedelta(days=1)


def can_start_run(last_started: datetime | None) -> bool:
    if last_started is None:
        return True
    return datetime.now(UTC) >= next_run_at(last_started)
