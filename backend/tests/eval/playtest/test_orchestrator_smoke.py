"""End-to-end smoke for the orchestrator with subprocess + Anthropic mocked.

Confirms: simulate_game runs with LLMPlayer; per-run archives are written;
runs.json / report.md scaffolding lands."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest
import respx

from scripts.play_test import PlaytestConfig, run_harness


@pytest.mark.eval
async def test_orchestrator_writes_artifacts(
    monkeypatch: pytest.MonkeyPatch,
    canned_persona_response_factory,
    graph_postgres,  # provides Postgres availability skip + injects checkpointer
    tmp_path: Path,
) -> None:
    """Smoke run: 1 persona × 1 game with everything mocked. Verifies that
    the orchestrator provisions its own synthetic user, runs the game, and
    produces docs/playtest-style artifacts. cleanup_after_run=True keeps
    the test DB clean."""
    from tests.fixtures._factories import make_haiku_debate_side_effect

    # Subprocess side: dispatch on the user_message shape.
    #   - Turn calls (first/subsequent_turn) include 'displayed_order' in the
    #     prompt; return a canned PersonaTurnOutput pinned to the first option.
    #   - Final-verdict calls don't include 'displayed_order'; return a canned
    #     PersonaRunVerdict so PersonaRunVerdict.model_validate succeeds.
    def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
        import ast

        user_msg = cmd[-1]
        if "displayed_order" in user_msg:
            line = next(line for line in user_msg.splitlines() if "displayed_order" in line)
            bracket_start = line.find("[")
            bracket_end = line.rfind("]") + 1
            order = ast.literal_eval(line[bracket_start:bracket_end])
            canned = canned_persona_response_factory(
                chosen_proposal_id=order[0], displayed_order=order
            )
        else:
            verdict_inner = {
                "axes": {
                    "stakes_felt_real": {"score": 3, "rationale": "ok", "turn_citations": []},
                    "tonal_coherence": {"score": 3, "rationale": "ok", "turn_citations": []},
                    "advisor_distinctness": {"score": 3, "rationale": "ok", "turn_citations": []},
                    "dramatic_arc": {"score": 3, "rationale": "ok", "turn_citations": []},
                },
                "continue_tomorrow": {"decision": True, "reason": "ok"},
                "closing_paragraph": "ok",
            }
            envelope = {
                "type": "result",
                "session_id": "test-session-abc123",
                "duration_ms": 1500,
                "stop_reason": "end_turn",
                "result": "```json\n" + json.dumps(verdict_inner) + "\n```",
                "usage": {"input_tokens": 1500, "output_tokens": 80},
            }
            canned = json.dumps(envelope)
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout=canned, stderr="")

    monkeypatch.setattr("app.eval.playtest.persona_session.subprocess.run", fake_run)

    out_dir = tmp_path / "playtest-out"

    with respx.mock(base_url="https://api.anthropic.com", assert_all_called=False) as router:
        router.post("/v1/messages").mock(side_effect=make_haiku_debate_side_effect())

        await run_harness(
            config=PlaytestConfig(
                personas=["optimizer"],
                runs_per_persona=1,
                settings=["arctic_station_v1"],
                out_dir=out_dir,
                shell_dir=tmp_path / "shell",
                max_turns=3,
                max_concurrency=1,
                cleanup_after_run=True,
            )
        )

    assert (out_dir / "runs.json").exists()
    assert (out_dir / "report.md").exists()
    raw = out_dir / "raw"
    assert raw.is_dir()
    md_files = list(raw.glob("*.md"))
    json_files = list(raw.glob("*.json"))
    assert len(md_files) == 1
    assert len(json_files) == 1
