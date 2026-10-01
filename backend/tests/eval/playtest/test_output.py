"""Output emission tests — markdown + JSON to docs/playtest/<date>/."""

from __future__ import annotations

import json
from pathlib import Path

from app.eval.playtest.output import (
    PerRunArchive,
    write_corpus_artifacts,
    write_per_run_archives,
)
from app.eval.playtest.schemas import (
    PersonaTurnFlags,
    PersonaTurnOutput,
)
from tests.eval.playtest._verdicts import make_verdict


def _fake_archive() -> PerRunArchive:
    return PerRunArchive(
        run_id="run_001",
        persona_id="optimizer",
        setting_id="arctic_station_v1",
        seed=42,
        end_state="ENDURED",
        turns_played=10,
        per_turn_outputs=[
            PersonaTurnOutput(
                turn=1,
                displayed_order=["a", "b", "c"],
                chosen_proposal_id="a",
                shown_position_of_chosen=0,
                reaction="picked first.",
                flags=PersonaTurnFlags(
                    surprise=False, tone_break=False, advisor_oc=False, stakes_low=False
                ),
                quotes={},
            )
        ],
        verdict=make_verdict(bugs=["Fuel bar did not move after +12 fuel."]),
        run_record_summary={
            "tool_calls": [],
            "narration_per_turn": ["..."],
        },
        per_run_oracles={
            "tool_validity_rate": 0.97,
            "repetition_bleu2": 0.12,
            "personality_consistency": {},
        },
    )


def test_per_run_archive_writes_md_and_json(tmp_path: Path) -> None:
    archive = _fake_archive()
    write_per_run_archives(tmp_path, [archive])
    md = tmp_path / "raw" / "optimizer-arctic_station_v1-seed42.md"
    js = tmp_path / "raw" / "optimizer-arctic_station_v1-seed42.json"
    assert md.exists()
    assert js.exists()
    md_text = md.read_text()
    assert "# Run: optimizer" in md_text
    assert "## Turn 1" in md_text
    assert "Stakes felt real" in md_text
    payload = json.loads(js.read_text())
    assert payload["run_id"] == "run_001"
    assert payload["per_turn_outputs"][0]["chosen_proposal_id"] == "a"


def test_markdown_renders_baseline_fields(tmp_path: Path) -> None:
    archive = _fake_archive()
    archive.final_legitimacy = 44
    archive.final_resource_frac = 0.375
    archive.turn_context = [
        {
            "turn": 1,
            "day": 0,
            "event_title": "Coil cracks",
            "event_origin": "NATURAL",
            "chosen": {
                "id": "a",
                "name": "Cut heat",
                "flavor": "DOMINANT",
                "effects_preview": ["+12 fuel"],
            },
            "spread_l1_max": 0.25,
            "identical_options": False,
            "any_option_ends_run": True,
            "options": [
                {
                    "proposal_id": "a",
                    "name": "Cut heat",
                    "flavor": "DOMINANT",
                    "resource_deltas": {"fuel": 2},
                    "legitimacy_delta": 0,
                    "player_health_delta": 0,
                    "grievance_deltas": {},
                    "status_changes": [],
                    "rejected": ["modify_resource:would_overflow"],
                    "ends_run": None,
                },
                {
                    "proposal_id": "b",
                    "name": "Walk out",
                    "flavor": "WILDCARD",
                    "resource_deltas": {},
                    "legitimacy_delta": -5,
                    "player_health_delta": -100,
                    "grievance_deltas": {"Nadia": 1},
                    "status_changes": [],
                    "rejected": [],
                    "ends_run": "KILLED",
                },
            ],
            "oracle_error": None,
        }
    ]
    write_per_run_archives(tmp_path, [archive])
    md = (tmp_path / "raw" / "optimizer-arctic_station_v1-seed42.md").read_text()
    assert "**Final legitimacy:** 44" in md
    assert "**Event:** Coil cracks (day 0)" in md
    assert "**Cut heat** (DOMINANT); effects shown: +12 fuel" in md
    assert "**Previous pick matched expectation:** n/a" in md
    assert "suspected_bug=·" in md
    assert "spread 0.25; an option ends the run" in md
    assert "- → `a` Cut heat (DOMINANT): fuel +2; rejected modify_resource:would_overflow" in md
    assert "player health -100; Nadia grievance +1; ENDS RUN (KILLED)" in md
    assert "### Difficulty — 3/5" in md
    assert "Meaningful choices" in md
    assert "- Fuel bar did not move after +12 fuel." in md


