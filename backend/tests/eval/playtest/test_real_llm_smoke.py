"""Real-LLM smoke. Skipped unless OUTPOST_PLAYTEST_REAL_LLM=1 AND
ANTHROPIC_API_KEY is set. Runs one persona for one short game with the
actual claude CLI subprocess + actual Anthropic API."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from scripts.play_test import PlaytestConfig, run_harness

pytestmark = [pytest.mark.eval, pytest.mark.asyncio]


@pytest.mark.skipif(
    os.environ.get("OUTPOST_PLAYTEST_REAL_LLM") != "1",
    reason="OUTPOST_PLAYTEST_REAL_LLM not set; skipping real-LLM playtest",
)
async def test_real_llm_one_persona_one_game(eval_user_id, graph_postgres, tmp_path: Path) -> None:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        pytest.skip("ANTHROPIC_API_KEY not set")
    if shutil.which("claude") is None:
        pytest.skip("claude CLI not on PATH")

    out_dir = tmp_path / "playtest-out"
    shell_dir = tmp_path / "shell"

    await run_harness(
        config=PlaytestConfig(
            personas=["optimizer"],
            runs_per_persona=1,
            settings=["arctic_station_v1"],
            out_dir=out_dir,
            shell_dir=shell_dir,
            user_id=eval_user_id,
            max_turns=3,  # keep cost ≤ ~10¢
            max_concurrency=1,
        )
    )

    assert (out_dir / "runs.json").exists()
    assert any((out_dir / "raw").glob("*.json")), "expected per-run JSON archive"
