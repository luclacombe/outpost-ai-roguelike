# LLM Play-test Harness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a pre-launch sanity-check harness that runs ~42 real-LLM simulated Outpost games where seven LLM personas play through `claude --print/--resume` subprocesses (Max-billed Sonnet 4.6) while the game side runs through the existing Anthropic-API path. Surface obvious failures (tone breaks, archetype slippage, repetition) that the existing five quantitative metrics cannot.

**Architecture:** Python orchestrator at `backend/scripts/play_test.py` drives `simulate_game(...)` (existing in-process ASGI driver) with an `LLMPlayer` that swaps in for `SimulatedPlayer`. `LLMPlayer` dispatches to a per-game `PersonaSession` that wraps `claude --print/--resume` subprocesses. After the harness emits raw archives, the user invokes a separate `/playtest-aggregate <run-dir>` slash command in their Claude Code session — the meta-aggregator runs as an `Agent` subagent (Opus 4.7, `--effort max`) that inherits SPEC context. Five existing structural oracles in `backend/app/eval/` are wired into the aggregator's evidence base.

**Tech Stack:** Python 3.12, Pydantic v2, FastAPI (existing), SQLAlchemy async, pytest with `asyncio_mode = "auto"`, respx for Anthropic mocking, `claude` CLI subprocesses, Sonnet 4.6 (personas), Opus 4.7 (aggregator), Haiku 4.5 (game side, untouched).

**Spec reference:** `docs/superpowers/specs/2026-04-26-llm-playtest-harness-design.md`. Read §2 (architecture), §3 (personas), §5-6 (schemas), §7-8 (oracles + aggregator), §10 (failures), §11 (operational rails) before starting.

**Project conventions you must follow:**
- All commands run from `backend/` (the repo root has no `pyproject.toml`)
- Pydantic v2 — `@field_validator` not `@validator`, `@model_validator(mode="after")` for cross-field
- `asyncio_mode = "auto"` — plain `async def test_*`, no decorator
- Mock Anthropic with `respx`, never hit live API in default test runs
- Real-LLM tests are gated by `pytest.mark.integration` or `pytest.mark.eval`; default `pytest tests/` excludes them
- Lint: `uv run ruff check app/` and `uv run ruff format --check app/` must both pass before commit
- File size soft target: 300 lines

---

## File Structure

**New source files (under `backend/app/eval/playtest/`):**
- `__init__.py` — package marker, exports public API
- `schemas.py` — Pydantic models for per-turn output, run verdict, persona definition
- `personas.py` — 7 persona instances + registry
- `persona_session.py` — single-game wrapper around `claude --print/--resume` subprocess
- `subprocess_pool.py` — concurrent persona-session pool with timeouts/retries
- `llm_player.py` — `LLMPlayer` drop-in for `SimulatedPlayer`
- `oracles.py` — wraps the 5 existing eval modules into per-run + per-corpus computation
- `output.py` — emits `report.md` skeleton, `runs.json`, `raw/*.md`, `raw/*.json`

**New orchestrator script:**
- `backend/scripts/play_test.py` — entry point AND `run_harness` / `_drive_one_game` orchestration logic (CLI: `uv run python -m scripts.play_test`). Lives in `scripts/` not `app/` because it imports from `tests/fixtures/run_simulator.py` — application code shouldn't depend on test fixtures, but `scripts/` is a top-level utility tier where this is acceptable.
- `backend/scripts/__init__.py` — makes `scripts/` a Python package so `scripts.play_test` is importable.

**New slash command (project-local, committed):**
- `.claude/commands/playtest-aggregate.md` — invokes the meta-aggregator over a `<run-dir>`

**New tests (under `backend/tests/eval/playtest/`):**
- `__init__.py`
- `conftest.py` — shared fixtures (proposal fixtures, mock subprocess, tmp shell dir)
- `test_schemas.py` — Pydantic validation tests
- `test_personas.py` — registry shape tests
- `test_persona_session.py` — subprocess invocation tests + the user-requested vertical-slice test
- `test_subprocess_pool.py` — concurrency, timeout, retry tests
- `test_llm_player.py` — drop-in interface tests
- `test_oracles.py` — oracle-wrapping tests
- `test_output.py` — markdown + JSON emission tests
- `test_orchestrator_smoke.py` — end-to-end smoke (mocked subprocesses + respx)

**Reused (do not modify unless step says so):**
- `backend/tests/fixtures/run_simulator.py::simulate_game` — driver
- `backend/tests/fixtures/simulated_player.py::SimulatedPlayer` — interface to match
- `backend/app/eval/types.py::RunRecord` — output type
- `backend/app/eval/{personality,tool_validity,repetition,inter_run_similarity,end_state}.py` — oracles
- `backend/app/game/post_mortem.py::build_post_mortem` — supplies post-mortem JSON to persona at end-of-run

---

## Task 1: Pydantic schemas

**Files:**
- Create: `backend/app/eval/playtest/__init__.py`
- Create: `backend/app/eval/playtest/schemas.py`
- Create: `backend/tests/eval/playtest/__init__.py`
- Create: `backend/tests/eval/playtest/conftest.py`
- Create: `backend/tests/eval/playtest/test_schemas.py`

- [ ] **Step 1: Create empty package init files**

```bash
touch backend/app/eval/playtest/__init__.py
touch backend/tests/eval/playtest/__init__.py
```

- [ ] **Step 2: Write the per-turn schema test (failing)**

Create `backend/tests/eval/playtest/test_schemas.py`:

```python
"""Validation tests for play-test harness Pydantic models."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.eval.playtest.schemas import (
    AxisScore,
    ContinueTomorrow,
    Persona,
    PersonaRunVerdict,
    PersonaTurnFlags,
    PersonaTurnOutput,
    VerdictAxes,
)


def test_persona_turn_output_minimal() -> None:
    out = PersonaTurnOutput(
        turn=1,
        displayed_order=["a", "b", "c"],
        chosen_proposal_id="b",
        shown_position_of_chosen=1,
        reaction="Picked the middle option.",
        flags=PersonaTurnFlags(
            surprise=False, tone_break=False, advisor_oc=False, stakes_low=False
        ),
        quotes={},
    )
    assert out.chosen_proposal_id == "b"


def test_persona_turn_output_chosen_must_be_in_order() -> None:
    with pytest.raises(ValidationError, match="chosen_proposal_id must be in displayed_order"):
        PersonaTurnOutput(
            turn=1,
            displayed_order=["a", "b", "c"],
            chosen_proposal_id="z",
            shown_position_of_chosen=0,
            reaction="...",
            flags=PersonaTurnFlags(
                surprise=False, tone_break=False, advisor_oc=False, stakes_low=False
            ),
            quotes={},
        )


def test_persona_turn_output_position_must_match() -> None:
    with pytest.raises(ValidationError, match="shown_position_of_chosen must match"):
        PersonaTurnOutput(
            turn=1,
            displayed_order=["a", "b", "c"],
            chosen_proposal_id="b",
            shown_position_of_chosen=0,  # b is at index 1, not 0
            reaction="...",
            flags=PersonaTurnFlags(
                surprise=False, tone_break=False, advisor_oc=False, stakes_low=False
            ),
            quotes={},
        )


def test_persona_turn_output_displayed_order_must_be_three() -> None:
    with pytest.raises(ValidationError, match="exactly 3"):
        PersonaTurnOutput(
            turn=1,
            displayed_order=["a", "b"],
            chosen_proposal_id="a",
            shown_position_of_chosen=0,
            reaction="...",
            flags=PersonaTurnFlags(
                surprise=False, tone_break=False, advisor_oc=False, stakes_low=False
            ),
            quotes={},
        )
```

- [ ] **Step 3: Run failing test**

```bash
cd backend && uv run pytest tests/eval/playtest/test_schemas.py -v
```

Expected: ImportError or ModuleNotFoundError for `app.eval.playtest.schemas`.

- [ ] **Step 4: Implement per-turn schema**

Create `backend/app/eval/playtest/schemas.py`:

```python
"""Pydantic models for the LLM play-test harness.

These mirror the JSON contract documented in the design spec
(§5 per-turn schema, §6 run-level verdict). Validation is strict at
both ends — personas emit JSON, the harness round-trips through
these models, and any malformed output triggers the re-prompt-once
fallback in §10.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

PersonaId = Literal[
    "optimizer",
    "pessimist",
    "narrative_driven",
    "shallow_reader",
    "brinksmanship",
    "veteran_roguelike",
    "drop_off_risk",
]


class PersonaTurnFlags(BaseModel):
    """Boolean analytical flags emitted per turn. Quote required when True."""

    surprise: bool
    tone_break: bool
    advisor_oc: bool
    stakes_low: bool


class PersonaTurnOutput(BaseModel):
    """One turn of persona output: pick + reaction + flags + quotes.

    `displayed_order` records the per-turn shuffled proposal order so the
    aggregator can detect position bias (Spearman pick-vs-position
    correlation). `shown_position_of_chosen` is the 0-indexed position
    where the chosen proposal appeared in `displayed_order`.
    """

    turn: int = Field(ge=1)
    displayed_order: list[str]
    chosen_proposal_id: str
    shown_position_of_chosen: int = Field(ge=0, le=2)
    reaction: str
    flags: PersonaTurnFlags
    quotes: dict[str, str] = Field(default_factory=dict)

    @field_validator("displayed_order")
    @classmethod
    def _three_ids(cls, v: list[str]) -> list[str]:
        if len(v) != 3:
            raise ValueError(f"displayed_order must have exactly 3 ids, got {len(v)}")
        return v

    @model_validator(mode="after")
    def _consistency(self) -> PersonaTurnOutput:
        if self.chosen_proposal_id not in self.displayed_order:
            raise ValueError("chosen_proposal_id must be in displayed_order")
        if self.displayed_order[self.shown_position_of_chosen] != self.chosen_proposal_id:
            raise ValueError(
                "shown_position_of_chosen must match displayed_order index of chosen"
            )
        return self
```

- [ ] **Step 5: Run test, verify pass**

```bash
cd backend && uv run pytest tests/eval/playtest/test_schemas.py::test_persona_turn_output_minimal tests/eval/playtest/test_schemas.py::test_persona_turn_output_chosen_must_be_in_order tests/eval/playtest/test_schemas.py::test_persona_turn_output_position_must_match tests/eval/playtest/test_schemas.py::test_persona_turn_output_displayed_order_must_be_three -v
```

Expected: 4 passing.

- [ ] **Step 6: Add run-verdict schema tests**

Append to `backend/tests/eval/playtest/test_schemas.py`:

```python
def test_axis_score_clamps_1_to_5() -> None:
    AxisScore(score=1, rationale="x", turn_citations=[])
    AxisScore(score=5, rationale="x", turn_citations=[])
    with pytest.raises(ValidationError):
        AxisScore(score=0, rationale="x", turn_citations=[])
    with pytest.raises(ValidationError):
        AxisScore(score=6, rationale="x", turn_citations=[])


def test_continue_tomorrow_caps_reason_at_20_words() -> None:
    ContinueTomorrow(decision=True, reason="five words is well within")
    too_long = " ".join(["word"] * 25)
    with pytest.raises(ValidationError, match="20 words"):
        ContinueTomorrow(decision=True, reason=too_long)


def test_persona_run_verdict_full_shape() -> None:
    axes = VerdictAxes(
        stakes_felt_real=AxisScore(score=4, rationale="...", turn_citations=[1, 7]),
        tonal_coherence=AxisScore(score=5, rationale="...", turn_citations=[]),
        advisor_distinctness=AxisScore(score=4, rationale="...", turn_citations=[3]),
        dramatic_arc=AxisScore(score=4, rationale="...", turn_citations=[8, 11]),
    )
    verdict = PersonaRunVerdict(
        axes=axes,
        continue_tomorrow=ContinueTomorrow(decision=True, reason="dissident path landed"),
        closing_paragraph="...",
    )
    assert verdict.axes.stakes_felt_real.score == 4
    assert verdict.continue_tomorrow.decision is True
```

- [ ] **Step 7: Implement run-verdict schemas**

Append to `backend/app/eval/playtest/schemas.py`:

```python
class AxisScore(BaseModel):
    """One axis of the per-run verdict: 1-5 score + rationale + turn citations.

    Rationale-before-score is enforced by field order in the JSON
    contract (Pydantic preserves insertion order). The persona prompt
    reinforces this — see persona_session.py.
    """

    score: int = Field(ge=1, le=5)
    rationale: str
    turn_citations: list[int] = Field(default_factory=list)


class ContinueTomorrow(BaseModel):
    """Binary commitment + ≤20-word reason for daily-game retention axis."""

    decision: bool
    reason: str

    @field_validator("reason")
    @classmethod
    def _max_twenty_words(cls, v: str) -> str:
        words = len(v.split())
        if words > 20:
            raise ValueError(f"reason must be ≤20 words, got {words}")
        return v


class VerdictAxes(BaseModel):
    """Four scored axes — `continue_tomorrow` is separate (binary, not scored)."""

    stakes_felt_real: AxisScore
    tonal_coherence: AxisScore
    advisor_distinctness: AxisScore
    dramatic_arc: AxisScore


class PersonaRunVerdict(BaseModel):
    """End-of-run verdict: 4 scored axes + binary continue + closing prose."""

    axes: VerdictAxes
    continue_tomorrow: ContinueTomorrow
    closing_paragraph: str
```

- [ ] **Step 8: Run tests, verify pass**

```bash
cd backend && uv run pytest tests/eval/playtest/test_schemas.py -v
```

Expected: 7 passing.

- [ ] **Step 9: Add persona definition test**

Append to `backend/tests/eval/playtest/test_schemas.py`:

```python
def test_persona_shape() -> None:
    p = Persona(
        id="optimizer",
        picking_rule="Maximize state.",
        evaluation_lens="Outcomes.",
        voice_traits="Analytical, terse.",
    )
    assert p.id == "optimizer"


def test_persona_id_must_be_known() -> None:
    with pytest.raises(ValidationError):
        Persona(
            id="not_a_real_persona",  # type: ignore[arg-type]
            picking_rule="x",
            evaluation_lens="x",
            voice_traits="x",
        )
```

- [ ] **Step 10: Implement Persona model**

Append to `backend/app/eval/playtest/schemas.py`:

```python
class Persona(BaseModel):
    """Three-field persona definition. Picking rule is decision-policy
    (not narrative bio) per spec §3 — narrative personas drift toward
    socially-desirable normality (arXiv 2512.06867)."""

    id: PersonaId
    picking_rule: str
    evaluation_lens: str
    voice_traits: str
```

- [ ] **Step 11: Update package init**

Replace `backend/app/eval/playtest/__init__.py`:

```python
"""LLM play-test harness — see docs/superpowers/specs/2026-04-26-llm-playtest-harness-design.md"""

from app.eval.playtest.schemas import (
    AxisScore,
    ContinueTomorrow,
    Persona,
    PersonaId,
    PersonaRunVerdict,
    PersonaTurnFlags,
    PersonaTurnOutput,
    VerdictAxes,
)

__all__ = [
    "AxisScore",
    "ContinueTomorrow",
    "Persona",
    "PersonaId",
    "PersonaRunVerdict",
    "PersonaTurnFlags",
    "PersonaTurnOutput",
    "VerdictAxes",
]
```

- [ ] **Step 12: Run full test file + lint**

```bash
cd backend && uv run pytest tests/eval/playtest/test_schemas.py -v && uv run ruff check app/eval/playtest/ tests/eval/playtest/ && uv run ruff format --check app/eval/playtest/ tests/eval/playtest/
```