def test_runs_json_carries_baseline_aggregates(tmp_path: Path) -> None:
    write_corpus_artifacts(
        tmp_path,
        archives=[_fake_archive()],
        findings={},
        corpus_oracles={},
        git_sha="abc",
        wall_clock_seconds=1,
    )
    payload = json.loads((tmp_path / "runs.json").read_text())
    for key in (
        "flag_aggregates",
        "verdict_axis_means",
        "difficulty",
        "outcome_expectation",
        "flagged_quotes",
        "bugs_noticed",
        "end_states",
        "choice_spread",
        "paired",
    ):
        assert key in payload, key
    row = payload["per_run_summaries"][0]
    assert row["difficulty"] == 3
    assert row["raw_md_path"] == "raw/optimizer-arctic_station_v1-seed42.md"
    assert set(payload["flag_aggregates"]) >= {"confused", "suspected_bug"}


def test_verdict_error_surfaces_in_md_and_json(tmp_path: Path) -> None:
    """Sub-bug (iii): when the per-run verdict step fails twice, the harness
    archive must record `verdict_error` (not silently emit empty axis_scores).
    Both rendered formats expose the failure mode so the meta-aggregator
    can flag `verdict_failed` runs cleanly."""
    archive = PerRunArchive(
        run_id="run_brink_orbital",
        persona_id="brinksmanship",
        setting_id="orbital_colony_v1",
        seed=598501255,
        end_state="ENDURED",  # game DID complete; only the verdict step failed
        turns_played=12,
        per_turn_outputs=[],
        verdict=None,
        verdict_error="parse error after 2 attempts: unexpected EOF",
        run_record_summary={"tool_calls": [], "narration_per_turn": []},
        per_run_oracles={},
    )
    write_per_run_archives(tmp_path, [archive])
    md_text = (tmp_path / "raw" / "brinksmanship-orbital_colony_v1-seed598501255.md").read_text()
    js_payload = json.loads(
        (tmp_path / "raw" / "brinksmanship-orbital_colony_v1-seed598501255.json").read_text()
    )

    assert "verdict_failed" in md_text.lower() or "verdict failed" in md_text.lower()
    assert "parse error" in md_text  # the failure detail must surface
    assert js_payload["verdict_error"] == "parse error after 2 attempts: unexpected EOF"


def test_corpus_artifacts_marks_run_with_verdict_error(tmp_path: Path) -> None:
    """`runs.json.per_run_summaries[i]` must distinguish `verdict_failed`
    from "verdict missing because game didn't end". The aggregator uses
    this to count silent-failure runs separately from never-finished runs."""
    archive = PerRunArchive(
        run_id="run_x",
        persona_id="optimizer",
        setting_id="arctic_station_v1",
        seed=1,
        end_state="ENDURED",
        turns_played=12,
        per_turn_outputs=[],
        verdict=None,
        verdict_error="timeout after 2 attempts",
        run_record_summary={"tool_calls": [], "narration_per_turn": []},
        per_run_oracles={},
    )
    write_corpus_artifacts(
        tmp_path,
        archives=[archive],
        findings={},
        corpus_oracles={},
        git_sha="abc",
        wall_clock_seconds=10,
    )
    payload = json.loads((tmp_path / "runs.json").read_text())
    summary = payload["per_run_summaries"][0]
    # When the verdict step failed, axis_scores stays empty AND the run
    # is flagged as verdict_failed so the aggregator can count silent
    # failures distinctly from never-finished runs.
    assert summary["axis_scores"] == {}
    assert summary["verdict_failed"] is True
    assert summary["verdict_error"] == "timeout after 2 attempts"


def test_corpus_artifacts_writes_runs_json_and_report_skeleton(tmp_path: Path) -> None:
    archives = [_fake_archive()]
    findings = {"malformed_json_count": 0, "subprocess_hangs": 0}
    corpus_oracles = {
        "inter_run_similarity": 0.71,
        "end_state_distribution": {"ENDURED": 1.0},
        "spec_target_match": False,
    }
    write_corpus_artifacts(
        tmp_path,
        archives=archives,
        findings=findings,
        corpus_oracles=corpus_oracles,
        git_sha="abc123",
        wall_clock_seconds=600,
    )
    runs_json = tmp_path / "runs.json"
    report_md = tmp_path / "report.md"
    assert runs_json.exists()
    assert report_md.exists()
    payload = json.loads(runs_json.read_text())
    assert payload["per_persona_n"] == 1
    assert "harness_findings" in payload
    assert "corpus_oracles" in payload
    report_text = report_md.read_text()
    # Honest-limits boilerplate must lead the report
    assert "obviousness detector" in report_text.lower()
    assert "META_AGGREGATOR_OUTPUT_BELOW" in report_text  # marker for aggregator
