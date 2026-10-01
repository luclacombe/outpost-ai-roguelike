"""Real-LLM smoke. Skipped unless OUTPOST_PLAYTEST_REAL_LLM=1 AND an engine
LLM key is configured (`settings.llm_api_key`: LLM_API_KEY, else
ANTHROPIC_API_KEY, from env or `.env`). Runs one persona for one short game
with the actual claude CLI subprocess + the configured engine provider."""

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
async def test_real_llm_one_persona_one_game(graph_postgres, tmp_path: Path) -> None:
    from app.config import settings

    if not (settings.LLM_API_KEY or settings.ANTHROPIC_API_KEY):
        pytest.skip("no engine LLM key configured (LLM_API_KEY / ANTHROPIC_API_KEY)")
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
            max_turns=3,  # keep cost ≤ ~10¢
            max_concurrency=1,
            cleanup_after_run=True,
        )
    )

    assert (out_dir / "runs.json").exists()
    assert any((out_dir / "raw").glob("*.json")), "expected per-run JSON archive"
