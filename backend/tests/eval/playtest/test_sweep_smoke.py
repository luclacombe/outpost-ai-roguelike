"""Balance-sweep smoke against local Postgres with the engine LLM mocked.

Two policies × one cell × 3 turns. Confirms the real driver path: synthetic
users, seed injection (both games start from the same colony), the
choice-spread hook, DB read-back and the written artifacts.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import respx

from scripts.sweep import SweepConfig, run_sweep


@pytest.mark.eval
async def test_sweep_writes_paired_artifacts(graph_postgres, tmp_path: Path) -> None:
    from tests.fixtures._factories import make_haiku_debate_side_effect

    out_dir = tmp_path / "sweep"
    with respx.mock(base_url="https://api.anthropic.com", assert_all_called=False) as router:
        router.post("/v1/messages").mock(side_effect=make_haiku_debate_side_effect())
        summary = await run_sweep(
            SweepConfig(
                policies=["moral", "pragmatic"],
                seeds=1,
                settings=["arctic_station_v1"],
                max_turns=3,
                concurrency=2,
                out_dir=out_dir,
                cleanup_after_run=True,
            )
        )

    assert summary["n_aborted"] == 0, summary["aborted"]
    assert summary["n_games"] == 2
    assert summary["paired"]["summary"]["n_paired_cells"] == 1
    rows = [json.loads(p.read_text()) for p in sorted((out_dir / "raw").glob("*.json"))]
    assert {r["policy"] for r in rows} == {"moral", "pragmatic"}
    assert all(r["setting"] == "arctic_station_v1" for r in rows)
    # Paired: same seed → same rolled colony length for both policies.
    assert len({r["max_turns"] for r in rows}) == 1
    assert all(r["choice_spread_summary"]["n_turns"] >= 1 for r in rows)
    assert (out_dir / "summary.md").exists()
