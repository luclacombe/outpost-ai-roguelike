"""End-state distribution check.

Compares the observed frequencies of `EndState` values across a batch of
runs against per-state target bands defined in SPEC §7 / PLAN §5.3 step 5.
A state's observed frequency is "out of band" if it falls outside
`[target_min - tol, target_max + tol]`.

Pure-Python, $0 LLM.
"""

from __future__ import annotations

from collections import Counter

from pydantic import BaseModel

from app.eval.types import RunRecord
from app.game.schemas import EndState

# Stable ordering used when assembling `observed` / `targets` dicts.
_END_STATE_ORDER: tuple[EndState, ...] = (
    EndState.ENDURED,
    EndState.COLLAPSED,
    EndState.OUSTED,
    EndState.KILLED,
)

END_STATE_TARGETS: dict[EndState, tuple[float, float]] = {
    EndState.ENDURED: (0.35, 0.45),
    EndState.COLLAPSED: (0.30, 0.40),
    EndState.OUSTED: (0.10, 0.15),
    EndState.KILLED: (0.05, 0.10),
}
"""Target frequency bands per end state (PLAN §5.3 step 5)."""

END_STATE_TOLERANCE_PP = 0.10
"""±10 percentage points allowed on a 50-run sample (PLAN §5.3 step 5)."""


class EndStateDistributionResult(BaseModel):
    observed: dict[EndState, float]
    targets: dict[EndState, tuple[float, float]]
    out_of_band: list[EndState]
    n_runs: int


def end_state_distribution_check(runs: list[RunRecord]) -> EndStateDistributionResult:
    """Compute observed end-state frequencies and flag out-of-band states.

    Runs whose `end_state is None` are treated as still active and excluded
    from the denominator. With zero finished runs we return all-zero
    observed frequencies and an empty `out_of_band` list — the caller
    decides whether that's a smoke-test pass or a failure to record.
    """
    finished = [r for r in runs if r.end_state is not None]
    n_finished = len(finished)

    counter: Counter[EndState] = Counter(r.end_state for r in finished if r.end_state is not None)

    if n_finished == 0:
        observed = {state: 0.0 for state in _END_STATE_ORDER}
        return EndStateDistributionResult(
            observed=observed,
            targets={state: END_STATE_TARGETS[state] for state in _END_STATE_ORDER},
            out_of_band=[],
            n_runs=0,
        )

    observed = {state: counter.get(state, 0) / n_finished for state in _END_STATE_ORDER}

    out_of_band: list[EndState] = []
    for state in _END_STATE_ORDER:
        target_min, target_max = END_STATE_TARGETS[state]
        lo = target_min - END_STATE_TOLERANCE_PP
        hi = target_max + END_STATE_TOLERANCE_PP
        if not (lo <= observed[state] <= hi):
            out_of_band.append(state)

    return EndStateDistributionResult(
        observed=observed,
        targets={state: END_STATE_TARGETS[state] for state in _END_STATE_ORDER},
        out_of_band=out_of_band,
        n_runs=n_finished,
    )