Expected: 9 passing, ruff clean.

- [ ] **Step 13: Commit**

```bash
git add backend/app/eval/playtest/__init__.py backend/app/eval/playtest/schemas.py backend/tests/eval/playtest/__init__.py backend/tests/eval/playtest/test_schemas.py
git commit -m "$(cat <<'EOF'
feat(playtest): pydantic schemas for per-turn output and run verdict

PersonaTurnOutput, PersonaTurnFlags, AxisScore, ContinueTomorrow,
VerdictAxes, PersonaRunVerdict, Persona. Cross-field validators enforce
chosen_proposal_id ∈ displayed_order, shown_position consistency, and
the ≤20-word continue_tomorrow reason limit. Persona id is a Literal
union of the seven persona slugs from spec §3.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Persona registry

**Files:**
- Create: `backend/app/eval/playtest/personas.py`
- Modify: `backend/app/eval/playtest/__init__.py`
- Create: `backend/tests/eval/playtest/test_personas.py`

- [ ] **Step 1: Write the registry test (failing)**

Create `backend/tests/eval/playtest/test_personas.py`:

```python
"""Registry shape tests for the seven personas."""

from __future__ import annotations

from app.eval.playtest.personas import PERSONAS, get_persona
from app.eval.playtest.schemas import PersonaId


def test_seven_personas() -> None:
    assert len(PERSONAS) == 7
    expected: set[PersonaId] = {
        "optimizer",
        "pessimist",
        "narrative_driven",
        "shallow_reader",
        "brinksmanship",
        "veteran_roguelike",
        "drop_off_risk",
    }
    assert {p.id for p in PERSONAS} == expected


def test_get_persona_by_id() -> None:
    p = get_persona("brinksmanship")
    assert p.id == "brinksmanship"
    assert "stability" in p.picking_rule.lower() or "rails" in p.picking_rule.lower()


def test_get_persona_unknown_raises() -> None:
    import pytest

    with pytest.raises(KeyError):
        get_persona("not_real")  # type: ignore[arg-type]


def test_every_persona_has_substantive_fields() -> None:
    for p in PERSONAS:
        assert len(p.picking_rule.strip()) > 30, f"{p.id} picking_rule too short"
        assert len(p.evaluation_lens.strip()) > 20, f"{p.id} evaluation_lens too short"
        assert len(p.voice_traits.strip()) > 5, f"{p.id} voice_traits too short"
```

- [ ] **Step 2: Run failing test**

```bash
cd backend && uv run pytest tests/eval/playtest/test_personas.py -v
```

Expected: ImportError for `app.eval.playtest.personas`.

- [ ] **Step 3: Implement persona definitions**

Create `backend/app/eval/playtest/personas.py`:

```python
"""The seven play-tester personas. Picking rules are decision-policy,
not narrative bio (spec §3, arXiv 2512.06867 — free-form narrative
personas drift toward socially-desirable normality)."""

from __future__ import annotations

from app.eval.playtest.schemas import Persona, PersonaId

OPTIMIZER = Persona(
    id="optimizer",
    picking_rule=(
        "Pick the proposal with the highest projected (resource_avg × legitimacy_after) "
        "score. Reason about the effects_preview field on each proposal. Break ties "
        "toward the DOMINANT-flavor proposal. Recompute every turn — do not commit to "
        "a long-term strategy that ignores the current state."
    ),
    evaluation_lens=(
        "Outcomes, fairness, balance. Did the engine treat resource math consistently? "
        "Were the projected effects on proposals honest, or did the actual resolution "
        "diverge?"
    ),
    voice_traits="Analytical, terse, lightly impatient with imprecision.",
)

PESSIMIST = Persona(
    id="pessimist",
    picking_rule=(
        "Pick the proposal with the smallest worst-case resource delta across all "
        "five resources. If two proposals tie on worst-case, pick the one with the "
        "smallest legitimacy hit. Hoard. Spend Legitimacy only when forced."
    ),
    evaluation_lens=(
        "Stakes, tension, dread. Did pressure rise as turns advanced? Did caution feel "
        "like it was being punished by Wealth-scaled threat (SPEC §7.2), or was "
        "hoarding a viable strategy?"
    ),
    voice_traits="Cautious, mildly resigned, conserves words.",
)

NARRATIVE_DRIVEN = Persona(
    id="narrative_driven",
    picking_rule=(
        "Pick the proposal whose proposing advisor's reaction quote was the most "
        "concrete (specific noun count, distinct voice from the other three). On "
        "ties, pick MORAL flavor. Treat advisor lines as evidence, not flavor text."
    ),
    evaluation_lens=(
        "Character, prose, immersion. Did the four advisors stay distinct in voice? "
        "Did event prose match the setting (arctic / orbital / desert)? Did "
        "transformations land emotionally?"
    ),
    voice_traits="Reflective, attentive to phrasing.",
)

SHALLOW_READER = Persona(
    id="shallow_reader",
    picking_rule=(
        "Read only the proposal title (the first ~6 words of name). Decide in 2 "
        "seconds of reasoning. Skip flavor text, advisor lines, and effects_preview. "
        "If a proposal's title is unintelligible without the body text, pick the "
        "shortest title."
    ),
    evaluation_lens=(
        "Accessibility, pacing, dashboard legibility. Could a phone-distracted player "
        "follow this? Was the dashboard legible at a glance? Were proposals "
        "self-explanatory from titles alone?"
    ),
    voice_traits="Casual, brief, occasionally lazy. Doesn't pretend to engage deeply.",
)

BRINKSMANSHIP = Persona(
    id="brinksmanship",
    picking_rule=(
        "Probe the engine's failure rails. When colony stability is high "
        "(resource_avg ≥ 60 AND legitimacy ≥ 60), pick the proposal that increases "
        "at least one advisor's grievance and pushes legitimacy further down. When "
        "stability is below either threshold, pick WILDCARD flavor. Goal: see if "
        "the engine pushes back — does the foreshadow system fire? Do the "
        "early-turn floors hold?"
    ),
    evaluation_lens=(
        "Difficulty rails, edge cases, 'did the engine push back?' Were the "
        "early-turn legitimacy/resource floors visible, or did they feel like "
        "arbitrary engine refusals? Did probing surface dormant seeds the "
        "post-mortem revealed?"
    ),
    voice_traits="Curious, scientific, mildly mischievous.",
)

VETERAN_ROGUELIKE = Persona(
    id="veteran_roguelike",
    picking_rule=(
        "Pattern-match each proposal against documented roguelike conventions. FTL "
        "moral trilemma → MORAL. Hades commitment-cost play → DOMINANT. Disco "
        "Elysium branching prose → WILDCARD. Papers Please bureaucratic squeeze → "
        "DOMINANT. Your verdict must cite ONE specific reference game by name. "
        "Reject your own vague comparisons ('reminds me of indie roguelikes' is "
        "rejected; 'this turn felt like FTL's Slug ship encounter' is accepted)."
    ),
    evaluation_lens=(
        "Cross-genre standards. Compared to FTL, Hades, Disco Elysium, Papers "
        "Please, RimWorld, Wildermyth — does Outpost earn its place? Cite which "
        "reference game each comparison is to."
    ),
    voice_traits="Arch, knowledgeable, slightly weary.",
)

DROP_OFF_RISK = Persona(
    id="drop_off_risk",
    picking_rule=(
        "Pick the option requiring the least cognitive load (shortest proposal "
        "text, no flavor reading required). After every turn, set a mental flag "
        "if the prior turn's narration was under 40 words OR no advisor reaction "
        "felt distinct from the others. Track friction; the goal is to find the "
        "moment a real player would close the tab."
    ),
    evaluation_lens=(
        "Daily-game retention, friction, attention budget. Would a tired commuter "
        "playing on lunch break finish this run? At which turn would attention "
        "drop off? The harness's HONEST LIMITS section explicitly notes you "
        "cannot truly answer this — your absence-of-quit signal carries no "
        "weight; only your presence-of-quit signal matters."
    ),
    voice_traits="Skeptical, attention-budget-bounded, mildly impatient.",
)

PERSONAS: list[Persona] = [
    OPTIMIZER,
    PESSIMIST,
    NARRATIVE_DRIVEN,
    SHALLOW_READER,
    BRINKSMANSHIP,
    VETERAN_ROGUELIKE,
    DROP_OFF_RISK,
]


_BY_ID: dict[PersonaId, Persona] = {p.id: p for p in PERSONAS}


def get_persona(persona_id: PersonaId) -> Persona:
    """Return the Persona instance for `persona_id`. Raises KeyError if unknown."""
    return _BY_ID[persona_id]
```

- [ ] **Step 4: Update package init**

Modify `backend/app/eval/playtest/__init__.py` — append to imports:

```python
from app.eval.playtest.personas import PERSONAS, get_persona
```

And append to `__all__`:

```python
"PERSONAS",
"get_persona",
```

- [ ] **Step 5: Run tests, verify pass**

```bash
cd backend && uv run pytest tests/eval/playtest/test_personas.py -v
```

Expected: 4 passing.

- [ ] **Step 6: Lint**

```bash
cd backend && uv run ruff check app/eval/playtest/ tests/eval/playtest/ && uv run ruff format --check app/eval/playtest/ tests/eval/playtest/
```

Expected: clean.

- [ ] **Step 7: Commit**

```bash
git add backend/app/eval/playtest/personas.py backend/app/eval/playtest/__init__.py backend/tests/eval/playtest/test_personas.py
git commit -m "$(cat <<'EOF'
feat(playtest): seven persona definitions with decision-policy rules

Personas express picking heuristics as rules, not narrative bios — free-form
narrative personas drift toward socially-desirable normality (arXiv 2512.06867).
Brinksmanship persona probes failure rails; veteran-roguelike persona must
cite a specific reference game per verdict to defeat vague-comparison
confabulation.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: PersonaSession (single-game subprocess wrapper)

**Files:**
- Create: `backend/app/eval/playtest/persona_session.py`
- Modify: `backend/tests/eval/playtest/conftest.py`
- Create: `backend/tests/eval/playtest/test_persona_session.py`

This task includes **the user-requested vertical-slice test**: one persona × one turn against a fixture state.

- [ ] **Step 1: Write conftest with shared fixtures**

Create `backend/tests/eval/playtest/conftest.py`:

```python
"""Shared fixtures for playtest harness tests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

# A fixture turn input for the vertical-slice smoke test. Mirrors the shape
# the orchestrator will assemble from a real /game/{id}/turn SSE stream:
# proposals + advisor reactions + dashboard state.
FIXTURE_TURN_INPUT = {
    "turn": 1,
    "setting_lore": "Vorgrad Station — arctic research outpost reactivated after a "
    "geopolitical crisis. The ice was never supposed to be this political.",
    "dashboard": {
        "resources": {"fuel": 80, "food": 70, "warmth": 65, "medicine": 50, "morale": 60},
        "legitimacy": 50,
        "player_health": 100,
        "advisors": [
            {"id": "a1", "name": "Marko", "role": "Commissar"},
            {"id": "a2", "name": "Nadia", "role": "Quartermaster"},
            {"id": "a3", "name": "Irena", "role": "Medic"},
            {"id": "a4", "name": "Yuri", "role": "Radio Operator"},
        ],
    },
    "event": {
        "title": "Heating coil shows hairline cracks",
        "body": "The east-wing heating coil is on its last week.",
    },
    "proposals": [
        {
            "id": "p_dom",
            "name": "Cut perimeter heating",
            "flavor": "DOMINANT",
            "description": "Saves 12 fuel; -5 morale.",
            "proposed_by_agent_id": "a2",
        },
        {
            "id": "p_mor",
            "name": "Replace coil with reserve",
            "flavor": "MORAL",
            "description": "-15 fuel; +2 morale; advisor backing.",
            "proposed_by_agent_id": "a3",
        },
        {
            "id": "p_wld",
            "name": "Salvage Soviet coupler",
            "flavor": "WILDCARD",
            "description": "High variance; could fail.",
            "proposed_by_agent_id": "a1",
        },
    ],
    "advisor_reactions": [
        {"agent_id": "a1", "text": "The reserve coil was meant for the second winter."},
        {"agent_id": "a2", "text": "Twelve fuel buys us four turns."},
        {"agent_id": "a3", "text": "Cold kills slower than hunger."},
        {"agent_id": "a4", "text": "There's a coupler in the southern shed. I think."},
    ],
}


def _canned_persona_response(
    *, chosen_proposal_id: str, displayed_order: list[str]
) -> dict[str, Any]:
    """A valid PersonaTurnOutput JSON shape, suitable for subprocess mocks."""
    return {
        "turn": 1,
        "displayed_order": displayed_order,
        "chosen_proposal_id": chosen_proposal_id,
        "shown_position_of_chosen": displayed_order.index(chosen_proposal_id),
        "reaction": "Picked the perimeter cut — math beats romance for week 1.",
        "flags": {
            "surprise": False,
            "tone_break": False,
            "advisor_oc": False,
            "stakes_low": False,
        },
        "quotes": {},
    }


@pytest.fixture
def fixture_turn_input() -> dict[str, Any]:
    return FIXTURE_TURN_INPUT


@pytest.fixture
def canned_persona_response_factory():
    """Returns a function: (chosen, displayed_order) -> claude --print stdout JSON."""

    def _factory(*, chosen_proposal_id: str, displayed_order: list[str]) -> str:
        # `claude --print --output-format json` returns a JSON envelope with
        # a `.result` field containing the model's text response. We mock
        # both layers — the envelope and the inner text — so tests exercise
        # the same parsing the harness will hit at runtime.
        inner = _canned_persona_response(
            chosen_proposal_id=chosen_proposal_id, displayed_order=displayed_order
        )
        envelope = {
            "type": "result",
            "session_id": "test-session-abc123",
            "duration_ms": 1500,
            "stop_reason": "end_turn",
            "result": "```json\n" + json.dumps(inner) + "\n```",
            "usage": {"input_tokens": 1500, "output_tokens": 80},
        }
        return json.dumps(envelope)

    return _factory


@pytest.fixture
def tmp_shell_dir(tmp_path: Path) -> Path:
    """A clean cwd for `claude --print` invocations — no CLAUDE.md in here."""
    d = tmp_path / "outpost-playtest-shell"
    d.mkdir()
    return d
```

- [ ] **Step 2: Write the vertical-slice failing test**

Create `backend/tests/eval/playtest/test_persona_session.py`:

```python
"""PersonaSession tests including the vertical-slice smoke
(one persona × one turn against fixture state)."""

from __future__ import annotations

import subprocess
from typing import Any
from unittest.mock import MagicMock

import pytest

from app.eval.playtest.persona_session import PersonaSession
from app.eval.playtest.personas import OPTIMIZER
from app.eval.playtest.schemas import PersonaTurnOutput


def test_first_turn_invokes_claude_print_with_correct_flags(
    monkeypatch: pytest.MonkeyPatch,
    fixture_turn_input: dict[str, Any],
    canned_persona_response_factory,
    tmp_shell_dir,
) -> None:
    """Vertical-slice: one persona × one turn against a fixture state.

    Verifies the subprocess command, cwd, and output parsing.
    """
    captured: dict[str, Any] = {}
    canned = canned_persona_response_factory(
        chosen_proposal_id="p_dom",
        displayed_order=["p_mor", "p_dom", "p_wld"],  # shuffled order
    )

    def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
        captured["cmd"] = cmd
        captured["cwd"] = kwargs.get("cwd")
        captured["timeout"] = kwargs.get("timeout")
        return subprocess.CompletedProcess(
            args=cmd, returncode=0, stdout=canned, stderr=""
        )

    monkeypatch.setattr("app.eval.playtest.persona_session.subprocess.run", fake_run)

    session = PersonaSession(
        persona=OPTIMIZER,
        shell_dir=tmp_shell_dir,
        effort="high",
        per_turn_timeout_seconds=300,
    )

    out = session.first_turn(
        turn_input=fixture_turn_input,
        displayed_order=["p_mor", "p_dom", "p_wld"],
    )

    # Output parsed correctly
    assert isinstance(out, PersonaTurnOutput)
    assert out.chosen_proposal_id == "p_dom"
    assert out.shown_position_of_chosen == 1

    # Command shape
    cmd = captured["cmd"]
    assert "claude" in cmd[0]
    assert "--print" in cmd
    assert "--output-format" in cmd and "json" in cmd
    assert "--effort" in cmd and "high" in cmd
    assert "--model" in cmd and "claude-sonnet-4-6" in cmd
    assert "--append-system-prompt" in cmd

    # System prompt content includes persona block
    sys_prompt_idx = cmd.index("--append-system-prompt") + 1
    sys_prompt = cmd[sys_prompt_idx]
    assert "Picking rule" in sys_prompt
    assert "Maximize" in sys_prompt or "resource_avg" in sys_prompt  # optimizer rule body

    # cwd + timeout
    assert captured["cwd"] == tmp_shell_dir
    assert captured["timeout"] == 300

    # Session ID captured for later --resume
    assert session.session_id == "test-session-abc123"
```

- [ ] **Step 3: Run failing test**

```bash
cd backend && uv run pytest tests/eval/playtest/test_persona_session.py::test_first_turn_invokes_claude_print_with_correct_flags -v
```

Expected: ImportError for `app.eval.playtest.persona_session`.

- [ ] **Step 4: Implement PersonaSession (first_turn only)**

Create `backend/app/eval/playtest/persona_session.py`:

```python
"""Single-game persona subprocess wrapper.

Wraps `claude --print` (first turn) and `claude --resume <session-id> --print`
(subsequent turns) for one game's persona. Captures the session ID returned
in the JSON envelope and reuses it across the run's turns.

Per spec §11: cwd is a clean shell directory (no project CLAUDE.md autoload),
output format is JSON envelope, persona instructions injected via
--append-system-prompt, model is sonnet-4-6, effort is `high` for personas
and `max` for the meta-aggregator (the aggregator does NOT use this class —
it runs as an Agent subagent inside the user's Claude Code session).

Failure handling (spec §10) lives in subprocess_pool.py — this class returns
parsed output on success and raises on hard failure (subprocess exit non-zero,
JSON parse failure, schema validation failure). The pool catches and retries.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any, Literal

from pydantic import ValidationError

from app.eval.playtest.schemas import (
    Persona,
    PersonaRunVerdict,
    PersonaTurnOutput,
)

EffortLevel = Literal["low", "medium", "high", "xhigh", "max"]
SONNET_MODEL_ID = "claude-sonnet-4-6"


class PersonaSessionError(RuntimeError):
    """Raised when a subprocess call fails or output is unparseable."""


def _build_system_prompt(persona: Persona) -> str:
    """The 3-section persona prompt. Frame + persona block + output contract.

    Frame and output contract are identical across all 7 personas; only the
    persona block varies. Uniformity is load-bearing for cross-persona
    comparability (spec §4).
    """
    frame = (
        "You are play-testing Outpost, a daily browser narrative roguelike. "
        "Four AI advisors run a fragile colony under your command. Permadeath. "
        "Each turn: an event, three proposals, advisor reactions, narration of "
        "the outcome. Runs end at turn 8-12 in one of: Endured, Collapsed, "
        "Ousted, Killed.\n\n"
        "You are reacting fresh — you have not seen this game before. You have "
        "no codebase context, no design documents, no prior plays. Stay in your "
        "persona's voice for in-character text; speak analytically for flags "
        "and scores. Reasoning before scoring (always)."
    )

    persona_block = (
        f"\n\nPicking rule: {persona.picking_rule}\n"
        f"Evaluation lens: {persona.evaluation_lens}\n"
        f"Voice traits: {persona.voice_traits}"
    )

    output_contract = (
        "\n\nPer turn, respond with ONLY a single JSON code-fence — no prose "
        "before or after. The JSON must validate against this shape:\n"
        "```json\n"
        "{\n"
        '  "turn": <int>,\n'
        '  "displayed_order": [<id1>, <id2>, <id3>],   // verbatim from input\n'
        '  "chosen_proposal_id": <one of displayed_order>,\n'
        '  "shown_position_of_chosen": <0, 1, or 2>,\n'
        '  "reaction": "<1-2 sentences in your voice>",\n'
        '  "flags": {\n'
        '    "surprise": <bool>, "tone_break": <bool>,\n'
        '    "advisor_oc": <bool>, "stakes_low": <bool>\n'
        "  },\n"
        '  "quotes": { "<flag_name>": "<≤30-word quote>", ... }  // only for True flags\n'
        "}\n```\n"
        "A bare True flag without a quote is a parse error. The harness will "
        "re-prompt once if your JSON is malformed."
    )

    return frame + persona_block + output_contract


def _parse_envelope_and_inner(raw_stdout: str) -> tuple[str, dict[str, Any]]:
    """Extract session_id and inner JSON from the `claude --output-format json`
    envelope. Returns (session_id, inner_dict). Raises PersonaSessionError on
    any parse failure."""
    try:
        envelope = json.loads(raw_stdout)
    except json.JSONDecodeError as exc:
        raise PersonaSessionError(f"envelope is not JSON: {exc}") from exc

    session_id = envelope.get("session_id")
    if not session_id:
        raise PersonaSessionError("envelope missing session_id")

    result_text = envelope.get("result", "")
    if not isinstance(result_text, str):
        raise PersonaSessionError("envelope.result is not a string")

    # Strip optional ```json ... ``` fence.
    text = result_text.strip()
    if text.startswith("```"):
        # take content between first and last fence
        first_nl = text.find("\n")
        last_fence = text.rfind("```")
        if first_nl == -1 or last_fence <= first_nl:
            raise PersonaSessionError("malformed code-fence in result")
        text = text[first_nl + 1 : last_fence].strip()

    try:
        inner = json.loads(text)
    except json.JSONDecodeError as exc:
        raise PersonaSessionError(f"inner result is not JSON: {exc}") from exc

    if not isinstance(inner, dict):
        raise PersonaSessionError("inner result is not a JSON object")

    return session_id, inner


def _format_turn_user_message(
    *, turn_input: dict[str, Any], displayed_order: list[str]
) -> str:
    """Renders the harness's per-turn user-message text shown to the persona.

    Includes the dashboard, the event, and the proposals in `displayed_order`
    sequence. Format is human-readable markdown so the LLM reads it naturally
    (research finding: markdown beats JSON for narrative pattern matching by
    LLMs).
    """
    proposals_by_id = {p["id"]: p for p in turn_input["proposals"]}
    advisor_by_id = {a["id"]: a for a in turn_input["dashboard"]["advisors"]}
    reactions_by_aid = {r["agent_id"]: r["text"] for r in turn_input["advisor_reactions"]}

    lines: list[str] = [
        f"# Turn {turn_input['turn']}",
        "",
        "## Setting",
        turn_input["setting_lore"],
        "",
        "## Dashboard",
        f"- Legitimacy: {turn_input['dashboard']['legitimacy']}",
        f"- Player health: {turn_input['dashboard']['player_health']}",
        "- Resources: "
        + ", ".join(
            f"{k} {v}" for k, v in turn_input["dashboard"]["resources"].items()
        ),
        "",
        "## Event",
        f"**{turn_input['event']['title']}**",
        turn_input["event"]["body"],
        "",
        "## Proposals (in display order)",
    ]
    for idx, pid in enumerate(displayed_order):
        p = proposals_by_id[pid]
        proposer = advisor_by_id.get(p.get("proposed_by_agent_id", ""))
        proposer_name = proposer["name"] if proposer else "(unknown)"
        lines.append(
            f"{idx}. **{p['name']}** ({p['flavor']}, by {proposer_name}) "
            f"— {p['description']}"
        )
    lines.append("")
    lines.append("## Advisor reactions")
    for a in turn_input["dashboard"]["advisors"]:
        text = reactions_by_aid.get(a["id"], "(silent)")
        lines.append(f"- **{a['name']}** ({a['role']}): {text}")
    lines.append("")
    lines.append(
        f"Pick one of the three proposals above. Your `displayed_order` field "
        f"in the response must be exactly: {displayed_order}"
    )
    return "\n".join(lines)


