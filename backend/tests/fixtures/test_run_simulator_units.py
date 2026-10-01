"""DB-free unit tests for run_simulator helpers.

The full driver needs Postgres (see test_run_simulator.py, marked `eval`).
These cover the pure pieces: seed injection and persona turn-input assembly.
"""

from __future__ import annotations

from tests.fixtures.run_simulator import _eval_seed_ctx, _seed_override_from_ctx


def test_seed_override_defaults_to_none() -> None:
    assert _seed_override_from_ctx() is None


def test_seed_override_reads_task_local_seed() -> None:
    token = _eval_seed_ctx.set(4242)
    try:
        assert _seed_override_from_ctx() == 4242
    finally:
        _eval_seed_ctx.reset(token)
    assert _seed_override_from_ctx() is None
