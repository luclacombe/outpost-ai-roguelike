"""Stable, paired seed allocation for the playtest harness and balance sweep.

A "cell" is one (setting_id, seed) pair. Every persona (play_test) or
policy (sweep) plays the same cells, so outcomes can be compared per cell:
same colony, same event RNG, different player. Seeds come from sha256, never
`hash()`, which Python salts per process for str.

The generator rolls content from (setting_id, seed) only and the harness
injects the seed via `app.dependencies.get_run_seed_override`, so a cell
means the same starting colony in every process. Engine-LLM output is
still sampled, so paired games share starting conditions and RNG streams,
not identical debates.
"""

from __future__ import annotations

import hashlib

_NAMESPACE = "outpost-paired"


def stable_seed(setting_id: str, run_idx: int) -> int:
    """Process-independent seed for the `run_idx`-th cell of a setting.

    Masked to 31 bits so it fits the signed `game_sessions.seed` column and
    the service's own `& 0x7FFFFFFF` convention.
    """
    digest = hashlib.sha256(f"{_NAMESPACE}:{setting_id}:{run_idx}".encode()).digest()
    return int.from_bytes(digest[:4], "big") & 0x7FFFFFFF


def paired_cells(settings: list[str], n_cells: int) -> list[tuple[str, int]]:
    """First `n_cells` cells, interleaving settings run-major.

    Order is (s0, 0), (s1, 0), (s2, 0), (s0, 1), ... so any prefix spreads
    across settings evenly and a smaller run is a prefix of a larger one.
    """
    if not settings:
        raise ValueError("settings must be non-empty")
    n = len(settings)
    return [(settings[i % n], stable_seed(settings[i % n], i // n)) for i in range(n_cells)]