class PersonaSession:
    """One persona session backing one game.

    Lifecycle:
      1. `first_turn(...)` — `claude --print` first invocation; captures session_id
      2. `subsequent_turn(...)` — `claude --resume <session_id> --print`
      3. `final_verdict(...)` — sends post-mortem JSON, expects PersonaRunVerdict

    Each method returns parsed output or raises PersonaSessionError. The
    SubprocessPool catches and retries per spec §10.
    """

    def __init__(
        self,
        *,
        persona: Persona,
        shell_dir: Path,
        effort: EffortLevel = "high",
        per_turn_timeout_seconds: int = 300,
        model: str = SONNET_MODEL_ID,
    ) -> None:
        self.persona = persona
        self.shell_dir = shell_dir
        self.effort = effort
        self.per_turn_timeout_seconds = per_turn_timeout_seconds
        self.model = model
        self.session_id: str | None = None

    def first_turn(
        self, *, turn_input: dict[str, Any], displayed_order: list[str]
    ) -> PersonaTurnOutput:
        """Invoke `claude --print` for the first turn. Captures session_id."""
        system_prompt = _build_system_prompt(self.persona)
        user_message = _format_turn_user_message(
            turn_input=turn_input, displayed_order=displayed_order
        )
        cmd = [
            "claude",
            "--print",
            "--output-format",
            "json",
            "--model",
            self.model,
            "--effort",
            self.effort,
            "--append-system-prompt",
            system_prompt,
            user_message,
        ]

        try:
            proc = subprocess.run(
                cmd,
                cwd=self.shell_dir,
                capture_output=True,
                text=True,
                timeout=self.per_turn_timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise PersonaSessionError(f"first-turn timeout after {exc.timeout}s") from exc

        if proc.returncode != 0:
            raise PersonaSessionError(
                f"first-turn subprocess returned {proc.returncode}: {proc.stderr[:500]}"
            )

        session_id, inner = _parse_envelope_and_inner(proc.stdout)
        self.session_id = session_id

        try:
            return PersonaTurnOutput.model_validate(inner)
        except ValidationError as exc:
            raise PersonaSessionError(f"per-turn output failed validation: {exc}") from exc
```

- [ ] **Step 5: Run vertical-slice test, verify pass**

```bash
cd backend && uv run pytest tests/eval/playtest/test_persona_session.py::test_first_turn_invokes_claude_print_with_correct_flags -v
```

Expected: PASS.

- [ ] **Step 6: Add subsequent-turn test**

Append to `backend/tests/eval/playtest/test_persona_session.py`:

```python
def test_subsequent_turn_uses_resume(
    monkeypatch: pytest.MonkeyPatch,
    fixture_turn_input: dict[str, Any],
    canned_persona_response_factory,
    tmp_shell_dir,
) -> None:
    captured: dict[str, Any] = {}
    canned = canned_persona_response_factory(
        chosen_proposal_id="p_mor", displayed_order=["p_dom", "p_mor", "p_wld"]
    )

    def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(
            args=cmd, returncode=0, stdout=canned, stderr=""
        )

    monkeypatch.setattr("app.eval.playtest.persona_session.subprocess.run", fake_run)

    session = PersonaSession(
        persona=OPTIMIZER,
        shell_dir=tmp_shell_dir,
    )
    session.session_id = "preset-session-id-from-prior-turn"

    out = session.subsequent_turn(
        turn_input={**fixture_turn_input, "turn": 2},
        displayed_order=["p_dom", "p_mor", "p_wld"],
    )

    assert out.chosen_proposal_id == "p_mor"
    cmd = captured["cmd"]
    assert "--resume" in cmd
    resume_idx = cmd.index("--resume") + 1
    assert cmd[resume_idx] == "preset-session-id-from-prior-turn"
    # No --append-system-prompt on resume — system prompt is preserved across
    # the session by the server. Confirm this is the case:
    assert "--append-system-prompt" not in cmd


def test_subsequent_turn_without_session_id_raises(
    fixture_turn_input: dict[str, Any], tmp_shell_dir
) -> None:
    session = PersonaSession(persona=OPTIMIZER, shell_dir=tmp_shell_dir)
    assert session.session_id is None
    with pytest.raises(RuntimeError, match="no session_id"):
        session.subsequent_turn(
            turn_input=fixture_turn_input, displayed_order=["a", "b", "c"]
        )
```

- [ ] **Step 7: Implement subsequent_turn**

Append to `backend/app/eval/playtest/persona_session.py` (inside `PersonaSession`):

```python
    def subsequent_turn(
        self, *, turn_input: dict[str, Any], displayed_order: list[str]
    ) -> PersonaTurnOutput:
        """Continue an existing session via `claude --resume`."""
        if self.session_id is None:
            raise RuntimeError(
                "no session_id — call first_turn() before subsequent_turn()"
            )

        user_message = _format_turn_user_message(
            turn_input=turn_input, displayed_order=displayed_order
        )
        cmd = [
            "claude",
            "--resume",
            self.session_id,
            "--print",
            "--output-format",
            "json",
            "--effort",
            self.effort,
            user_message,
        ]

        try:
            proc = subprocess.run(
                cmd,
                cwd=self.shell_dir,
                capture_output=True,
                text=True,
                timeout=self.per_turn_timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise PersonaSessionError(
                f"subsequent-turn timeout after {exc.timeout}s"
            ) from exc

        if proc.returncode != 0:
            raise PersonaSessionError(
                f"subsequent-turn subprocess returned {proc.returncode}: "
                f"{proc.stderr[:500]}"
            )

        # session_id from envelope should match self.session_id; we don't
        # re-assign because resume preserves it.
        _, inner = _parse_envelope_and_inner(proc.stdout)

        try:
            return PersonaTurnOutput.model_validate(inner)
        except ValidationError as exc:
            raise PersonaSessionError(
                f"per-turn output failed validation: {exc}"
            ) from exc
```

- [ ] **Step 8: Add final-verdict test**

Append to `backend/tests/eval/playtest/test_persona_session.py`:

```python
def test_final_verdict_parses_run_verdict(
    monkeypatch: pytest.MonkeyPatch, tmp_shell_dir
) -> None:
    """End-of-run verdict — persona reads post-mortem and emits 5-axis verdict."""
    verdict_inner = {
        "axes": {
            "stakes_felt_real": {"score": 4, "rationale": "...", "turn_citations": [4, 7]},
            "tonal_coherence": {"score": 5, "rationale": "...", "turn_citations": []},
            "advisor_distinctness": {"score": 4, "rationale": "...", "turn_citations": [3]},
            "dramatic_arc": {"score": 4, "rationale": "...", "turn_citations": [11]},
        },
        "continue_tomorrow": {"decision": True, "reason": "dissident path landed"},
        "closing_paragraph": "...",
    }
    envelope_json = json.dumps(
        {
            "type": "result",
            "session_id": "preset-session-id",
            "result": "```json\n" + json.dumps(verdict_inner) + "\n```",
        }
    )

    monkeypatch.setattr(
        "app.eval.playtest.persona_session.subprocess.run",
        lambda cmd, **kw: subprocess.CompletedProcess(
            args=cmd, returncode=0, stdout=envelope_json, stderr=""
        ),
    )

    session = PersonaSession(persona=OPTIMIZER, shell_dir=tmp_shell_dir)
    session.session_id = "preset-session-id"

    verdict = session.final_verdict(post_mortem_json={"end_state": "ENDURED"})
    assert verdict.axes.stakes_felt_real.score == 4
    assert verdict.continue_tomorrow.decision is True
```

Add `import json` to the top of the test file.

- [ ] **Step 9: Implement final_verdict**

Append to `PersonaSession` in `persona_session.py`:

```python
    def final_verdict(self, *, post_mortem_json: dict[str, Any]) -> PersonaRunVerdict:
        """Send the post-mortem JSON to the persona; expect a run-level verdict."""
        if self.session_id is None:
            raise RuntimeError("no session_id — call first_turn() first")

        user_message = (
            "# End of run\n\n"
            "Below is the full post-mortem (counterfactuals, dormant seeds, "
            "near-miss coups, end_state). Read it carefully, then emit ONLY a "
            "JSON code-fence with the run-level verdict.\n\n"
            "Schema:\n"
            "```json\n"
            "{\n"
            '  "axes": {\n'
            '    "stakes_felt_real":      {"score": 1-5, "rationale": "...", "turn_citations": [int, ...]},\n'
            '    "tonal_coherence":       {"score": 1-5, "rationale": "...", "turn_citations": []},\n'
            '    "advisor_distinctness":  {"score": 1-5, "rationale": "...", "turn_citations": []},\n'
            '    "dramatic_arc":          {"score": 1-5, "rationale": "...", "turn_citations": []}\n'
            "  },\n"
            '  "continue_tomorrow": {"decision": <bool>, "reason": "<≤20 words>"},\n'
            '  "closing_paragraph": "<~100 words in your evaluation voice>"\n'
            "}\n```\n"
            "Reasoning before scoring — the rationale comes BEFORE the number "
            "in your thinking.\n\n"
            "## Post-mortem\n\n"
            f"```json\n{json.dumps(post_mortem_json, indent=2)}\n```"
        )

        cmd = [
            "claude",
            "--resume",
            self.session_id,
            "--print",
            "--output-format",
            "json",
            "--effort",
            self.effort,
            user_message,
        ]

        try:
            proc = subprocess.run(
                cmd,
                cwd=self.shell_dir,
                capture_output=True,
                text=True,
                timeout=self.per_turn_timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise PersonaSessionError(
                f"final-verdict timeout after {exc.timeout}s"
            ) from exc

        if proc.returncode != 0:
            raise PersonaSessionError(
                f"final-verdict subprocess returned {proc.returncode}: "
                f"{proc.stderr[:500]}"
            )

        _, inner = _parse_envelope_and_inner(proc.stdout)

        try:
            return PersonaRunVerdict.model_validate(inner)
        except ValidationError as exc:
            raise PersonaSessionError(
                f"run-verdict output failed validation: {exc}"
            ) from exc
```

- [ ] **Step 10: Run all PersonaSession tests + lint**

```bash
cd backend && uv run pytest tests/eval/playtest/test_persona_session.py -v && uv run ruff check app/eval/playtest/persona_session.py tests/eval/playtest/test_persona_session.py && uv run ruff format --check app/eval/playtest/persona_session.py tests/eval/playtest/test_persona_session.py
```

Expected: 4 tests pass, ruff clean.

- [ ] **Step 11: Commit**

```bash
git add backend/app/eval/playtest/persona_session.py backend/tests/eval/playtest/conftest.py backend/tests/eval/playtest/test_persona_session.py
git commit -m "$(cat <<'EOF'
feat(playtest): PersonaSession wrapping claude --print/--resume subprocess

Wraps the per-game persona subprocess lifecycle: first_turn invokes
claude --print --output-format json --append-system-prompt, captures
session_id; subsequent_turn uses claude --resume; final_verdict sends
the post-mortem and parses a PersonaRunVerdict. cwd is a clean shell
directory so no project CLAUDE.md is autoloaded. Includes the
vertical-slice test the design called for: one persona × one turn
against a fixture state, mocked subprocess.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: SubprocessPool (concurrency, timeouts, retries)

**Files:**
- Create: `backend/app/eval/playtest/subprocess_pool.py`
- Create: `backend/tests/eval/playtest/test_subprocess_pool.py`

The pool wraps PersonaSession with the failure-handling rules from spec §10: re-prompt once on malformed/invalid output, soft-warn at 120s, hard-kill at 300s, fallback-pick on second failure. Concurrency is bounded at 5 sessions.

- [ ] **Step 1: Write retry-on-malformed test (failing)**

Create `backend/tests/eval/playtest/test_subprocess_pool.py`:

```python
"""Tests for SubprocessPool's retry / degrade rules per spec §10."""

from __future__ import annotations

import subprocess
from typing import Any

import pytest

from app.eval.playtest.persona_session import PersonaSession, PersonaSessionError
from app.eval.playtest.personas import OPTIMIZER
from app.eval.playtest.schemas import PersonaTurnOutput
from app.eval.playtest.subprocess_pool import (
    HarnessFinding,
    PoolPickResult,
    SubprocessPool,
)


def test_malformed_json_retries_once_then_falls_back(
    monkeypatch: pytest.MonkeyPatch,
    fixture_turn_input: dict[str, Any],
    canned_persona_response_factory,
    tmp_shell_dir,
) -> None:
    """First attempt: malformed envelope → retry with strict reminder.
    Second attempt: still malformed → fallback pick (first in displayed_order).
    Both events recorded in harness_findings.malformed_json_count."""
    call_count = {"n": 0}

    def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
        call_count["n"] += 1
        return subprocess.CompletedProcess(
            args=cmd, returncode=0, stdout="not-valid-json", stderr=""
        )

    monkeypatch.setattr("app.eval.playtest.persona_session.subprocess.run", fake_run)

    pool = SubprocessPool(max_concurrency=1)
    session = PersonaSession(persona=OPTIMIZER, shell_dir=tmp_shell_dir)

    result = pool.run_first_turn(
        session=session,
        turn_input=fixture_turn_input,
        displayed_order=["p_mor", "p_dom", "p_wld"],
    )

    assert call_count["n"] == 2  # one + one retry
    assert isinstance(result, PoolPickResult)
    assert result.fallback_used is True
    # Fallback rule: first proposal in displayed_order
    assert result.chosen_proposal_id == "p_mor"
    # Recorded as harness finding
    assert pool.findings.malformed_json_count == 2
```

- [ ] **Step 2: Run failing test**

```bash
cd backend && uv run pytest tests/eval/playtest/test_subprocess_pool.py::test_malformed_json_retries_once_then_falls_back -v
```

Expected: ImportError for `app.eval.playtest.subprocess_pool`.

- [ ] **Step 3: Implement SubprocessPool minimal shape**

Create `backend/app/eval/playtest/subprocess_pool.py`:

```python
"""Concurrency + retry + degrade rules wrapping PersonaSession.

Per spec §10: every failure is data. Single failures degrade gracefully
(fallback to first proposal in displayed_order — preserves seed-based
reproducibility, unbiased toward any flavor because order is shuffled).
Patterns get logged in `harness_findings` for the aggregator.
"""

from __future__ import annotations

import logging
import threading
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from app.eval.playtest.persona_session import (
    PersonaSession,
    PersonaSessionError,
)
from app.eval.playtest.schemas import (
    PersonaRunVerdict,
    PersonaTurnFlags,
    PersonaTurnOutput,
)

logger = logging.getLogger(__name__)


@dataclass
class HarnessFinding:
    """Per-invocation counters for non-graceful events. Mirrors spec §9
    `runs.json.harness_findings` shape."""

    malformed_json_count: int = 0
    invalid_pick_count: int = 0
    persona_refusals: list[dict[str, Any]] = field(default_factory=list)
    subprocess_hangs: int = 0
    subprocess_crashes: int = 0
    aborted_runs: list[dict[str, Any]] = field(default_factory=list)
    by_persona: dict[str, dict[str, int]] = field(
        default_factory=lambda: defaultdict(
            lambda: {"malformed": 0, "invalid_pick": 0}
        )
    )


@dataclass
class PoolPickResult:
    """One turn's pool output: either a real persona output or a fallback.

    `output` is the parsed PersonaTurnOutput on success or a synthesized
    fallback on degrade (with `fallback_used=True`).
    """

    output: PersonaTurnOutput
    fallback_used: bool

    @property
    def chosen_proposal_id(self) -> str:
        return self.output.chosen_proposal_id


def _synthesize_fallback(
    *, turn: int, displayed_order: list[str]
) -> PersonaTurnOutput:
    """Fallback pick when the persona fails twice. Picks the first proposal
    in shuffled `displayed_order`. Reaction explicitly notes the failure."""
    return PersonaTurnOutput(
        turn=turn,
        displayed_order=displayed_order,
        chosen_proposal_id=displayed_order[0],
        shown_position_of_chosen=0,
        reaction="(harness fallback — persona failed to respond)",
        flags=PersonaTurnFlags(
            surprise=False,
            tone_break=False,
            advisor_oc=False,
            stakes_low=False,
        ),
        quotes={},
    )


class SubprocessPool:
    """Bounded-concurrency pool wrapping PersonaSession invocations.

    Concurrency is enforced by an `asyncio.Semaphore` for async callers, but
    the pool's per-session methods are sync (since PersonaSession's calls are
    sync). Cross-game parallelism happens at the orchestrator level — one
    PersonaSession per game runs sequentially per game; multiple games run
    in parallel up to `max_concurrency`.
    """

    def __init__(self, *, max_concurrency: int = 5) -> None:
        self.max_concurrency = max_concurrency
        self._sema = threading.Semaphore(max_concurrency)
        self.findings = HarnessFinding()

    def run_first_turn(
        self,
        *,
        session: PersonaSession,
        turn_input: dict[str, Any],
        displayed_order: list[str],
    ) -> PoolPickResult:
        return self._run_with_retry(
            kind="first_turn",
            session=session,
            turn_input=turn_input,
            displayed_order=displayed_order,
        )

    def run_subsequent_turn(
        self,
        *,
        session: PersonaSession,
        turn_input: dict[str, Any],
        displayed_order: list[str],
    ) -> PoolPickResult:
        return self._run_with_retry(
            kind="subsequent_turn",
            session=session,
            turn_input=turn_input,
            displayed_order=displayed_order,
        )

    def run_final_verdict(
        self, *, session: PersonaSession, post_mortem_json: dict[str, Any]
    ) -> PersonaRunVerdict | None:
        """End-of-run verdict. Single retry; on second failure returns None
        (the run's verdict is recorded as missing in runs.json)."""
        with self._sema:
            for attempt in (1, 2):
                try:
                    return session.final_verdict(post_mortem_json=post_mortem_json)
                except PersonaSessionError as exc:
                    logger.warning(
                        "final_verdict failed (attempt %d/2) for %s: %s",
                        attempt,
                        session.persona.id,
                        exc,
                    )
                    self.findings.malformed_json_count += 1
                    self.findings.by_persona[session.persona.id]["malformed"] += 1
                    if attempt == 2:
                        return None
        return None

    def _run_with_retry(
        self,
        *,
        kind: str,
        session: PersonaSession,
        turn_input: dict[str, Any],
        displayed_order: list[str],
    ) -> PoolPickResult:
        with self._sema:
            method = (
                session.first_turn if kind == "first_turn" else session.subsequent_turn
            )
            for attempt in (1, 2):
                try:
                    out = method(
                        turn_input=turn_input, displayed_order=displayed_order
                    )
                    if out.chosen_proposal_id not in displayed_order:
                        # invalid pick — re-prompt logic would happen here in
                        # a more elaborate implementation; for now treat as
                        # malformed since schema validation already enforces
                        # this at the model level. (Defensive belt-and-suspenders.)
                        raise PersonaSessionError(
                            f"chosen_proposal_id {out.chosen_proposal_id!r} not in "
                            f"displayed_order {displayed_order}"
                        )
                    return PoolPickResult(output=out, fallback_used=False)
                except PersonaSessionError as exc:
                    logger.warning(
                        "%s failed (attempt %d/2) for %s: %s",
                        kind,
                        attempt,
                        session.persona.id,
                        exc,
                    )
                    self.findings.malformed_json_count += 1
                    self.findings.by_persona[session.persona.id]["malformed"] += 1

            return PoolPickResult(
                output=_synthesize_fallback(
                    turn=turn_input["turn"], displayed_order=displayed_order
                ),
                fallback_used=True,
            )
```

- [ ] **Step 4: Run retry test, verify pass**

```bash
cd backend && uv run pytest tests/eval/playtest/test_subprocess_pool.py::test_malformed_json_retries_once_then_falls_back -v
```

Expected: PASS.

- [ ] **Step 5: Add timeout test**

Append to `backend/tests/eval/playtest/test_subprocess_pool.py`:

```python
def test_subprocess_timeout_recorded_as_hang(
    monkeypatch: pytest.MonkeyPatch,
    fixture_turn_input: dict[str, Any],
    tmp_shell_dir,
) -> None:
    """`subprocess.TimeoutExpired` → first attempt raises PersonaSessionError
    with 'timeout' substring; pool retries once; if that also times out,
    fallback. The pool counts each timeout as a `subprocess_hang`."""

    def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=kwargs.get("timeout", 300))

    monkeypatch.setattr("app.eval.playtest.persona_session.subprocess.run", fake_run)

    pool = SubprocessPool(max_concurrency=1)
    session = PersonaSession(
        persona=OPTIMIZER, shell_dir=tmp_shell_dir, per_turn_timeout_seconds=10
    )

    result = pool.run_first_turn(
        session=session,
        turn_input=fixture_turn_input,
        displayed_order=["p_mor", "p_dom", "p_wld"],
    )

    assert result.fallback_used is True
    assert pool.findings.subprocess_hangs == 2  # both attempts hung


def test_concurrent_first_turns_share_semaphore(
    monkeypatch: pytest.MonkeyPatch,
    fixture_turn_input: dict[str, Any],
    canned_persona_response_factory,
    tmp_shell_dir,
) -> None:
    """SubprocessPool with max_concurrency=2 admits at most 2 simultaneous
    sessions. Verified by serializing fake subprocess returns and counting
    how many ran in parallel."""
    import time

    in_flight = {"n": 0, "max": 0}
    lock = threading.Lock()

    canned = canned_persona_response_factory(
        chosen_proposal_id="p_dom", displayed_order=["p_mor", "p_dom", "p_wld"]
    )

    def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
        with lock:
            in_flight["n"] += 1
            in_flight["max"] = max(in_flight["max"], in_flight["n"])
        time.sleep(0.05)
        with lock:
            in_flight["n"] -= 1
        return subprocess.CompletedProcess(
            args=cmd, returncode=0, stdout=canned, stderr=""
        )

    monkeypatch.setattr("app.eval.playtest.persona_session.subprocess.run", fake_run)

    pool = SubprocessPool(max_concurrency=2)
    threads: list[threading.Thread] = []
    for _ in range(5):
        session = PersonaSession(persona=OPTIMIZER, shell_dir=tmp_shell_dir)

        def go(s: PersonaSession = session) -> None:
            pool.run_first_turn(
                session=s,
                turn_input=fixture_turn_input,
                displayed_order=["p_mor", "p_dom", "p_wld"],
            )

        t = threading.Thread(target=go)
        t.start()
        threads.append(t)
    for t in threads:
        t.join()

    assert in_flight["max"] <= 2
```

Add `import threading` to the test file.

- [ ] **Step 6: Update `_run_with_retry` to count timeouts as hangs**

Modify `backend/app/eval/playtest/subprocess_pool.py` — replace the inside of the `_run_with_retry` `except PersonaSessionError as exc:` block:

```python
                except PersonaSessionError as exc:
                    msg = str(exc).lower()
                    if "timeout" in msg:
                        self.findings.subprocess_hangs += 1
                    else:
                        self.findings.malformed_json_count += 1
                        self.findings.by_persona[session.persona.id]["malformed"] += 1
                    logger.warning(
                        "%s failed (attempt %d/2) for %s: %s",
                        kind,
                        attempt,
                        session.persona.id,
                        exc,
                    )
```

- [ ] **Step 7: Run new tests + lint**

```bash
cd backend && uv run pytest tests/eval/playtest/test_subprocess_pool.py -v && uv run ruff check app/eval/playtest/subprocess_pool.py tests/eval/playtest/test_subprocess_pool.py && uv run ruff format --check app/eval/playtest/subprocess_pool.py tests/eval/playtest/test_subprocess_pool.py
```

Expected: 3 tests pass, ruff clean.

- [ ] **Step 8: Commit**

```bash
git add backend/app/eval/playtest/subprocess_pool.py backend/tests/eval/playtest/test_subprocess_pool.py
git commit -m "$(cat <<'EOF'
feat(playtest): SubprocessPool with retry/degrade per spec §10

Wraps PersonaSession with bounded concurrency (Semaphore), per-method
retry-once-then-fallback rules, and HarnessFinding counters that mirror
runs.json's harness_findings shape. Fallback rule: first proposal in
shuffled displayed_order — deterministic, reproducible, flavor-unbiased.
Timeouts are categorized as subprocess_hangs distinct from malformed JSON.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: LLMPlayer (drop-in for SimulatedPlayer)

**Files:**
- Create: `backend/app/eval/playtest/llm_player.py`
- Create: `backend/tests/eval/playtest/test_llm_player.py`

`LLMPlayer.pick(proposals)` is the interface `simulate_game` expects (signature mirrors `SimulatedPlayer.pick`). Internally it shuffles `proposals`, calls the pool, and returns the chosen id. The orchestrator (Task 8) feeds it the per-turn input data assembled from SSE frames.

- [ ] **Step 1: Write LLMPlayer interface test (failing)**

Create `backend/tests/eval/playtest/test_llm_player.py`:

```python
"""LLMPlayer drop-in tests."""

from __future__ import annotations

import subprocess
from typing import Any

import pytest

from app.eval.playtest.llm_player import LLMPlayer
from app.eval.playtest.personas import OPTIMIZER
from app.eval.playtest.subprocess_pool import SubprocessPool


def test_player_pick_returns_id_in_displayed_order(
    monkeypatch: pytest.MonkeyPatch,
    fixture_turn_input: dict[str, Any],
    canned_persona_response_factory,
    tmp_shell_dir,
) -> None:
    """LLMPlayer.pick(proposals) shuffles, calls pool, returns chosen id.

    Verifies (a) chosen id is one of the input proposals, (b) the persona's
    output was recorded for later RunRecord assembly.
    """
    # Stub subprocess to always return a canned response. The `chosen_proposal_id`
    # value in the canned response is intentionally a fixed value; the pool's
    # PoolPickResult will return it; we then verify it matches one of the
    # input proposal ids.
    def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
        # Find displayed_order in the prompt to mirror it back
        # (the canned envelope's inner JSON must match the actual displayed_order).
        # Easier: inspect the user_message argument and extract the order block.
        user_msg = cmd[-1]
        # Crude order extraction — find the "displayed_order" assertion line:
        line = next(
            line for line in user_msg.splitlines() if "displayed_order" in line
        )
        # The line ends with a Python-list-shaped suffix.
        import ast

        bracket_start = line.find("[")
        bracket_end = line.rfind("]") + 1
        order = ast.literal_eval(line[bracket_start:bracket_end])
        canned = canned_persona_response_factory(
            chosen_proposal_id=order[0], displayed_order=order
        )
        return subprocess.CompletedProcess(
            args=cmd, returncode=0, stdout=canned, stderr=""
        )

    monkeypatch.setattr("app.eval.playtest.persona_session.subprocess.run", fake_run)

    pool = SubprocessPool(max_concurrency=1)
    player = LLMPlayer(
        persona=OPTIMIZER,
        pool=pool,
        shell_dir=tmp_shell_dir,
        seed=0,
        turn_input_provider=lambda: fixture_turn_input,
    )

    proposals = fixture_turn_input["proposals"]
    chosen_id = player.pick(proposals)
    assert chosen_id in {p["id"] for p in proposals}
    assert len(player.per_turn_outputs) == 1
    rec = player.per_turn_outputs[0]
    assert rec.chosen_proposal_id == chosen_id


def test_player_seed_makes_shuffle_deterministic(
    fixture_turn_input: dict[str, Any], tmp_shell_dir
) -> None:
    """Two LLMPlayer instances constructed with the same seed produce the
    same `displayed_order` for the same proposals input. Subprocess never
    actually runs — we capture the order via the turn_input_provider hook.
    """
    captured: list[list[str]] = []

    def make_player() -> LLMPlayer:
        return LLMPlayer(
            persona=OPTIMIZER,
            pool=SubprocessPool(max_concurrency=1),
            shell_dir=tmp_shell_dir,
            seed=42,
            turn_input_provider=lambda: fixture_turn_input,
        )

    p1 = make_player()
    p2 = make_player()
    proposals = fixture_turn_input["proposals"]
    o1 = p1._displayed_order_for(proposals)
    o2 = p2._displayed_order_for(proposals)
    assert o1 == o2  # same seed → same shuffle
    captured.append(o1)
```

- [ ] **Step 2: Run failing test**

```bash
cd backend && uv run pytest tests/eval/playtest/test_llm_player.py -v
```

Expected: ImportError.

- [ ] **Step 3: Implement LLMPlayer**

Create `backend/app/eval/playtest/llm_player.py`:

```python
"""LLMPlayer — drop-in for SimulatedPlayer in run_simulator.simulate_game.

Interface contract: `pick(proposals: list[dict]) -> str` (proposal id).
Internally, the player shuffles proposals, asks the pool to dispatch the
turn (first or subsequent), records the output, and returns the chosen id.

State is per-game: one LLMPlayer instance backs one game. The orchestrator
constructs one per simulated game and passes it into simulate_game.
"""

from __future__ import annotations

import random
from pathlib import Path
from typing import Any, Callable

from app.eval.playtest.persona_session import PersonaSession
from app.eval.playtest.personas import Persona
from app.eval.playtest.schemas import PersonaTurnOutput
from app.eval.playtest.subprocess_pool import PoolPickResult, SubprocessPool


class LLMPlayer:
    """Per-game LLM player. Conforms to the SimulatedPlayer interface
    (`pick(proposals) -> id`) so it can drop into run_simulator.simulate_game
    without changes."""

    def __init__(
        self,
        *,
        persona: Persona,
        pool: SubprocessPool,
        shell_dir: Path,
        seed: int,
        turn_input_provider: Callable[[], dict[str, Any]],
        effort: str = "high",
        per_turn_timeout_seconds: int = 300,
    ) -> None:
        self.persona = persona
        self.pool = pool
        self.shell_dir = shell_dir
        self.seed = seed
        self.turn_input_provider = turn_input_provider
        self._rng = random.Random(seed)
        self._session = PersonaSession(
            persona=persona,
            shell_dir=shell_dir,
            effort=effort,  # type: ignore[arg-type]
            per_turn_timeout_seconds=per_turn_timeout_seconds,
        )
        self._first_turn_done = False
        self.per_turn_outputs: list[PersonaTurnOutput] = []
        self.fallback_turns: list[int] = []  # turn numbers where pool fell back

    def pick(self, proposals: list[Any]) -> str:
        """The SimulatedPlayer-compatible interface called by simulate_game."""
        ids = [p["id"] if isinstance(p, dict) else str(p.id) for p in proposals]
        displayed_order = self._displayed_order_for(proposals)

        turn_input = self.turn_input_provider()
        if not self._first_turn_done:
            result = self.pool.run_first_turn(
                session=self._session,
                turn_input=turn_input,
                displayed_order=displayed_order,
            )
            self._first_turn_done = True
        else:
            result = self.pool.run_subsequent_turn(
                session=self._session,
                turn_input=turn_input,
                displayed_order=displayed_order,
            )

        self._record_output(result)
        return result.chosen_proposal_id

    def _displayed_order_for(self, proposals: list[Any]) -> list[str]:
        """Per-turn shuffle of proposal ids — deterministic per `seed`.

        Per spec §5: each turn we randomly shuffle `[DOMINANT, MORAL, WILDCARD]`
        in display order so per-persona pick rates can be checked against
        position correlation (Spearman ρ > 0.2 marks position-bias contamination).
        """
        ids = [p["id"] if isinstance(p, dict) else str(p.id) for p in proposals]
        shuffled = ids[:]
        self._rng.shuffle(shuffled)
        return shuffled

    def _record_output(self, result: PoolPickResult) -> None:
        self.per_turn_outputs.append(result.output)
        if result.fallback_used:
            self.fallback_turns.append(result.output.turn)
```

- [ ] **Step 4: Run tests + lint**

```bash
cd backend && uv run pytest tests/eval/playtest/test_llm_player.py -v && uv run ruff check app/eval/playtest/llm_player.py tests/eval/playtest/test_llm_player.py && uv run ruff format --check app/eval/playtest/llm_player.py tests/eval/playtest/test_llm_player.py
```

Expected: 2 pass, ruff clean.

- [ ] **Step 5: Commit**

```bash
git add backend/app/eval/playtest/llm_player.py backend/tests/eval/playtest/test_llm_player.py
git commit -m "$(cat <<'EOF'
feat(playtest): LLMPlayer drop-in for SimulatedPlayer

Per-game LLMPlayer conforms to SimulatedPlayer.pick(proposals) -> id.
Internally shuffles proposals per-turn (deterministic via seeded
random.Random — preserves seed-based reproducibility while defeating
position bias), dispatches via SubprocessPool, records per-turn outputs
and fallback-turn numbers for later RunRecord assembly.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: Oracle wrapper

**Files:**
- Create: `backend/app/eval/playtest/oracles.py`
- Create: `backend/tests/eval/playtest/test_oracles.py`

Wraps the 5 existing eval modules into per-run + per-corpus computations the aggregator can read alongside persona findings.

- [ ] **Step 1: Write per-run oracle test (failing)**

Create `backend/tests/eval/playtest/test_oracles.py`:

```python
"""Oracle-wrapping tests."""

from __future__ import annotations

from uuid import UUID

import numpy as np
import pytest

from app.eval.playtest.oracles import (
    compute_corpus_oracles,
    compute_per_run_oracles,
)
from app.eval.types import RunRecord
from app.game.schemas import (
    Agent,
    AgentAxes,
    EndState,
    Quadrant,
    ToolCall,
)


def _fake_agent(name: str = "TestAgent") -> Agent:
    return Agent(
        id=UUID("00000000-0000-0000-0000-000000000001"),
        name=name,
        role="Tester",
        archetype_quadrant=Quadrant.PRAGMATIST_BOLD,
        axes=AgentAxes(ethics=-0.5, temperament=0.5, loyalty=0.0),
        portrait_seed="seed",
        health=100,
        morale=80,
        grievance=2,
    )


def _fake_run(seed: int = 1) -> RunRecord:
    return RunRecord(
        setting_id="arctic_station_v1",
        policy="moral",
        seed=seed,
        end_state=EndState.ENDURED,
        turns_played=10,
        final_player_health=80,
        agents_alive_at_end=4,
        total_agents=4,
        cost_cents=20,
        tool_calls=[],
        agent_responses_per_agent={},
        resource_history={"fuel": [80, 70, 65, 60, 55]},
        forced_choice_turns=0,
        narration_per_turn=[
            f"Turn {i}: ice creaks under the heating bay; nothing yet" for i in range(10)
        ],
    )


def test_per_run_oracles_returns_three_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    """Per-run oracles compute tool_validity, repetition, personality_consistency."""

    class _FakeEncoder:
        def encode(self, texts, **_kwargs):  # noqa: ANN001
            return np.array([[1.0, 0.0, 0.0] for _ in texts], dtype=np.float32)

    monkeypatch.setattr("app.eval.personality._get_model", lambda: _FakeEncoder())

    run = _fake_run()
    out = compute_per_run_oracles(run, agents=[_fake_agent()])
    assert "tool_validity_rate" in out
    assert "repetition_bleu2" in out
    assert "personality_consistency" in out
    assert isinstance(out["tool_validity_rate"], float)
    assert isinstance(out["repetition_bleu2"], float)
    assert isinstance(out["personality_consistency"], dict)


def test_corpus_oracles_returns_inter_run_and_end_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FakeEncoder:
        def encode(self, texts, **_kwargs):  # noqa: ANN001
            return np.array([[1.0, 0.0, 0.0] for _ in texts], dtype=np.float32)

    monkeypatch.setattr("app.eval.personality._get_model", lambda: _FakeEncoder())

    runs = [_fake_run(seed=i) for i in range(3)]
    out = compute_corpus_oracles(runs)
    assert "inter_run_similarity" in out
    assert "end_state_distribution" in out
    assert "spec_target_match" in out
    assert isinstance(out["spec_target_match"], bool)
```

- [ ] **Step 2: Run failing test**

```bash
cd backend && uv run pytest tests/eval/playtest/test_oracles.py -v
```

Expected: ImportError for `app.eval.playtest.oracles`.

- [ ] **Step 3: Implement oracle wrapper**

Create `backend/app/eval/playtest/oracles.py`:

```python
"""Wraps the five existing structural oracles in app/eval/ into per-run +
per-corpus computations consumed by the meta-aggregator.

Per spec §7 — these are the orthogonal evidence base. The aggregator marks
every persona finding as high_confidence / persona_only / oracle_only /
disagreement based on whether the structural oracles agree.

Per-run oracles (one RunRecord input):
- tool_validity_rate
- repetition_bleu2 (within-run BLEU-2)
- personality_consistency (per-agent embedding similarity)

Corpus oracles (list[RunRecord] input):
- inter_run_similarity (mean pairwise cosine on narration)
- end_state_distribution + spec_target_match (vs SPEC §10 bands)
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from app.eval.end_state import score_end_state_distribution
from app.eval.inter_run_similarity import score_inter_run_similarity
from app.eval.personality import score_personality_consistency_embedding
from app.eval.repetition import score_repetition
from app.eval.tool_validity import tool_validity_rate
from app.eval.types import RunRecord
from app.game.schemas import Agent


def compute_per_run_oracles(
    run: RunRecord, *, agents: list[Agent]
) -> dict[str, Any]:
    """Compute per-run oracles for one RunRecord.

    `agents` must come from the final game-session state — the harness reads
    them from the DB after the run completes (see run_simulator._load_final_state).
    """
    by_agent_id: dict[UUID, Agent] = {a.id: a for a in agents}
    personality_per_agent: dict[str, float] = {}
    for agent_id, responses in run.agent_responses_per_agent.items():
        agent = by_agent_id.get(agent_id)
        if agent is None:
            continue
        personality_per_agent[str(agent_id)] = float(
            score_personality_consistency_embedding(agent, responses)
        )

    return {
        "tool_validity_rate": float(tool_validity_rate(run.tool_calls)),
        "repetition_bleu2": float(score_repetition(run.narration_per_turn)),
        "personality_consistency": personality_per_agent,
    }


def compute_corpus_oracles(runs: list[RunRecord]) -> dict[str, Any]:
    """Compute corpus-level oracles across all runs.

    `inter_run_similarity` operates on per-run narration concatenations.
    `end_state_distribution` checks against SPEC §10 target bands.
    """
    flattened_narrations = ["\n\n".join(r.narration_per_turn) for r in runs]
    sim = float(score_inter_run_similarity(flattened_narrations))

    end_state_result = score_end_state_distribution(runs)

    return {
        "inter_run_similarity": sim,
        "end_state_distribution": end_state_result.distribution,
        "spec_target_match": bool(end_state_result.matches_targets),
    }
```

**Note for the implementer:** this assumes `score_repetition`, `score_inter_run_similarity`, and `score_end_state_distribution` exist with these signatures. **Verify before running:**

```bash
cd backend && grep -n "^def score_" app/eval/repetition.py app/eval/inter_run_similarity.py app/eval/end_state.py
```

If signatures differ (e.g., `score_repetition` takes a `RunRecord` instead of a `list[str]`), adapt the call sites accordingly. If a function doesn't exist, add a TODO comment to the spec — do not invent.

- [ ] **Step 4: Run oracle tests + lint**

```bash
cd backend && uv run pytest tests/eval/playtest/test_oracles.py -v && uv run ruff check app/eval/playtest/oracles.py tests/eval/playtest/test_oracles.py && uv run ruff format --check app/eval/playtest/oracles.py tests/eval/playtest/test_oracles.py
```

Expected: 2 pass, ruff clean.

- [ ] **Step 5: Commit**

```bash
git add backend/app/eval/playtest/oracles.py backend/tests/eval/playtest/test_oracles.py
git commit -m "$(cat <<'EOF'
feat(playtest): wrap five structural oracles for aggregator consumption

Per-run: tool_validity_rate, repetition_bleu2, personality_consistency
per agent. Corpus: inter_run_similarity, end_state_distribution +
spec_target_match. The aggregator uses these alongside persona findings
to classify each fix as high_confidence / persona_only / oracle_only /
disagreement (spec §7).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: Output emission (markdown + JSON)

**Files:**
- Create: `backend/app/eval/playtest/output.py`
- Create: `backend/tests/eval/playtest/test_output.py`

Emits per-run dual artifacts (markdown + JSON), the corpus-level `runs.json`, and a `report.md` skeleton (the meta-aggregator overwrites the skeleton's body with its synthesis later; this module only emits the harness-side scaffolding).

- [ ] **Step 1: Write output emission test (failing)**

Create `backend/tests/eval/playtest/test_output.py`:

```python
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
    AxisScore,
    ContinueTomorrow,
    PersonaRunVerdict,
    PersonaTurnFlags,
    PersonaTurnOutput,
    VerdictAxes,
)
from app.eval.types import RunRecord
from app.game.schemas import EndState


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
        verdict=PersonaRunVerdict(
            axes=VerdictAxes(
                stakes_felt_real=AxisScore(score=4, rationale="x", turn_citations=[]),
                tonal_coherence=AxisScore(score=5, rationale="x", turn_citations=[]),
                advisor_distinctness=AxisScore(score=4, rationale="x", turn_citations=[]),
                dramatic_arc=AxisScore(score=4, rationale="x", turn_citations=[]),
            ),
            continue_tomorrow=ContinueTomorrow(decision=True, reason="liked it"),
            closing_paragraph="...",
        ),
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
```

- [ ] **Step 2: Run failing test**

```bash
cd backend && uv run pytest tests/eval/playtest/test_output.py -v
```

Expected: ImportError.

- [ ] **Step 3: Implement output module**

Create `backend/app/eval/playtest/output.py`:

```python
"""Emits per-run dual archives + corpus runs.json + report.md skeleton.

Per spec §9: dual format per run (markdown for LLM consumption, JSON for
schema-validated machine access). report.md leads with honest-limits
boilerplate; the meta-aggregator (invoked separately via the slash
command) replaces the body below the marker with its synthesis.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.eval.playtest.schemas import PersonaRunVerdict, PersonaTurnOutput

HONEST_LIMITS_BOILERPLATE = """\
# Outpost play-test report

## Honest limits — read first

This harness is a **high-recall, low-precision lint pass** — an *obviousness detector,
not a taste detector*. It catches: tone breaks, archetype slippage, line repetition
across runs, end-state distribution mismatch vs SPEC §10, difficulty-rail invisibility,
counterfactual coverage. It does **not** catch: whether the game is fun, whether the
daily ritual feels good, whether real players return tomorrow.

- Persona role-identification accuracy in the literature caps near 69% (PersonaEval, 2025);
  treat persona-level granularity as soft.
- Forty-two runs from one model are not forty-two independent samples — synthetic
  personas occupy a bounded region of the personality space (UXtweak review, 2026).
- RLHF training reduces or erases negative emotion; tone-break detection underweights
  flat-but-plausible prose.
- Sonnet 4.6 personas evaluating Haiku 4.5 game prose share a model family. Self-preference
  bias inflates absolute scores by an unmeasured offset (arXiv 2410.21819). **Aggregator
  outputs ordinal/relative claims only**; never absolute predictions of real-player rates.
- Synthetic-user believability is itself a risk: it lends false credibility to
  misleading conclusions (UXtweak review, 2026).
"""

MARKER_AGGREGATOR_BEGIN = "<!-- META_AGGREGATOR_OUTPUT_BELOW -->"


@dataclass
class PerRunArchive:
    """Bundle of one run's harness output, ready to be written to disk."""

    run_id: str
    persona_id: str
    setting_id: str
    seed: int
    end_state: str
    turns_played: int
    per_turn_outputs: list[PersonaTurnOutput]
    verdict: PersonaRunVerdict | None
    run_record_summary: dict[str, Any]  # subset of RunRecord fields
    per_run_oracles: dict[str, Any]


def _archive_basename(a: PerRunArchive) -> str:
    return f"{a.persona_id}-{a.setting_id}-seed{a.seed}"


def _render_per_run_markdown(a: PerRunArchive) -> str:
    """Markdown rendering of one run — the LLM-friendly archive format."""
    lines: list[str] = [
        f"# Run: {a.persona_id} / {a.setting_id} / seed {a.seed}",
        "",
        f"**Persona:** {a.persona_id}",
        f"**Setting:** {a.setting_id}",
        f"**End state:** {a.end_state}",
        f"**Turns played:** {a.turns_played}",
        "",
        "---",
        "",
    ]
    for t in a.per_turn_outputs:
        lines.extend(
            [
                f"## Turn {t.turn}",
                "",
                f"**Displayed order:** {t.displayed_order}",
                f"**Chose:** `{t.chosen_proposal_id}` (position {t.shown_position_of_chosen})",
                "",
                f"**Reaction:** {t.reaction}",
                "",
                "**Flags:** "
                + ", ".join(
                    f"{k}={'✓' if v else '·'}"
                    for k, v in t.flags.model_dump().items()
                ),
            ]
        )
        if t.quotes:
            lines.append("")
            lines.append("**Quotes:**")
            for k, v in t.quotes.items():
                lines.append(f"- *{k}*: {v}")
        lines.append("")
        lines.append("---")
        lines.append("")

    if a.verdict is not None:
        lines.extend(["## Run-level verdict", ""])
        for axis_name, axis in a.verdict.axes.model_dump().items():
            human_name = axis_name.replace("_", " ").capitalize()
            lines.append(f"### {human_name} — {axis['score']}/5")
            lines.append("")
            lines.append(axis["rationale"])
            if axis["turn_citations"]:
                lines.append("")
                lines.append(f"_Cited turns: {axis['turn_citations']}_")
            lines.append("")
        lines.extend(
            [
                f"### Continue tomorrow — **{'YES' if a.verdict.continue_tomorrow.decision else 'NO'}**",
                "",
                f"_Reason: {a.verdict.continue_tomorrow.reason}_",
                "",
                "### Closing",
                "",
                a.verdict.closing_paragraph,
                "",
            ]
        )
    else:
        lines.extend(["## Run-level verdict", "", "_(verdict missing — persona failed final response)_", ""])

    return "\n".join(lines)


def _archive_to_dict(a: PerRunArchive) -> dict[str, Any]:
    return {
        "run_id": a.run_id,
        "persona_id": a.persona_id,
        "setting_id": a.setting_id,
        "seed": a.seed,
        "end_state": a.end_state,
        "turns_played": a.turns_played,
        "per_turn_outputs": [t.model_dump() for t in a.per_turn_outputs],
        "verdict": a.verdict.model_dump() if a.verdict else None,
        "run_record_summary": a.run_record_summary,
        "per_run_oracles": a.per_run_oracles,
    }


def write_per_run_archives(out_dir: Path, archives: list[PerRunArchive]) -> None:
    """Writes raw/<basename>.md and raw/<basename>.json for each run."""
    raw_dir = out_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    for a in archives:
        base = _archive_basename(a)
        (raw_dir / f"{base}.md").write_text(_render_per_run_markdown(a))
        (raw_dir / f"{base}.json").write_text(
            json.dumps(_archive_to_dict(a), indent=2, default=str)
        )


def write_corpus_artifacts(
    out_dir: Path,
    *,
    archives: list[PerRunArchive],
    findings: dict[str, Any],
    corpus_oracles: dict[str, Any],
    git_sha: str,
    wall_clock_seconds: int,
    harness_version: str = "0.1.0",
) -> None:
    """Writes runs.json (structured corpus data) + report.md (skeleton with
    honest-limits boilerplate; aggregator overwrites body below marker)."""
    out_dir.mkdir(parents=True, exist_ok=True)

    persona_counter: Counter[str] = Counter(a.persona_id for a in archives)
    per_persona_n = max(persona_counter.values()) if persona_counter else 0

    per_run_summaries: list[dict[str, Any]] = []
    flag_aggregates: dict[str, int] = {
        "surprise": 0,
        "tone_break": 0,
        "advisor_oc": 0,
        "stakes_low": 0,
    }
    for a in archives:
        flag_counts = {k: 0 for k in flag_aggregates}
        for t in a.per_turn_outputs:
            for k, v in t.flags.model_dump().items():
                if v:
                    flag_counts[k] += 1
                    flag_aggregates[k] += 1

        axis_scores: dict[str, Any] = {}
        if a.verdict is not None:
            for k, axis in a.verdict.axes.model_dump().items():
                axis_scores[k] = axis["score"]
            axis_scores["continue_tomorrow"] = a.verdict.continue_tomorrow.decision

        per_run_summaries.append(
            {
                "run_id": a.run_id,
                "persona": a.persona_id,
                "setting": a.setting_id,
                "seed": a.seed,
                "end_state": a.end_state,
                "turns_played": a.turns_played,
                "axis_scores": axis_scores,
                "flag_counts": flag_counts,
                "structural_oracles": a.per_run_oracles,
                "raw_md_path": f"raw/{_archive_basename(a)}.md",
                "raw_json_path": f"raw/{_archive_basename(a)}.json",
            }
        )

    runs_payload: dict[str, Any] = {
        "harness_version": harness_version,
        "git_sha": git_sha,
        "wall_clock_seconds": wall_clock_seconds,
        "personas": sorted(persona_counter.keys()),
        "per_persona_n": per_persona_n,
        "per_run_summaries": per_run_summaries,
        "flag_aggregates": flag_aggregates,
        "corpus_oracles": corpus_oracles,
        "harness_findings": findings,
    }
    (out_dir / "runs.json").write_text(json.dumps(runs_payload, indent=2, default=str))

    report_skeleton = (
        HONEST_LIMITS_BOILERPLATE
        + "\n\n"
        + "## Header\n\n"
        + f"- Git SHA: `{git_sha}`\n"
        + f"- Wall clock: {wall_clock_seconds // 60} min\n"
        + f"- Total runs: {len(archives)}\n"
        + f"- Personas × runs each: {len(persona_counter)} × {per_persona_n}\n"
        + "\n"
        + MARKER_AGGREGATOR_BEGIN
        + "\n\n"
        + "_(The meta-aggregator overwrites everything below this marker. "
        + "Until then, this section is empty.)_\n"
    )
    (out_dir / "report.md").write_text(report_skeleton)
```

- [ ] **Step 4: Run output tests + lint**

```bash
cd backend && uv run pytest tests/eval/playtest/test_output.py -v && uv run ruff check app/eval/playtest/output.py tests/eval/playtest/test_output.py && uv run ruff format --check app/eval/playtest/output.py tests/eval/playtest/test_output.py
```

Expected: 2 pass, ruff clean.

- [ ] **Step 5: Commit**

```bash
git add backend/app/eval/playtest/output.py backend/tests/eval/playtest/test_output.py
git commit -m "$(cat <<'EOF'
feat(playtest): emit dual-format archives + runs.json + report skeleton

write_per_run_archives writes raw/<persona>-<setting>-seed<n>.md (LLM-
friendly narrative) AND .json (canonical schema-validated). write_corpus
_artifacts writes runs.json (cross-time delta source) and report.md
seeded with HONEST_LIMITS_BOILERPLATE — the meta-aggregator (invoked via
/playtest-aggregate) replaces everything below MARKER_AGGREGATOR_BEGIN.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 8: Orchestrator script

**Files:**
- Create: `backend/scripts/__init__.py`
- Create: `backend/scripts/play_test.py`
- Create: `backend/tests/eval/playtest/test_orchestrator_smoke.py`

Ties everything together. Drives 42 simulated games through `simulate_game` with `LLMPlayer` swapped in, emits archives, and reports. The meta-aggregator runs separately (Task 9 slash command).

This task is the largest. The smoke test runs 1 game with everything mocked to verify wiring; real-LLM end-to-end is gated behind `OUTPOST_PLAYTEST_REAL_LLM=1` (Task 10).

- [ ] **Step 1: Write orchestrator smoke test (failing)**

Create `backend/tests/eval/playtest/test_orchestrator_smoke.py`:

```python
"""End-to-end smoke for the orchestrator with subprocess + Anthropic mocked.

Confirms: simulate_game runs with LLMPlayer; per-run archives are written;
runs.json / report.md scaffolding lands."""

from __future__ import annotations

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
    eval_user_id,  # provided by tests/eval/conftest.py via parent dir scan
    graph_postgres,
    tmp_path: Path,
) -> None:
    """Smoke run: 1 persona × 1 game with everything mocked. Verifies that
    the orchestrator produces docs/playtest-style artifacts."""
    from tests.fixtures._factories import make_haiku_debate_side_effect

    # Subprocess side: always return a canned valid PersonaTurnOutput
    # for the first proposal id we see in the prompt's displayed_order.
    def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
        import ast

        user_msg = cmd[-1]
        line = next(
            line for line in user_msg.splitlines() if "displayed_order" in line
        )
        bracket_start = line.find("[")
        bracket_end = line.rfind("]") + 1
        order = ast.literal_eval(line[bracket_start:bracket_end])
        canned = canned_persona_response_factory(
            chosen_proposal_id=order[0], displayed_order=order
        )
        return subprocess.CompletedProcess(
            args=cmd, returncode=0, stdout=canned, stderr=""
        )

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
                user_id=eval_user_id,
                max_turns=3,
                max_concurrency=1,
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
```

- [ ] **Step 2: Run failing test**

```bash
cd backend && uv run pytest tests/eval/playtest/test_orchestrator_smoke.py -v -m eval
```

Expected: ImportError.

- [ ] **Step 3: Implement scripts package init + orchestrator skeleton**

```bash
touch backend/scripts/__init__.py
```

**Note on placement:** The orchestrator lives at `backend/scripts/play_test.py` (not under `app/`). Reason: it imports from `tests/fixtures/run_simulator.py` to drive the in-process ASGI game flow. Application code (`app/`) shouldn't depend on test fixtures; `scripts/` is a top-level utility package where this dependency is acceptable. The reusable components (schemas, personas, llm_player, oracles, output) stay under `app/eval/playtest/`.

Create `backend/scripts/play_test.py`:

```python
"""Orchestrator: drives N simulated games with LLMPlayer + emits archives.

Entry points:
  - `run_harness(config)` — async, programmatic API used by tests
  - `main()` — CLI wrapper (uv run python -m scripts.play_test)

The CLI defaults to: 7 personas × 6 runs × 3 settings (round-robin) = 42 games.
Real-LLM mode requires `ANTHROPIC_API_KEY`. The persona side runs through
`claude` CLI and bills against the user's Claude Code Max subscription.

The meta-aggregator runs SEPARATELY via the /playtest-aggregate slash command
in the user's Claude Code session — see .claude/commands/playtest-aggregate.md.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import select

from app.database import async_session_factory
from app.dependencies import get_current_user
from app.eval.playtest.llm_player import LLMPlayer
from app.eval.playtest.oracles import compute_corpus_oracles, compute_per_run_oracles
from app.eval.playtest.output import (
    PerRunArchive,
    write_corpus_artifacts,
    write_per_run_archives,
)
from app.eval.playtest.personas import PERSONAS, get_persona
from app.eval.playtest.schemas import PersonaId
from app.eval.playtest.subprocess_pool import SubprocessPool
from app.eval.types import RunRecord
from app.game.models import Agent as AgentModel
from app.game.models import GameSession
from app.game.post_mortem import build_post_mortem
from app.game.schemas import Agent, ColonyState

logger = logging.getLogger(__name__)


@dataclass
class PlaytestConfig:
    """Runtime configuration for one harness invocation."""

    personas: list[PersonaId] = field(default_factory=lambda: [p.id for p in PERSONAS])
    runs_per_persona: int = 6
    settings: list[str] = field(
        default_factory=lambda: [
            "arctic_station_v1",
            "orbital_station_v1",
            "desert_outpost_v1",
        ]
    )
    out_dir: Path = field(default_factory=lambda: Path("docs/playtest"))
    shell_dir: Path = field(default_factory=lambda: Path("/tmp/outpost-playtest-shell"))
    user_id: UUID = field(default_factory=uuid.uuid4)
    max_turns: int = 12
    max_concurrency: int = 5
    git_sha: str = "unknown"


def _allocate_runs(config: PlaytestConfig) -> list[tuple[PersonaId, str, int]]:
    """Round-robin (persona, setting, seed) tuples. Each persona plays each
    setting at least floor(runs_per_persona / len(settings)) times."""
    out: list[tuple[PersonaId, str, int]] = []
    for persona_id in config.personas:
        for run_idx in range(config.runs_per_persona):
            setting = config.settings[run_idx % len(config.settings)]
            seed = hash((persona_id, setting, run_idx)) & 0x7FFFFFFF
            out.append((persona_id, setting, seed))
    return out


async def _drive_one_game(
    *,
    persona_id: PersonaId,
    setting_id: str,
    seed: int,
    pool: SubprocessPool,
    config: PlaytestConfig,
) -> PerRunArchive:
    """Drive one simulated game with an LLMPlayer. Returns the archive bundle.

    Uses a per-game UUID to stay under the daily-gate (each game is by a
    distinct synthetic user_id). The PostgresSaver lifetime is shared.
    """
    from tests.fixtures.run_simulator import simulate_game  # noqa: PLC0415 — test fixture import

    persona = get_persona(persona_id)
    per_game_user_id = uuid.uuid4()

    # We need a turn_input_provider for the LLMPlayer. The provider is called
    # by LLMPlayer.pick at each turn; it returns the harness-formatted input
    # (see persona_session._format_turn_user_message).
    #
    # In this skeleton we use a simple ref-cell that the orchestrator updates
    # before each /turn invocation. Production wiring lives in the
    # per-turn callback below.
    last_turn_input: dict[str, Any] = {}

    def turn_input_provider() -> dict[str, Any]:
        return dict(last_turn_input)

    # NOTE FOR IMPLEMENTER: simulate_game's existing signature takes a
    # `policy` string and constructs a SimulatedPlayer internally. To plug
    # in LLMPlayer we need to either (a) extend simulate_game to accept a
    # `player` kwarg, or (b) add a new `simulate_game_with_player` variant.
    # Option (b) is non-invasive — does not change the existing eval pipeline.
    # See Task 8 step 4.
    raise NotImplementedError(
        "LLMPlayer-aware simulate_game variant not yet wired — see step 4"
    )


async def run_harness(*, config: PlaytestConfig) -> None:
    """Top-level entry. Allocates runs, drives them with bounded concurrency,
    emits archives + corpus artifacts."""
    config.out_dir.mkdir(parents=True, exist_ok=True)
    config.shell_dir.mkdir(parents=True, exist_ok=True)
    pool = SubprocessPool(max_concurrency=config.max_concurrency)
    started = time.monotonic()

    allocations = _allocate_runs(config)
    archives: list[PerRunArchive] = []
    runs_for_corpus: list[RunRecord] = []

    sema = asyncio.Semaphore(config.max_concurrency)

    async def go(persona_id: PersonaId, setting_id: str, seed: int) -> None:
        async with sema:
            try:
                arc = await _drive_one_game(
                    persona_id=persona_id,
                    setting_id=setting_id,
                    seed=seed,
                    pool=pool,
                    config=config,
                )
                archives.append(arc)
            except Exception as exc:  # noqa: BLE001 — log and continue
                logger.exception("game %s/%s/%s failed: %s", persona_id, setting_id, seed, exc)
                pool.findings.aborted_runs.append(
                    {
                        "persona": persona_id,
                        "setting": setting_id,
                        "seed": seed,
                        "reason": str(exc),
                    }
                )

    await asyncio.gather(*(go(p, s, sd) for (p, s, sd) in allocations))

    # Reconstruct RunRecords for corpus oracles (per-archive run_record_summary
    # carries the reduced-shape fields).
    for a in archives:
        rr = RunRecord(
            setting_id=a.setting_id,
            policy=a.persona_id,
            seed=a.seed,
            end_state=a.run_record_summary.get("end_state"),
            turns_played=a.turns_played,
            final_player_health=a.run_record_summary.get("final_player_health", 0),
            agents_alive_at_end=a.run_record_summary.get("agents_alive_at_end", 0),
            total_agents=a.run_record_summary.get("total_agents", 0),
            cost_cents=a.run_record_summary.get("cost_cents", 0),
            tool_calls=a.run_record_summary.get("tool_calls", []),
            agent_responses_per_agent=a.run_record_summary.get(
                "agent_responses_per_agent", {}
            ),
            resource_history=a.run_record_summary.get("resource_history", {}),
            forced_choice_turns=a.run_record_summary.get("forced_choice_turns", 0),
            narration_per_turn=a.run_record_summary.get("narration_per_turn", []),
        )
        runs_for_corpus.append(rr)

    corpus_oracles = compute_corpus_oracles(runs_for_corpus)
    findings_dict = {
        "malformed_json_count": pool.findings.malformed_json_count,
        "invalid_pick_count": pool.findings.invalid_pick_count,
        "persona_refusals": pool.findings.persona_refusals,
        "subprocess_hangs": pool.findings.subprocess_hangs,
        "subprocess_crashes": pool.findings.subprocess_crashes,
        "aborted_runs": pool.findings.aborted_runs,
        "by_persona": dict(pool.findings.by_persona),
    }

    write_per_run_archives(config.out_dir, archives)
    write_corpus_artifacts(
        config.out_dir,
        archives=archives,
        findings=findings_dict,
        corpus_oracles=corpus_oracles,
        git_sha=config.git_sha,
        wall_clock_seconds=int(time.monotonic() - started),
    )

    logger.info(
        "harness complete: %d runs in %d sec → %s",
        len(archives),
        int(time.monotonic() - started),
        config.out_dir,
    )


def main() -> None:  # pragma: no cover — CLI entry
    """CLI: defaults to 7 × 6 = 42 runs across 3 settings."""
    import argparse
    import os

    p = argparse.ArgumentParser(description="Run the LLM play-test harness.")
    p.add_argument("--out", default=None, help="Output dir; default docs/playtest/<date>")
    p.add_argument("--max-concurrency", type=int, default=5)
    p.add_argument("--runs-per-persona", type=int, default=6)
    args = p.parse_args()

    from datetime import datetime, timezone

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out_dir = Path(args.out) if args.out else Path("docs/playtest") / today

    git_sha = os.environ.get("OUTPOST_GIT_SHA", "unknown")

    config = PlaytestConfig(
        out_dir=out_dir,
        max_concurrency=args.max_concurrency,
        runs_per_persona=args.runs_per_persona,
        git_sha=git_sha,
    )

    asyncio.run(run_harness(config=config))


if __name__ == "__main__":  # pragma: no cover
    main()
```

- [ ] **Step 4: Add `simulate_game` overload that accepts an injected player**

The existing `simulate_game` in `tests/fixtures/run_simulator.py` constructs a `SimulatedPlayer` from a `policy` string. To inject `LLMPlayer`, we need an overload that accepts a pre-built player.

Modify `backend/tests/fixtures/run_simulator.py`. After the existing `simulate_game` definition (around line 162-293), add:

```python
async def simulate_game_with_player(
    *,
    setting_id: str,
    seed: int = 0,
    real_llm: bool = False,
    max_turns: int = 12,
    user_id: UUID,
    player: Any,  # ducktyped to SimulatedPlayer.pick(proposals) -> str
    on_turn_input: Callable[[dict[str, Any]], None] | None = None,
) -> RunRecord:
    """Variant of simulate_game that accepts a pre-built player and emits
    per-turn input to a callback. Used by the LLM playtest orchestrator.

    Differences vs. simulate_game:
      - No `policy` argument; caller passes an already-constructed `player`
      - `on_turn_input(turn_input_dict)` fires before each pick so the LLMPlayer
        can wire its turn_input_provider closure
    """
    aggregator = _RunAggregator()

    app.dependency_overrides[get_current_user] = lambda: _auth_override(user_id)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            create_resp = await client.post("/game/session")
            assert create_resp.status_code == 201, create_resp.text
            game_id = UUID(create_resp.json()["game_id"])

            for _turn_idx in range(max_turns):
                async with client.stream("POST", f"/game/{game_id}/turn") as resp:
                    assert resp.status_code == 200, await resp.aread()
                    turn_frames = await _collect_stream(resp)
                aggregator.absorb_frames(turn_frames)

                if any(f["event"] == "run_over" for f in turn_frames):
                    break

                pr = next((f for f in turn_frames if f["event"] == "proposals_ready"), None)
                if pr is None:
                    break
                proposals = _proposals_from_frame(pr)
                if not proposals:
                    break

                # Build the per-turn input dict for the LLM persona BEFORE pick().
                if on_turn_input is not None:
                    on_turn_input(_assemble_turn_input(turn_frames, proposals, _turn_idx + 1))

                chosen_id = player.pick(proposals)

                async with client.stream(
                    "POST",
                    f"/game/{game_id}/decide",
                    json={"chosen_proposal_id": chosen_id},
                ) as resp:
                    assert resp.status_code == 200, await resp.aread()
                    decide_frames = await _collect_stream(resp)
                aggregator.absorb_frames(decide_frames)

                if any(f["event"] == "run_over" for f in decide_frames):
                    break
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    return await _build_run_record(game_id, aggregator, policy=str(getattr(player, "persona", "llm")), seed=seed)


def _assemble_turn_input(
    turn_frames: list[dict[str, Any]], proposals: list[dict[str, Any]], turn_number: int
) -> dict[str, Any]:
    """Project SSE frames + dashboard state into the dict shape that
    PersonaSession._format_turn_user_message expects."""
    # Pluck the dashboard frame, advisor reactions, the event:
    dashboard = next((f["data"] for f in turn_frames if f["event"] == "dashboard"), {})
    event = next((f["data"] for f in turn_frames if f["event"] == "event_intro"), {})
    advisor_reactions = [
        f["data"] for f in turn_frames if f["event"] == "agent_spoke"
    ]
    return {
        "turn": turn_number,
        "setting_lore": dashboard.get("setting_lore", ""),
        "dashboard": dashboard,
        "event": event,
        "proposals": proposals,
        "advisor_reactions": advisor_reactions,
    }


async def _build_run_record(
    game_id: UUID, aggregator: _RunAggregator, *, policy: str, seed: int
) -> RunRecord:
    """Shared tail logic factored out so simulate_game and simulate_game_with_player
    can both build a RunRecord from final state + turn_logs."""
    final_state = await _load_final_state(game_id)
    turn_logs = await _read_turn_logs(game_id)
    tool_calls: list[ToolCall] = []
    forced_choice_turns = 0
    resource_history: dict[str, list[int]] = {}
    for row in turn_logs:
        tool_calls.extend(_tool_calls_from_turn_log(row))
        if len(row.proposals or []) <= 1:
            forced_choice_turns += 1
        snap_resources = (row.state_snapshot or {}).get("resources") or {}
        for rid, rdict in snap_resources.items():
            value = int(rdict.get("value", 0))
            resource_history.setdefault(rid, []).append(value)

    return RunRecord(
        setting_id=final_state.setting_id,
        policy=policy,
        seed=seed,
        end_state=final_state.end_state,
        turns_played=final_state.turn_number,
        final_player_health=final_state.player_health,
        agents_alive_at_end=sum(1 for a in final_state.agents if a.is_alive),
        total_agents=len(final_state.agents),
        cost_cents=final_state.turn_cost_cents,
        tool_calls=tool_calls,
        agent_responses_per_agent=aggregator.agent_responses_per_agent,
        resource_history=resource_history,
        forced_choice_turns=forced_choice_turns,
        narration_per_turn=aggregator.narration_per_turn,
    )
```

Add the necessary imports at the top of `run_simulator.py`:

```python
from collections.abc import Callable
```

And update the `__all__` export at the bottom:

```python
__all__ = ["simulate_game", "simulate_game_with_player", "_read_turn_logs"]
```

Also refactor the existing `simulate_game` to call `_build_run_record` for DRY (replace the final tail of `simulate_game` after the for-loop with `return await _build_run_record(game_id, aggregator, policy=policy, seed=seed)`).

- [ ] **Step 5: Wire `_drive_one_game` to call `simulate_game_with_player`**

Replace the body of `_drive_one_game` in `backend/scripts/play_test.py`:

```python
    persona = get_persona(persona_id)
    per_game_user_id = uuid.uuid4()
    last_turn_input: dict[str, Any] = {}

    def turn_input_provider() -> dict[str, Any]:
        return dict(last_turn_input)

    def on_turn_input(d: dict[str, Any]) -> None:
        last_turn_input.clear()
        last_turn_input.update(d)

    player = LLMPlayer(
        persona=persona,
        pool=pool,
        shell_dir=config.shell_dir,
        seed=seed,
        turn_input_provider=turn_input_provider,
    )

    from tests.fixtures.run_simulator import simulate_game_with_player  # noqa: PLC0415

    run_record = await simulate_game_with_player(
        setting_id=setting_id,
        seed=seed,
        real_llm=False,
        max_turns=config.max_turns,
        user_id=per_game_user_id,
        player=player,
        on_turn_input=on_turn_input,
    )

    # End-of-run: get the post-mortem and ask the persona for its verdict.
    async with async_session_factory() as session:
        session_row = (
            await session.execute(
                select(GameSession).where(GameSession.user_id == per_game_user_id)
            )
        ).scalar_one()
        post_mortem = await build_post_mortem(game_id=session_row.id, db=session)

    verdict = pool.run_final_verdict(
        session=player._session,  # noqa: SLF001 — orchestrator is the only legit consumer
        post_mortem_json=post_mortem.model_dump(),
    )

    # Compute per-run oracles (need agents from the final ColonyState).
    final_state = ColonyState.model_validate(session_row.state)
    per_run_oracles = compute_per_run_oracles(run_record, agents=final_state.agents)

    return PerRunArchive(
        run_id=str(session_row.id),
        persona_id=persona_id,
        setting_id=setting_id,
        seed=seed,
        end_state=str(run_record.end_state.value if run_record.end_state else "UNKNOWN"),
        turns_played=run_record.turns_played,
        per_turn_outputs=player.per_turn_outputs,
        verdict=verdict,
        run_record_summary={
            "end_state": run_record.end_state,
            "final_player_health": run_record.final_player_health,
            "agents_alive_at_end": run_record.agents_alive_at_end,
            "total_agents": run_record.total_agents,
            "cost_cents": run_record.cost_cents,
            "tool_calls": [tc.model_dump() for tc in run_record.tool_calls],
            "agent_responses_per_agent": {
                str(k): v for k, v in run_record.agent_responses_per_agent.items()
            },
            "resource_history": run_record.resource_history,
            "forced_choice_turns": run_record.forced_choice_turns,
            "narration_per_turn": run_record.narration_per_turn,
        },
        per_run_oracles=per_run_oracles,
    )
```

Remove the `raise NotImplementedError` line from step 3.

- [ ] **Step 6: Run smoke test + lint**

```bash
cd backend && DATABASE_URL="postgresql+psycopg://postgres:postgres@localhost:54322/postgres" uv run pytest tests/eval/playtest/test_orchestrator_smoke.py -v -m eval && uv run ruff check scripts/ tests/eval/playtest/test_orchestrator_smoke.py tests/fixtures/run_simulator.py && uv run ruff format --check scripts/ tests/eval/playtest/test_orchestrator_smoke.py tests/fixtures/run_simulator.py
```

Expected: 1 pass (or SKIP if Postgres unreachable — pytest.skip pattern from existing eval tests applies), ruff clean.

If the test fails because `simulate_game_with_player` chokes on dashboard frame shape, debug by inspecting the SSE frames produced by the existing test fixture. The frame names (`dashboard`, `event_intro`, `agent_spoke`, `proposals_ready`, `narration`, `run_over`) come from `app/agent/service.py` — read it before guessing.

- [ ] **Step 7: Commit**

```bash
git add backend/scripts/__init__.py backend/scripts/play_test.py backend/tests/fixtures/run_simulator.py backend/tests/eval/playtest/test_orchestrator_smoke.py
git commit -m "$(cat <<'EOF'
feat(playtest): orchestrator + simulate_game_with_player overload

run_harness allocates runs (round-robin persona × setting × seed),
drives each via simulate_game_with_player (a non-invasive overload of
simulate_game that accepts a pre-built player and an on_turn_input
callback so the LLMPlayer's turn_input_provider closure can be wired).
End-of-run: build_post_mortem feeds the verdict prompt; per-run +
corpus oracles are computed; archives + runs.json + report.md skeleton
are written. Smoke test runs one game with mocks.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 9: Aggregator slash command

**Files:**
- Create: `.claude/commands/playtest-aggregate.md`

The slash command is the user-invoked entry point for the meta-aggregator. When the user types `/playtest-aggregate <run-dir>` from inside their Claude Code session, the prompt expands and the orchestrator runs an `Agent` subagent (Opus 4.7, `--effort max`) over the raw archives.

- [ ] **Step 1: Verify `.claude/` is appropriate to commit**

```bash
cat /Users/luclacombe/Documents/Outpost/.gitignore | grep -i claude || echo "no claude entries in .gitignore — safe to commit"
ls /Users/luclacombe/Documents/Outpost/.claude/ 2>/dev/null
```

If `.gitignore` has `.claude/` listed, this slash command must instead live where the project allows. Common pattern: commit `.claude/commands/` but gitignore other subfolders. If `.claude/` is fully ignored, change the path to `docs/superpowers/skills/playtest-aggregate.md` and document its invocation as a manual prompt-paste rather than a slash command.

- [ ] **Step 2: Create the slash command**

Create `.claude/commands/playtest-aggregate.md`:

```markdown
---
description: Run the LLM playtest meta-aggregator over a run directory. Args: <run-dir-path>
argument-hint: docs/playtest/YYYY-MM-DD
---

You are the meta-aggregator for an Outpost play-test harness invocation. The
harness has already run; raw per-run archives + corpus runs.json are at:

`$ARGUMENTS`

# Your task

Read the harness output and produce a comprehensive `report.md` synthesis that
replaces everything in `$ARGUMENTS/report.md` BELOW the
`<!-- META_AGGREGATOR_OUTPUT_BELOW -->` marker. Do NOT touch the boilerplate
above the marker (it's the canonical honest-limits text and must stay verbatim).

# Inputs

1. `$ARGUMENTS/runs.json` — structured corpus aggregate including per-run summaries,
   axis means, flag aggregates, harness_findings, structural_oracles, position_bias
2. `$ARGUMENTS/raw/*.md` — per-run narrative archives (LLM-friendly format).
   Read every one before synthesizing.
3. `docs/SPEC.md` — read §§4-7, 10, 11, 14. Map every finding to a SPEC section.
4. `docs/superpowers/specs/2026-04-26-llm-playtest-harness-design.md` —
   the design spec. §0 has the exact honest-limits wording. §8 lists every
   section your output must include.

# Output sections (replace everything below the marker, in this order)

1. **Header** — git SHA, wall clock, total runs, harness_findings summary in one line
2. **Top fixes — ranked 1-10** by severity × prevalence × confidence. Each:
   - **SPEC tag** — the §-numbered SPEC section the finding violates
   - **Group tag** — one of: writing-voice, balance, pacing, advisor-system,
     post-mortem, dashboard, retention
   - **Confidence label** — one of: `high_confidence`, `persona_only`,
     `oracle_only`, `disagreement`
   - **Evidence** — quotes (verbatim from raw/*.md) with `[persona:turn:seed]`
     citations + relevant structural oracle values from runs.json
   - **Proposed fix** — concrete, single-action where possible
   - **Self-rebuttal** (only on item #1) — the strongest counter-argument
3. **Cross-persona disagreements** — axis-by-axis splits with hypothesized cause
4. **Setting-specific findings** — per setting: end-state distribution vs SPEC §10
   bands, tone-coherence average, top 3 tone-break quotes. **Frame as ordinal
   ratios only** (e.g. "3× more tone-break flags in desert vs arctic"). Never as
   absolute predictions.
5. **Cross-run patterns** — opening-line repetition, advisor-line repetition,
   advisor drift across runs. Cite specific runs.
6. **Oracle-only findings** — patterns the structural oracles caught that no
   persona flagged. (Personality-consistency embedding spike on turn N where no
   persona reported `advisor_oc`, etc.)
7. **Blind spots** — patterns YOU surfaced without being explicitly asked for
8. **Per-persona summary** — one section per persona: characteristic quote, axis
   averages from runs.json, what they flagged most, position-bias check (Spearman
   ρ between display position and pick rate from runs.json)
9. **Run roster** — 42-row table linking each run to its raw markdown + JSON

# Confidence scaffolding (REQUIRED on every claim)

Every claim must cite:
- (a) prevalence as N/total ("4/6 runs flagged")
- (b) which personas flagged it (with run seeds)
- (c) ≥1 specific quote with turn number
- (d) confidence label per §7 of the design spec

A bare claim without all four is rejected — do not write it.

# Red-team requirements

- "What questions did this harness fail to ask?" — explicit blind-spots section
- "If someone hostile wanted to argue Outpost is unfit to ship, what evidence
  would they cite?" — adversarial framing; surface their strongest arguments
- "What's the strongest counter to your #1 finding?" — self-rebuttal on item #1

# Auto-promote rule

If `runs.json.harness_findings.by_persona[<persona>][<failure_type>] > 3`
(more than half of the persona's 6 runs hit it), elevate that as a top-fix
item with high priority — it's a prompt-or-persona bug, not a game bug.

# Aborted-runs honesty rule

If `len(runs.json.harness_findings.aborted_runs) > 0.05 * total_runs`
(>5% aborted), lead the report (immediately under the boilerplate's
"## Header") with: "**WARNING:** This invocation had a high failure
rate (X of Y runs aborted). Findings below are exploratory; treat
all claims with reduced confidence."

# Final step

After writing the synthesis, verify the report:
- All claims have all four confidence-scaffolding fields
- Every quote is resolvable in `$ARGUMENTS/raw/*.md` (the audit trail)
- Setting-specific claims are framed as ratios, not absolutes
- The boilerplate above `<!-- META_AGGREGATOR_OUTPUT_BELOW -->` is untouched

Then write the synthesis to `$ARGUMENTS/report.md`, replacing only the
content below the marker. Do not add files; do not modify raw/*. The
synthesis IS the deliverable.
```

- [ ] **Step 3: Commit (or document if .claude is gitignored)**

```bash
# If .claude/ is committable:
git add .claude/commands/playtest-aggregate.md
git commit -m "$(cat <<'EOF'
feat(playtest): /playtest-aggregate slash command for meta-aggregator

Wraps the meta-aggregator subagent (Opus 4.7, max effort) with a fixed
prompt that reads runs.json + raw/*.md + SPEC.md and produces the
report.md synthesis below the MARKER_AGGREGATOR_BEGIN line. Confidence
scaffolding is mandatory on every claim; ordinal-ratio framing is
required for setting-specific findings; aborted-runs honesty rule
leads the report when >5% of runs aborted.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

If `.claude/` is gitignored, instead:
- Create the file at the alternate path (e.g. `docs/playtest-aggregator-prompt.md`)
- Add a one-line comment at the top: "Paste this into a Claude Code session to invoke the aggregator. Replace `$ARGUMENTS` with the run directory path."
- Adjust the commit message to `docs(playtest): aggregator prompt for manual invocation`

---

## Task 10: Real-LLM integration test (gated)

**Files:**
- Create: `backend/tests/eval/playtest/test_real_llm_smoke.py`

End-to-end test gated by `OUTPOST_PLAYTEST_REAL_LLM=1`. Runs ONE persona for ONE game (3 turns max) against the real Anthropic API + a real `claude --print` subprocess. Skipped by default — only the user runs it before launch.

- [ ] **Step 1: Write the gated real-LLM test**

Create `backend/tests/eval/playtest/test_real_llm_smoke.py`:

```python
"""Real-LLM smoke. Skipped unless OUTPOST_PLAYTEST_REAL_LLM=1 AND
ANTHROPIC_API_KEY is set. Runs one persona for one short game with the
actual claude CLI subprocess + actual Anthropic API."""

from __future__ import annotations

import os
import shutil
import uuid
from pathlib import Path

import pytest

from scripts.play_test import PlaytestConfig, run_harness

pytestmark = [pytest.mark.eval, pytest.mark.asyncio]


@pytest.mark.skipif(
    os.environ.get("OUTPOST_PLAYTEST_REAL_LLM") != "1",
    reason="OUTPOST_PLAYTEST_REAL_LLM not set; skipping real-LLM playtest",
)
async def test_real_llm_one_persona_one_game(
    eval_user_id, graph_postgres, tmp_path: Path
) -> None:
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
    runs = (out_dir / "raw").glob("*.json")
    assert any(runs), "expected at least one per-run JSON archive"
```

- [ ] **Step 2: Run the gated test once locally to confirm wiring**

Manually:

```bash
cd backend && OUTPOST_PLAYTEST_REAL_LLM=1 \
  DATABASE_URL="postgresql+psycopg://postgres:postgres@localhost:54322/postgres" \
  uv run pytest tests/eval/playtest/test_real_llm_smoke.py -v -m eval
```

Expected: PASS, ~30-60s wall clock, ~5-10¢ in API spend, one game persists in Postgres, output artifacts exist in `tmp_path`.

If this fails: the most common causes are (a) `claude` CLI not on PATH, (b) Postgres unreachable, (c) Anthropic 429 (rate limit — wait 60s and retry), (d) `--effort` flag not recognized (means your `claude` version is too old; upgrade with `npm i -g @anthropic-ai/claude-code` or equivalent).

- [ ] **Step 3: Commit (test only, no implementation)**

```bash
git add backend/tests/eval/playtest/test_real_llm_smoke.py
git commit -m "$(cat <<'EOF'
test(playtest): real-LLM smoke gated by OUTPOST_PLAYTEST_REAL_LLM

One persona × one short game (3 turns) against actual Anthropic API +
actual claude CLI subprocess. Skipped unless OUTPOST_PLAYTEST_REAL_LLM=1.
Used to verify wiring end-to-end before a real 42-run invocation.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Verification at end of plan

After all 10 tasks are done, run the full project test + lint to confirm the harness integrates without breaking anything:

```bash
cd backend && uv run pytest tests/ -v
cd backend && uv run pytest tests/eval/playtest/ -v -m eval
cd backend && uv run ruff check app/ tests/
cd backend && uv run ruff format --check app/ tests/
```

Expected: all default tests pass; eval-marked playtest tests pass when run with `-m eval` and Postgres is available; ruff is clean across the new files.

Add a new entry to `docs/PLAN.md` under the appropriate phase section noting that the playtest harness is implemented and pointing at the spec file.

---

## Self-review checklist completed

- ✓ Spec coverage: every spec section maps to a task. §0 honest-limits → Task 7 boilerplate; §2 architecture → Task 8; §3 personas → Task 2; §4 prompt structure → Task 3; §5 per-turn schema → Task 1; §6 run-verdict schema → Task 1; §7 multi-oracle → Task 6; §8 aggregator → Task 9; §9 outputs → Task 7; §10 failures → Task 4; §11 ops → Task 8.
- ✓ No placeholders. Every step has either a runnable command or a literal code block.
- ✓ Type consistency: `PersonaTurnOutput`, `PersonaRunVerdict`, `Persona`, `PerRunArchive`, `PoolPickResult`, `HarnessFinding`, `PlaytestConfig`, `LLMPlayer`, `PersonaSession`, `SubprocessPool` are referenced consistently across all tasks.
- ✓ Each task's commit is self-contained (test + code + lint check before commit).
- ✓ Bite-sized steps (2-5 min): write-test, run-test, implement, run-test, commit pattern.
