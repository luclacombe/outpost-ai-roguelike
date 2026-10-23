# Truth & Integrity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every number the player sees about an option is rendered by the engine from validated tool calls, every stat change is a phase-tagged persisted tool call, narration is guarded, the post-mortem shows real seeds, and titles and tics stop leaking.

**Architecture:** A pure `app/game/preview.py` dry-runs each option through the same engine path `resolve` uses and renders `effects_preview`. A new `app/agent/prepare.py` runs between the debate and the pause: it whitelists debate calls to `propose_action`, strips rejected nested calls, classifies and selects the three surfaced proposals, renders previews and sanitizes `risk_note`. Narration gets its own system block and a pure guard (`app/agent/narration_guard.py`). Two nullable JSONB columns on `turn_logs` (`tool_calls`, `turn_meta`) persist the full record; an audit script checks the exit gate.

**Tech Stack:** Python 3.12, FastAPI, LangGraph, Pydantic v2, SQLAlchemy async + Alembic, pytest + respx; Next.js 16 + TypeScript strict + Tailwind v4.

**Spec:** `docs/superpowers/specs/2026-10-07-truth-and-integrity-design.md` (commit `b78ddc9`). Read it before starting any task.

## Global Constraints

- Never touch the production database. All DB work uses `DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:54322/postgres`. Never run `alembic upgrade` against anything else.
- Never `git push`. Commit on `feat/truth-and-integrity` only, with explicit `git add <paths>` (never `git add -A`).
- Conventional Commits; every commit message ends with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Python: ruff 100-char lines, type hints everywhere, Pydantic v2 `@field_validator`, no new `@validator`.
- Engine purity (`backend/app/game/CLAUDE.md`): functions take `ColonyState`, return a new one; `model_copy(update=...)` is the only mutation idiom; no `.append()` on model fields.
- Every numeric stat change goes through `engine.apply_tool_call(s)` (invariant #11). No new side channels.
- Minus sign in rendered previews is U+2212 (`−`), gains carry an explicit `+`.
- `effects_preview` is engine-written only. The LLM never supplies it.
- `risk_note` ≤ 120 chars, no digits, no `legitimacy`/`health`/`grievance`, no other setting's resource names.
- `player_title`: arctic `Director`, orbital `Director`, desert `Warden`.
- `EventCategory.title` ≤ 48 chars, present on every shipped event.
- Prompt caching stays on: every system block keeps `cache_control: {"type": "ephemeral"}`.
- Gates every Python task ends with, from `backend/`:
  - `DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:54322/postgres uv run pytest tests/ -q`
  - `uv run ruff check app/ tests/ scripts/`
  - `uv run ruff format --check app/ tests/ scripts/`
  Run both ruff commands yourself; do not trust a "ruff clean" claim. Code blocks in this plan are not pre-formatted: run `uv run ruff format <changed files>` before the `--check`.
- User-facing strings (titles, tics, fallback lines, prompt prose) follow `docs/SPEC.md` §14: short lines, concrete nouns, no softeners, no em-dash overuse.

## Review Focus

1. **A proposal whose every nested call is stripped, in a turn where fewer than three proposals survive at all.** Expected: the turn still pauses with however many proposals exist (1–3), each previewed honestly (`no immediate effect`), never a crash or an empty `effects_preview`. Test: Task 5, `test_prepare_handles_fewer_than_three_proposals`.
2. **Overrule cost at the legitimacy floor.** Legitimacy 1, MORAL picked over a living DOMINANT proposer: the −2 clamps to −1. Expected preview `legitimacy −1 (overrules <name>)` and the resolve record's `applied_delta == -1`. Test: Task 3, `test_render_legitimacy_partial_clamp_at_floor`.
3. **Narration that is entirely non-Latin (the Russian prompt echo).** Expected: hard `script` violation, retry, and fallback line if the retry is also non-Latin. Test: Task 4 `test_script_violation_on_cyrillic` and Task 8 `test_narrate_hard_violation_twice_falls_back`.
4. **Old shared post-mortem links whose `state.consequence_seeds` hold a malformed entry.** Expected: the post-mortem returns 200, skips that seed, logs a warning. Test: Task 9, `test_post_mortem_skips_malformed_seed`.
5. **A game paused under the old code and resumed under the new code** (its checkpoint `proposals` carry LLM previews and no `turn_meta`). Expected: `resolve` and `advance_turn` still work; `turn_meta` persists as `{}`-derived dict, never a crash. Test: Task 7, `test_advance_turn_without_turn_meta`.

## Deliberate departures from the spec

Small, each argued where it appears:
- `render_close()` takes no argument (the line doesn't vary by setting).
- `validate_narration(text, allowed_terms, roster_names)` takes the roster instead of the setting (the guard needs names, not the setting).
- `empty` is an extra hard violation, so a blank reply is never kept.
- `turn_meta.pre_pick` (a stats-only state dump) is added beyond spec §4.4's key list: the audit needs the pre-pick state to rebuild the outcome from recorded calls.
- `engine.py` Section 7 moves to `resolution.py` (re-exported): `backend/app/game/CLAUDE.md` asks for that split once `engine.py` passes ~600 lines, which this work would cause.
- Preparation records kept nested calls as `phase="prepare"` validation records (`applied_delta=None`), so `tool_validity_rate` has a denominator for nested calls, as spec §4.4 requires.

---

## File Structure

| File | Responsibility | Task |
|---|---|---|
| `backend/app/game/setting_loader.py` | `Setting.player_title`, `Setting.lexicon`, `EventCategory.title`, tic validation | 1 |
| `backend/app/game/settings/*.yml` | titles, player titles, lexicons, rewritten tics | 1 |
| `backend/app/game/events.py` | authored title first, `_derive_title` never cuts mid-word | 1 |
| `backend/app/game/schemas.py` | `ToolCall.applied_delta`, `ToolCall.phase`, `Proposal.risk_note` | 2 |
| `backend/app/game/resolution.py` (new) | `derive_resolution_consequences` (moved out of engine), per-rule legitimacy | 2 |
| `backend/app/game/engine.py` | `applied_delta`, `stamp_phase`, `record_rejected`, `close_turn`, no decay in `advance_turn` | 2 |
| `backend/app/game/validator.py` | overflow allowed, `strip_scaffold_prefix` | 2 |
| `backend/app/game/preview.py` (new) | `simulate_option`, `outcome_from_calls`, `render_effects`, `render_close`, compact state | 3 |
| `backend/app/eval/choice_spread.py` | uses `preview.simulate_option` | 3 |
| `backend/app/agent/narration_guard.py` (new) | `validate_narration`, `build_allowed_terms`, `build_strict_terms`, `sanitize_risk_note` | 4 |
| `backend/app/agent/prepare.py` (new) | `prepare_proposals`, `DEBATE_TOOL_NAMES`, `PreparedTurn` | 5 |
| `backend/app/agent/nodes.py`, `state.py` | wiring: prepare, phases, `turn_meta` (6); persistence (7); narration (8) | 6–8 |
| `backend/app/game/tools.py`, `backend/app/agent/prompts.py` | schema `risk_note`, prompt fixes | 6 |
| `backend/app/game/models.py`, `backend/alembic/versions/a7c3e9d41f20_*.py` | `turn_logs.tool_calls`, `turn_logs.turn_meta` | 7 |
| `backend/app/eval/tool_validity.py`, `backend/tests/fixtures/run_simulator.py` | LLM-phase validity, read new column | 7 |
| `backend/app/agent/narration.py` (new) | narration system block + user message builders | 8 |
| `backend/app/game/post_mortem.py` | seeds from state | 9 |
| `backend/app/game/schemas_io.py`, `router.py`, `backend/app/agent/service.py` | `risk_note`, `close_preview` | 10 |
| `frontend/lib/api/client.ts`, `frontend/components/dashboard/{ChoosePanel,DashboardShell}.tsx`, `frontend/lib/dev/fixtures.ts` | render `risk_note` + `close_preview` | 10 |
| `backend/app/eval/playtest/{turn_view,turn_message}.py` | persona sees the same | 10 |
| `backend/scripts/audit_integrity.py` (new) | exit-gate audit | 11 |
| CLAUDE.md files, `docs/PLAN.md`, axis spec note | docs, eval gate, exit run | 12 |

---

### Task 0: Worktree environment (no commit)

The worktree is a fresh checkout: it has no `backend/.env` (gitignored) and no `frontend/node_modules`, so nothing imports yet.

- [ ] **Step 1: Link the env file and install dependencies**

```bash
cd /Users/luclacombe/Documents/Outpost/.claude/worktrees/truth-and-integrity
ln -s /Users/luclacombe/Documents/Outpost/backend/.env backend/.env
git check-ignore -q backend/.env && echo ignored
cd backend && uv sync
cd ../frontend && pnpm install --frozen-lockfile
```

Expected: `ignored` is printed (the link is never committed). The `.env` points at the remote database; that is why every DB command in this plan sets `DATABASE_URL` to localhost explicitly, and why `tests/_settings_isolation.py` redirects remote URLs.

- [ ] **Step 2: Confirm local Supabase and a green baseline**

```bash
cd backend
supabase status || supabase start
DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:54322/postgres uv run pytest tests/ -q
uv run ruff check app/ tests/ scripts/
uv run ruff format --check app/ tests/ scripts/
```

Expected: all pass on the untouched branch. If the baseline is red, stop and report which tests fail before changing anything.

---

### Task 1: Setting content: titles, player title, lexicon, tics

**Files:**
- Modify: `backend/app/game/setting_loader.py` (`AdvisorArchetype`, `EventCategory`, `Setting`)
- Modify: `backend/app/game/settings/arctic_station_v1.yml`, `desert_outpost_v1.yml`, `orbital_colony_v1.yml`
- Modify: `backend/app/game/events.py:215-241` (`_build_event`, `_derive_title`)
- Modify: `backend/tests/test_events.py:126`, `backend/tests/test_difficulty.py:101` (inline `Setting(...)` builders gain `player_title`)
- Test: `backend/tests/test_setting_loader.py`, `backend/tests/test_events.py`

**Interfaces:**
- Produces: `Setting.player_title: str`, `Setting.lexicon: list[str]`, `EventCategory.title: str | None`; `events._derive_title(prompt_template: str) -> str` (≤ 48 chars, never mid-word).

- [ ] **Step 1: Write the failing loader tests** (append to `backend/tests/test_setting_loader.py`)

```python
import re

import pytest
from pydantic import ValidationError

from app.game.setting_loader import AdvisorArchetype, list_available_settings, load_setting

_EXPECTED_TITLES = {
    "arctic_station_v1": "Director",
    "desert_outpost_v1": "Warden",
    "orbital_colony_v1": "Director",
}


@pytest.mark.parametrize("setting_id", sorted(_EXPECTED_TITLES))
def test_player_title_per_setting(setting_id: str) -> None:
    assert load_setting(setting_id).player_title == _EXPECTED_TITLES[setting_id]


@pytest.mark.parametrize("setting_id", sorted(_EXPECTED_TITLES))
def test_every_shipped_event_has_short_title(setting_id: str) -> None:
    setting = load_setting(setting_id)
    for event in setting.event_categories:
        assert event.title, f"{setting_id}:{event.id} has no title"
        assert len(event.title) <= 48, f"{setting_id}:{event.id} title too long"


@pytest.mark.parametrize("setting_id", sorted(_EXPECTED_TITLES))
def test_lexicon_is_authored(setting_id: str) -> None:
    assert load_setting(setting_id).lexicon


def test_player_title_never_collides_with_a_role() -> None:
    for sid in list_available_settings():
        s = load_setting(sid)
        roles = {a.role_name for a in s.advisor_archetypes}
        assert s.player_title not in roles, sid


@pytest.mark.parametrize("setting_id", sorted(_EXPECTED_TITLES))
def test_shipped_tics_have_no_digits_or_gendered_pronouns(setting_id: str) -> None:
    for a in load_setting(setting_id).advisor_archetypes:
        assert not re.search(r"\d", a.verbal_tic), a.role_name
        assert not re.search(r"\b(he|him|his|she|her)\b", a.verbal_tic, re.I), a.role_name


def _archetype(tic: str) -> dict:
    return {
        "quadrant": "ACCOUNTANT",
        "role_name": "Clerk",
        "name_pool": ["A", "B", "C"],
        "description_template": "x",
        "verbal_tic": tic,
        "portrait_options": {},
    }


@pytest.mark.parametrize(
    "tic",
    [
        "Opens with MET 14:07:32 when nervous.",
        "Uses his last name when angry.",
        "She hums when tired.",
    ],
)
def test_tic_validator_rejects_digits_and_pronouns(tic: str) -> None:
    with pytest.raises(ValidationError):
        AdvisorArchetype.model_validate(_archetype(tic))


def test_tic_validator_allows_words_containing_pronoun_letters() -> None:
    # "other", "the", "whether", "theirs" contain he/her as substrings only.
    AdvisorArchetype.model_validate(
        _archetype("Asks whether the other crew agree before acting on theirs.")
    )
```

- [ ] **Step 2: Write the failing title tests** (append to `backend/tests/test_events.py`)

```python
from app.game.events import _derive_title


def test_derive_title_keeps_short_first_sentence() -> None:
    assert _derive_title("A blizzard rolls in. Visibility drops.") == "A blizzard rolls in"


def test_derive_title_never_cuts_mid_word() -> None:
    long = (
        "A coded message arrives addressed to the commissar by his old rank from the "
        "ministry. More text."
    )
    title = _derive_title(long)
    assert len(title) <= 48
    assert title.endswith("…")
    body_words = long.split(".")[0].split()
    assert title[:-1].split() == body_words[: len(title[:-1].split())]


def test_build_event_prefers_authored_title() -> None:
    import random

    from app.game.events import _build_event
    from app.game.schemas import EventOrigin
    from app.game.setting_loader import load_setting

    category = load_setting("arctic_station_v1").event_categories[0]
    event = _build_event(category, random.Random(1), EventOrigin.NATURAL)
    assert event.title == category.title
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_setting_loader.py tests/test_events.py -q`
Expected: FAIL (`player_title`, `lexicon`, `title` attributes missing; `_derive_title` returns a 60-char cut).

- [ ] **Step 4: Implement the schema fields** in `backend/app/game/setting_loader.py`

Add near the other regexes:

```python
_TIC_PRONOUN = re.compile(r"\b(he|him|his|she|her)\b", re.IGNORECASE)
```

In `AdvisorArchetype`, add a validator after `_verbal_tic_is_single_sentence`:

```python
    @field_validator("verbal_tic")
    @classmethod
    def _verbal_tic_has_no_literals(cls, v: str) -> str:
        # Tics are per role; names (and so pronouns) are drawn per run, and a
        # literal timestamp gets copied verbatim into every line.
        if re.search(r"\d", v):
            raise ValueError("verbal_tic must not contain digits")
        if _TIC_PRONOUN.search(v):
            raise ValueError("verbal_tic must not contain gendered pronouns")
        return v
```

In `EventCategory`, add the field after `can_kill_agent`:

```python
    title: Annotated[str, Field(min_length=1, max_length=48)] | None = None
```

In `Setting`, add after `narrative_voice`:

```python
    player_title: Annotated[str, Field(min_length=1, max_length=24)]
    lexicon: list[str] = []
```

- [ ] **Step 5: Implement title derivation** in `backend/app/game/events.py`

Replace `_build_event`'s title line and `_derive_title`:

```python
    title = category.title or _derive_title(category.prompt_template)
```

```python
_TITLE_MAX = 48


def _derive_title(prompt_template: str) -> str:
    """Fallback title for an event without an authored `title`.

    First sentence if it fits in 48 chars; otherwise cut at the last space
    before char 47 and append "…". Never cuts mid-word.
    """
    first_sentence = prompt_template.strip().split(".")[0].strip()
    if not first_sentence:
        return "Event"
    if len(first_sentence) <= _TITLE_MAX:
        return first_sentence
    cut = first_sentence[: _TITLE_MAX - 1]
    space = cut.rfind(" ")
    if space > 0:
        cut = cut[:space]
    return cut.rstrip(" ,;:—-") + "…"
```

- [ ] **Step 6: Author the YAML content**

In each setting YAML, add `player_title` and `lexicon` directly under `narrative_voice`, and a `title:` line to every event category (directly under its `id:`). Use exactly these values. Before saving, read each event's `prompt_template`; if a title contradicts the body, reword it (≤ 48 chars, no digits needed) and note it in the commit body.

`arctic_station_v1.yml`:

```yaml
player_title: Director
lexicon: [Vorgrad]
```

| event id | title |
|---|---|
| weather_blizzard | Blizzard from the northeast |
| equipment_failure | Reclamation pump seized |
| radio_contact | Signal on the emergency band |
| ice_quake | Tremor under the ice |
| frozen_stranger | Stranger at the outer door |
| soviet_archive_find | Sealed box in the sub-basement |
| infection_outbreak | Fever in three bunks |
| fuel_leak | Slow leak in the tank line |
| foreign_patrol | Patrol at the perimeter |
| internal_feud | Two days of silence |
| hunting_party_returns | Supply run comes back short |
| generator_overload | Generator running hot |
| forced_escalation_vorgrad | Coded message for the commissar |

`desert_outpost_v1.yml`:

```yaml
player_title: Warden
lexicon: [Carmine, Junction, Doc]
```

| event id | title |
|---|---|
| raider_probe | Three vehicles on the highway |
| desperate_stranger | A woman and a child, on foot |
| well_integrity | Crack in the well casing |
| road_medical_emergency | Injured man in a truck bed |
| old_world_salvage | Warehouse three miles east |
| water_trade_offer | Caravan wants water |
| shelter_overcrowding | Junction at capacity |
| fever_road | Fever off the road |
| moral_test_refugee | Eight people asking for shelter |
| mechanic_repair_gamble | One pump, one chance |
| junction_politics | Residents want rules |
| night_visitor | Someone in the storage room |
| forced_escalation_carmine | The vehicles are back |

`orbital_colony_v1.yml`:

```yaml
player_title: Director
lexicon: [Helios, HELO, MET, Earth]
```

| event id | title |
|---|---|
| oxygen_recycler_fault | Recycler unit three anomaly |
| radiation_burst | Solar particle event |
| earth_signal_ambiguous | Surface signal changes pattern |
| thruster_misalignment | Attitude drift past tolerance |
| helo_behavioral_drift | HELO writing in the crew file |
| water_recycler_contamination | Contaminant in the water |
| garden_bay_disease | Blight in the bean trays |
| resupply_salvage_opportunity | Unscheduled cargo module |
| crew_isolation_break | Botanist skipping meals |
| other_station_contact | Helios-4 asks for help |
| power_cascade_failure | Power bus cascade |
| crew_faction_burn_debate | Motion to burn thruster fuel |
| forced_escalation_helios | HELO's optimization analysis |

Rewrite these two tics verbatim:
- orbital `Flight Engineer`: `Sometimes opens with the current station time read off the board, when precision steadies them.`
- desert `Sheriff`: `Addresses people by role (Doc, Trader) rather than first name; a first name means something is wrong.`

The other ten tics already pass the digit and pronoun checks (verify with the test, do not reword them).

- [ ] **Step 7: Fix inline test settings**

In `backend/tests/test_events.py:126` and `backend/tests/test_difficulty.py:101`, every inline `Setting(...)` / `Setting.model_validate({...})` gains `player_title="Director"`. Find them all with `grep -rn "Setting(" backend/tests | grep -v load_setting`.

- [ ] **Step 8: Run the tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_setting_loader.py tests/test_events.py tests/test_difficulty.py -q`
Expected: PASS

- [ ] **Step 9: Run the full gates** (see Global Constraints), then commit

```bash
git add backend/app/game/setting_loader.py backend/app/game/events.py backend/app/game/settings/*.yml backend/tests/test_setting_loader.py backend/tests/test_events.py backend/tests/test_difficulty.py
git commit -m "feat(settings): authored event titles, player titles, lexicons, literal-free tics

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Engine: applied deltas, phases, per-rule legitimacy, overflow clamp, close_turn

**Files:**
- Modify: `backend/app/game/schemas.py` (`ToolCall`, `Proposal`)
- Create: `backend/app/game/resolution.py` (Section 7 of `engine.py` moves here)
- Modify: `backend/app/game/engine.py` (imports, `apply_tool_call`, `_effect_propose_action`, `advance_turn`, new `close_turn`/`stamp_phase`/`record_rejected`)
- Modify: `backend/app/game/validator.py` (`_validate_propose_action`, `_validate_modify_resource`, new `strip_scaffold_prefix`)
- Modify: `backend/app/agent/nodes.py::advance_turn` (call `engine.close_turn` before `engine.advance_turn`)
- Modify: `backend/tests/test_engine.py` (decay tests, reason assertions), `backend/tests/test_validator.py:168-173`, `backend/tests/test_eval_choice_spread.py` (overflow cases)
- Test: `backend/tests/test_engine.py`, `backend/tests/test_validator.py`, `backend/tests/test_resolution.py` (new)

**Interfaces:**
- Produces:
  - `schemas.ToolCallPhase = Literal["debate", "prepare", "resolve", "autonomy", "close"]`
  - `ToolCall.applied_delta: int | None = None`, `ToolCall.phase: ToolCallPhase | None = None`
  - `Proposal.risk_note: str | None = None`
  - `engine.stamp_phase(calls: list[ToolCall], phase: ToolCallPhase) -> list[ToolCall]`
  - `engine.record_rejected(state: ColonyState, tc: ToolCall, reason: str) -> ColonyState`
  - `engine.close_turn(state: ColonyState) -> ColonyState`; `engine.CLOSE_LEGITIMACY_DELTA = -1`; `engine.CLOSE_REASON = "end of turn"`
  - `engine.advance_turn` no longer decays
  - `resolution.derive_resolution_consequences(state, picked, all_proposals) -> list[ToolCall]` (re-exported as `engine.derive_resolution_consequences`)
  - `resolution.OVERRULE_GRIEVANCE_REASON = "their dominant proposal was overruled"`
  - `validator.strip_scaffold_prefix(name: str) -> str`

- [ ] **Step 1: Write the failing engine tests** (append to `backend/tests/test_engine.py`; update the imports block to add `close_turn, record_rejected, stamp_phase`)

```python
def test_applied_delta_records_full_change() -> None:
    state = _colony(resources={"fuel": _resource(value=50, max_=100)})
    out = apply_tool_call(state, _tc("modify_resource", {"resource_id": "fuel", "delta": -10, "reason": "r"}))
    assert out.turn_tool_calls[-1].applied_delta == -10


def test_overflow_clamps_and_records_applied_delta() -> None:
    state = _colony(resources={"fuel": _resource(value=95, max_=100)})
    out = apply_tool_call(state, _tc("modify_resource", {"resource_id": "fuel", "delta": 20, "reason": "r"}))
    assert out.resources["fuel"].value == 100
    last = out.turn_tool_calls[-1]
    assert last.rejection_reason is None
    assert last.applied_delta == 5


def test_applied_delta_for_legitimacy_health_grievance() -> None:
    a = _agent(name="Jaya", health=10, grievance=9)
    state = _colony(agents=[a], legitimacy=1)
    calls = [
        _tc("modify_legitimacy", {"delta": -2, "reason": "r"}),
        _tc("modify_health", {"target_id": str(a.id), "delta": -40, "reason": "r"}),
        _tc("adjust_grievance", {"target_id": str(a.id), "delta": 3, "reason": "r"}),
    ]
    out = apply_tool_calls(state, calls)
    assert [tc.applied_delta for tc in out.turn_tool_calls] == [-1, -10, 1]


def test_applied_delta_none_for_rejected_and_non_numeric() -> None:
    state = _colony()
    out = apply_tool_call(state, _tc("modify_resource", {"resource_id": "nope", "delta": 1, "reason": "r"}))
    assert out.turn_tool_calls[-1].applied_delta is None


def test_stamp_phase_does_not_mutate_input() -> None:
    calls = [_tc("modify_legitimacy", {"delta": -1, "reason": "r"})]
    stamped = stamp_phase(calls, "resolve")
    assert stamped[0].phase == "resolve"
    assert calls[0].phase is None


def test_record_rejected_logs_without_applying() -> None:
    state = _colony(resources={"fuel": _resource(value=50)})
    tc = _tc("modify_resource", {"resource_id": "fuel", "delta": -5, "reason": "r"})
    out = record_rejected(state, tc, "not_allowed_in_debate")
    assert out.resources["fuel"].value == 50
    assert out.turn_tool_calls[-1].rejection_reason == "not_allowed_in_debate"


def test_close_turn_emits_validated_decay_call() -> None:
    state = _colony(legitimacy=40)
    out = close_turn(state)
    assert out.legitimacy == 39
    last = out.turn_tool_calls[-1]
    assert (last.name, last.phase, last.caller_id) == ("modify_legitimacy", "close", None)
    assert last.args == {"delta": -1, "reason": "end of turn"}
    assert last.applied_delta == -1


def test_close_turn_runs_even_when_run_has_ended() -> None:
    state = _colony(legitimacy=40).model_copy(update={"end_state": EndState.OUSTED})
    assert close_turn(state).legitimacy == 39


def test_close_turn_at_zero_records_zero_applied() -> None:
    out = close_turn(_colony(legitimacy=0))
    assert out.legitimacy == 0
    assert out.turn_tool_calls[-1].applied_delta == 0
```

Replace the two existing decay tests (`test_advance_turn_decrements_legitimacy`, `test_advance_turn_legitimacy_does_not_go_below_zero`) with:

```python
def test_advance_turn_no_longer_decays() -> None:
    state = _colony(legitimacy=50)
    assert advance_turn(state).legitimacy == 50
```

In `test_derive_severe_health_reason_handles_player_target` (line ~1126) change the assertion to `assert legit.args["reason"] == "severe harm to you"`.

Add `EndState` to the `app.game.schemas` import list if not already imported.

- [ ] **Step 2: Write the failing resolution tests** (`backend/tests/test_resolution.py`)

```python
"""Per-rule legitimacy consequences (spec §4.1)."""

from __future__ import annotations

from app.game.resolution import OVERRULE_GRIEVANCE_REASON, derive_resolution_consequences
from app.game.schemas import Proposal, ProposalFlavor, ToolCall
from tests._factories import _agent, _colony, _resource


def _proposal(pid: str, flavor: ProposalFlavor, calls: list[ToolCall], by=None) -> Proposal:
    return Proposal(
        id=pid,
        proposed_by_agent_id=by,
        flavor=flavor,
        name=pid,
        description="d",
        effects_preview=[],
        tool_calls_on_pick=calls,
    )


def _health(target: str, delta: int) -> ToolCall:
    return ToolCall(name="modify_health", caller_id=None, args={"target_id": target, "delta": delta, "reason": "x"})


def test_one_legitimacy_call_per_triggered_rule_with_named_reasons() -> None:
    astrid = _agent(name="Astrid")
    jaya = _agent(name="Jaya")
    state = _colony(agents=[astrid, jaya, _agent(name="C")], resources={"fuel": _resource(max_=100)})
    dominant = _proposal("d", ProposalFlavor.DOMINANT, [], by=astrid.id)
    picked = _proposal(
        "m",
        ProposalFlavor.MORAL,
        [
            _health(str(jaya.id), -60),
            ToolCall(name="modify_resource", caller_id=None, args={"resource_id": "fuel", "delta": -40, "reason": "x"}),
        ],
    )
    derived = derive_resolution_consequences(state, picked, [dominant, picked])
    legit = [(tc.args["delta"], tc.args["reason"]) for tc in derived if tc.name == "modify_legitimacy"]
    assert legit == [
        (-12, "severe harm to Jaya"),
        (-5, "heavy fuel drain"),
        (-2, "overrules Astrid"),
    ]
    grievance = [tc for tc in derived if tc.name == "adjust_grievance"]
    assert grievance[0].args["reason"] == OVERRULE_GRIEVANCE_REASON


def test_player_target_reads_you() -> None:
    picked = _proposal("w", ProposalFlavor.WILDCARD, [_health("player", -50)])
    derived = derive_resolution_consequences(_colony(), picked, [picked])
    assert derived[0].args["reason"] == "severe harm to you"


def test_no_rule_no_calls() -> None:
    picked = _proposal("w", ProposalFlavor.WILDCARD, [])
    assert derive_resolution_consequences(_colony(), picked, [picked]) == []
```

- [ ] **Step 3: Write the failing validator tests** (append to `backend/tests/test_validator.py`; replace `test_modify_resource_would_overflow` at line 168)

```python
def test_modify_resource_overflow_is_valid_now() -> None:
    # Overflow clamps in the effect (engine._effect_modify_resource); spec §4.1.
    state = _colony(resources={"fuel": _resource(value=95, max_=100)})
    tc = ToolCall(name="modify_resource", caller_id=None, args={"resource_id": "fuel", "delta": 20, "reason": "r"})
    assert validate_tool_call(tc, state).valid


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("DOMINANT: Burn it", "Burn it"),
        ("Rank flavor: Hold", "Hold"),
        ("  wildcard :  Jump", "Jump"),
        ("Moral high ground", "Moral high ground"),
    ],
)
def test_strip_scaffold_prefix(raw: str, expected: str) -> None:
    assert strip_scaffold_prefix(raw) == expected


def test_duplicate_check_compares_stripped_names() -> None:
    from app.game.schemas import Proposal, ProposalFlavor

    existing = Proposal(
        id="x", proposed_by_agent_id=None, flavor=ProposalFlavor.DOMINANT, name="Burn it",
        description="d", effects_preview=[], tool_calls_on_pick=[],
    )
    state = _colony().model_copy(update={"turn_proposals": [existing]})
    tc = ToolCall(
        name="propose_action", caller_id=None,
        args={"name": "DOMINANT: Burn it", "description": "d", "flavor": "MORAL"},
    )
    assert validate_tool_call(tc, state).reason == "duplicate_proposal"


def test_name_that_is_only_a_prefix_is_missing() -> None:
    tc = ToolCall(name="propose_action", caller_id=None, args={"name": "MORAL:", "description": "d", "flavor": "MORAL"})
    assert validate_tool_call(tc, _colony()).reason == "missing_name"
```

Add `strip_scaffold_prefix` to the `app.game.validator` import in that file.

- [ ] **Step 4: Update the choice-spread overflow test** (`backend/tests/test_eval_choice_spread.py`)

Overflow is now a clamp, not a reject. Change the fixture comment at line 28 to `# Fuel one below max so a +5 clamps to +1.`, the `_triple` comment at line 62 to `# Overflow clamps at max.`, and replace the WILDCARD assertions in `test_options_report_engine_deltas`:

```python
    wld = by_id["p_wld"]
    assert wld["resource_deltas"]["fuel"] == 1
    assert wld["rejected_calls"] == []
    assert wld["n_calls"] >= 1
```

Run `grep -n "would_overflow" -r backend/tests backend/app` and update every remaining test expectation the same way (a clamp, no rejection). `tests/eval/playtest/test_summaries.py:98` counts rejects: re-read it and adjust its expected counts to match clamping.

- [ ] **Step 5: Run the tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_engine.py tests/test_resolution.py tests/test_validator.py tests/test_eval_choice_spread.py -q`
Expected: FAIL (import errors for `close_turn`, `resolution`, `strip_scaffold_prefix`; overflow still rejected).

- [ ] **Step 6: Add the schema fields** (`backend/app/game/schemas.py`)

Add `Literal` to the `typing` import. Above `class ToolCall`:

```python
ToolCallPhase = Literal["debate", "prepare", "resolve", "autonomy", "close"]
```

In `ToolCall`, after `rejection_reason`:

```python
    # What the engine actually changed (after clamping); None for rejected or
    # non-numeric calls. Set by engine.apply_tool_call.
    applied_delta: int | None = None
    # Which node produced the call; None on calls recorded before 2026-10.
    phase: ToolCallPhase | None = None
```

In `Proposal`, after `tool_calls_on_pick`:

```python
    risk_note: str | None = None  # engine-sanitized qualitative hint; never numbers
```

Change the `effects_preview` comment to `# engine-rendered by app.game.preview; never LLM text`.

- [ ] **Step 7: Create `backend/app/game/resolution.py`**

Move Section 7 of `engine.py` (the rule constants, `_resolve_target_name`, `derive_resolution_consequences`, and its comment block) into this new module, then apply the per-rule changes. Full file:

```python
"""Engine-derived consequences of a pick (formerly engine.py Section 7).

Pure. Emits validated tool calls (caller_id=None); `nodes.resolve` and
`app.game.preview.simulate_option` fold them through engine.apply_tool_calls.
One modify_legitimacy call per triggered rule so the preview can show each
part (spec §4.1). Does not import engine (engine re-exports this function).
"""

from __future__ import annotations

from uuid import UUID

from app.game.schemas import Agent, ColonyState, Proposal, ProposalFlavor, ToolCall

# Rule constants — taste-tunable, in band with SPEC §5.3 ranges (small
# orders 1-2, medium 5-8, extreme 15-25). Each rule fires ONCE PER
# QUALIFYING NESTED CALL — a bigger atrocity should hurt more.
# Tuning signals:
#   _LEGIT_PER_50_HEALTH_DRAIN: raise toward -15 if severe-health proposals
#     don't visibly hurt the dashboard in playtests.
#   _LEGIT_PER_30PCT_RESOURCE_DRAIN: lower toward -3 if mid-game resource
#     reshuffles get penalised more than they should.
#   _LEGIT_OVERRULE_DOMINANT: lower toward -1 if the player feels punished
#     for non-dominant picks too often.
#   _GRIEVANCE_OVERRULE_DOMINANT: raise toward 3 if dominant-proposer coups
#     never trigger; lower toward 1 if they trigger too easily.
_LEGIT_PER_50_HEALTH_DRAIN = -12
_LEGIT_PER_30PCT_RESOURCE_DRAIN = -5
_LEGIT_OVERRULE_DOMINANT = -2
_GRIEVANCE_OVERRULE_DOMINANT = 2

OVERRULE_GRIEVANCE_REASON = "their dominant proposal was overruled"


def _agent(state: ColonyState, agent_id: UUID) -> Agent | None:
    return next((a for a in state.agents if a.id == agent_id), None)


def _resolve_target_name(state: ColonyState, target_id: object) -> str:
    """UUID → agent name, "player" → "you", anything else → "an advisor"."""
    if target_id == "player":
        return "you"
    if not isinstance(target_id, str):
        return "an advisor"
    try:
        agent = _agent(state, UUID(target_id))
    except ValueError:
        return "an advisor"
    return agent.name if agent is not None else "an advisor"


def _legitimacy(delta: int, reason: str) -> ToolCall:
    return ToolCall(
        name="modify_legitimacy", caller_id=None, args={"delta": delta, "reason": reason}
    )


def derive_resolution_consequences(
    state: ColonyState,
    picked: Proposal,
    all_proposals: list[Proposal],
) -> list[ToolCall]:
    """Pure: the engine's consequences of picking `picked` from `all_proposals`.

    Order: rule 1 (severe health) and rule 2 (heavy resource drain) per nested
    call in proposal order, then rule 3 (overrule) per DOMINANT proposer —
    its grievance call first, then its legitimacy call.
    """
    derived: list[ToolCall] = []

    for nested in picked.tool_calls_on_pick:
        if nested.name == "modify_health":
            delta = nested.args.get("delta", 0)
            if isinstance(delta, int) and delta <= -50:
                target = _resolve_target_name(state, nested.args.get("target_id"))
                derived.append(_legitimacy(_LEGIT_PER_50_HEALTH_DRAIN, f"severe harm to {target}"))

    for nested in picked.tool_calls_on_pick:
        if nested.name == "modify_resource":
            rid = nested.args.get("resource_id")
            delta = nested.args.get("delta", 0)
            res = state.resources.get(rid) if isinstance(rid, str) else None
            if (
                res is not None
                and isinstance(delta, int)
                and delta < 0
                and abs(delta) >= 0.30 * res.max
            ):
                derived.append(_legitimacy(_LEGIT_PER_30PCT_RESOURCE_DRAIN, f"heavy {rid} drain"))

    if picked.flavor != ProposalFlavor.DOMINANT:
        for prop in all_proposals:
            if prop.flavor != ProposalFlavor.DOMINANT or prop.proposed_by_agent_id is None:
                continue
            proposer = _agent(state, prop.proposed_by_agent_id)
            if proposer is None or not proposer.is_alive:
                continue
            derived.append(
                ToolCall(
                    name="adjust_grievance",
                    caller_id=None,
                    args={
                        "target_id": str(proposer.id),
                        "delta": _GRIEVANCE_OVERRULE_DOMINANT,
                        "reason": OVERRULE_GRIEVANCE_REASON,
                    },
                )
            )
            derived.append(_legitimacy(_LEGIT_OVERRULE_DOMINANT, f"overrules {proposer.name}"))

    return derived
```

Note: rule 2's reason still uses the resource id (`heavy fuel drain`); `render_effects` shows reasons as written.

- [ ] **Step 8: Rewire `engine.py`**

1. Delete Section 7 from `engine.py` and add, after the schema imports:

```python
from app.game.resolution import derive_resolution_consequences  # noqa: F401  (re-export)
```

2. Add `ToolCallPhase` to the schema import list. Add `from app.game.validator import strip_scaffold_prefix, validate_tool_call` (replacing the single import).

3. Replace `apply_tool_call` and add helpers in Section 2:

```python
def apply_tool_call(state: ColonyState, tc: ToolCall) -> ColonyState:
    """Validate tc against state; apply if valid, stamp rejection if not.

    Valid calls get `applied_delta` (what actually changed after clamping)
    for numeric tools. Always returns a new ColonyState.
    """
    result = validate_tool_call(tc, state)

    if not result.valid:
        rejected_tc = tc.model_copy(update={"rejection_reason": result.reason})
        return state.model_copy(update={"turn_tool_calls": [*state.turn_tool_calls, rejected_tc]})

    new_state = _apply_valid_effect(state, tc)
    logged = tc.model_copy(update={"applied_delta": _applied_delta(state, new_state, tc)})
    return new_state.model_copy(update={"turn_tool_calls": [*new_state.turn_tool_calls, logged]})


def _applied_delta(before: ColonyState, after: ColonyState, tc: ToolCall) -> int | None:
    """Post-clamp change for numeric tools; None for everything else."""
    name, args = tc.name, tc.args
    if name == "modify_resource":
        rid = args["resource_id"]
        return after.resources[rid].value - before.resources[rid].value
    if name in ("modify_legitimacy", "back_leader"):
        return after.legitimacy - before.legitimacy
    if name == "modify_health":
        if args["target_id"] == "player":
            return after.player_health - before.player_health
        return _health_of(after, args["target_id"]) - _health_of(before, args["target_id"])
    if name in ("adjust_grievance", "adjust_own_grievance"):
        target = args["target_id"] if name == "adjust_grievance" else str(tc.caller_id)
        return _grievance_of(after, target) - _grievance_of(before, target)
    if name == "adjust_relationship":
        assert tc.caller_id is not None
        target = UUID(args["target_id"])
        b = _agent_by_id(before, tc.caller_id)
        a = _agent_by_id(after, tc.caller_id)
        assert a is not None and b is not None
        return a.relationships.get(target, 0) - b.relationships.get(target, 0)
    return None


def _health_of(state: ColonyState, agent_id: str) -> int:
    agent = _agent_by_id(state, UUID(agent_id))
    assert agent is not None
    return agent.health


def _grievance_of(state: ColonyState, agent_id: str) -> int:
    agent = _agent_by_id(state, UUID(agent_id))
    assert agent is not None
    return agent.grievance


def stamp_phase(calls: list[ToolCall], phase: ToolCallPhase) -> list[ToolCall]:
    """Return copies of `calls` tagged with `phase`. Never mutates the input."""
    return [tc.model_copy(update={"phase": phase}) for tc in calls]


def record_rejected(state: ColonyState, tc: ToolCall, reason: str) -> ColonyState:
    """Log `tc` as rejected without validating or applying it.

    For calls the graph refuses by policy (e.g. a non-propose_action call in
    the debate phase), so the record still shows them.
    """
    rejected = tc.model_copy(update={"rejection_reason": reason})
    return state.model_copy(update={"turn_tool_calls": [*state.turn_tool_calls, rejected]})
```

4. In `_effect_propose_action`, change `name=args["name"]` to `name=strip_scaffold_prefix(args["name"])`, `effects_preview=args.get("effects_preview", [])` to `effects_preview=[]`, and add `risk_note=args.get("risk_note") if isinstance(args.get("risk_note"), str) else None,`.

5. Replace Section 6's `advance_turn` and add `close_turn`:

```python
CLOSE_LEGITIMACY_DELTA = -1
CLOSE_REASON = "end of turn"


def close_turn(state: ColonyState) -> ColonyState:
    """End-of-turn passive legitimacy decay, as a validated tool call.

    Runs every turn, including one a coup or death already ended, as the
    decay always has.
    """
    tc = ToolCall(
        name="modify_legitimacy",
        caller_id=None,
        args={"delta": CLOSE_LEGITIMACY_DELTA, "reason": CLOSE_REASON},
        phase="close",
    )
    return apply_tool_call(state, tc)


def advance_turn(state: ColonyState) -> ColonyState:
    """Increment the turn, evaluate end state, clear per-turn fields.

    Decay moved to `close_turn` (call it first).
    """
    partial = state.model_copy(update={"turn_number": state.turn_number + 1})
    return partial.model_copy(
        update={
            "end_state": compute_end_state(partial),
            "current_event": None,
            "turn_proposals": [],
            "turn_tool_calls": [],
        }
    )
```

Keep the rest of the original `advance_turn` body's field-clearing behaviour identical (check the original for any other cleared field and keep it).

6. Keep the game's decay alive in the graph: in `backend/app/agent/nodes.py::advance_turn`, replace `new_colony = engine.advance_turn(colony_state)` with:

```python
    closed = engine.close_turn(colony_state)
    new_colony = engine.advance_turn(closed)
```

(Task 7 adds the persistence of `closed.turn_tool_calls`.)

- [ ] **Step 9: Update the validator** (`backend/app/game/validator.py`)

Add near the top:

```python
import re

_SCAFFOLD_PREFIX = re.compile(r"^\s*(rank\s+)?(flavor|dominant|moral|wildcard)\s*:\s*", re.IGNORECASE)


def strip_scaffold_prefix(name: str) -> str:
    """Remove a leaked prompt scaffold like 'DOMINANT:' or 'Rank flavor:'."""
    return _SCAFFOLD_PREFIX.sub("", name, count=1).strip()
```

In `_validate_propose_action`, replace the name checks and duplicate check with:

```python
    raw_name = args.get("name")
    if not isinstance(raw_name, str):
        return _fail("missing_name")
    name = strip_scaffold_prefix(raw_name)
    if not name:
        return _fail("missing_name")
```

and `normalised = name.lower()`; compare with `strip_scaffold_prefix(proposal.name).lower()`.

In `_validate_modify_resource`, delete the two `would_overflow` lines and add a comment: `# Overflow is allowed: the effect clamps at max (spec §4.1).`

- [ ] **Step 10: Run the tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_engine.py tests/test_resolution.py tests/test_validator.py tests/test_eval_choice_spread.py tests/eval/playtest/test_summaries.py -q`
Expected: PASS. Then run the full gates; fix any other test that asserted the old consolidated legitimacy call or `"overruled dominant advisor"` (search `grep -rn "overruled dominant\|; " backend/tests | grep -i legit`).

- [ ] **Step 11: Commit**

```bash
git add backend/app/game/schemas.py backend/app/game/resolution.py backend/app/game/engine.py backend/app/game/validator.py backend/app/agent/nodes.py backend/tests/test_engine.py backend/tests/test_resolution.py backend/tests/test_validator.py backend/tests/test_eval_choice_spread.py backend/tests/eval/playtest/test_summaries.py
git commit -m "feat(engine): applied deltas, phase tags, per-rule legitimacy, overflow clamp, close_turn

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Engine-rendered previews (`app/game/preview.py`)

**Files:**
- Create: `backend/app/game/preview.py`
- Modify: `backend/app/eval/choice_spread.py` (drop `_option_outcome`, use `simulate_option(...).to_spread_dict()`)
- Test: `backend/tests/test_preview.py` (new), `backend/tests/test_eval_choice_spread.py` (must still pass)

**Interfaces:**
- Consumes: Task 2's `applied_delta`, `stamp_phase`, `derive_resolution_consequences`, `OVERRULE_GRIEVANCE_REASON`, `CLOSE_LEGITIMACY_DELTA`.
- Produces:
  - `preview.LegitimacyPart(reason: str, applied: int)` (frozen dataclass)
  - `preview.OptionOutcome` (frozen dataclass, fields below) with `.to_spread_dict(picked: Proposal) -> dict`
  - `preview.simulate_option(state: ColonyState, picked: Proposal, proposals: list[Proposal]) -> OptionOutcome`
  - `preview.outcome_from_calls(calls: list[ToolCall], state_before: ColonyState) -> OptionOutcome`
  - `preview.render_effects(setting: Setting, outcome: OptionOutcome) -> list[str]`
  - `preview.render_close() -> list[str]` (spec wrote `render_close(setting)`; the line doesn't vary by setting, so no argument)
  - `preview.fmt_delta(n: int) -> str`
  - `preview.compact_state(state: ColonyState) -> dict`, `preview.state_from_compact(data: dict) -> ColonyState`

- [ ] **Step 1: Write the failing tests** (`backend/tests/test_preview.py`)

```python
"""Engine-rendered previews (spec §4.1)."""

from __future__ import annotations

from uuid import UUID

import pytest

from app.game import engine
from app.game.generator import generate_run
from app.game.preview import (
    compact_state,
    outcome_from_calls,
    render_close,
    render_effects,
    simulate_option,
    state_from_compact,
)
from app.game.schemas import ColonyState, Proposal, ProposalFlavor, ToolCall
from app.game.setting_loader import load_setting

USER = UUID("bbbbbbbb-0000-0000-0000-000000000002")
ARCTIC = load_setting("arctic_station_v1")


@pytest.fixture
def colony() -> ColonyState:
    return generate_run(user_id=USER, setting_id="arctic_station_v1", seed=7).model_copy(
        update={"turn_number": 4, "legitimacy": 60}
    )


def _tc(name: str, **args: object) -> ToolCall:
    return ToolCall(name=name, caller_id=None, args={"reason": "r", **args})


def _p(pid: str, flavor: ProposalFlavor, calls: list[ToolCall], by: UUID | None = None) -> Proposal:
    return Proposal(
        id=pid, proposed_by_agent_id=by, flavor=flavor, name=pid, description="d",
        effects_preview=[], tool_calls_on_pick=calls,
    )


def _set(colony: ColonyState, rid: str, value: int) -> ColonyState:
    res = colony.resources[rid]
    return colony.model_copy(update={"resources": {**colony.resources, rid: res.model_copy(update={"value": value})}})


def test_resource_lines_in_yaml_order_with_true_minus(colony: ColonyState) -> None:
    picked = _p("d", ProposalFlavor.DOMINANT, [
        _tc("modify_resource", resource_id="food", delta=5),
        _tc("modify_resource", resource_id="fuel", delta=-10),
    ])
    lines = render_effects(ARCTIC, simulate_option(colony, picked, [picked]))
    assert lines == ["fuel −10", "food +5"]


def test_clamped_gain_renders_at_cap(colony: ColonyState) -> None:
    colony = _set(colony, "fuel", colony.resources["fuel"].max - 2)
    picked = _p("d", ProposalFlavor.DOMINANT, [_tc("modify_resource", resource_id="fuel", delta=10)])
    assert render_effects(ARCTIC, simulate_option(colony, picked, [picked])) == ["fuel +2 (at cap)"]


def test_overrule_legitimacy_single_part(colony: ColonyState) -> None:
    dom_by = colony.agents[0].id
    dom = _p("d", ProposalFlavor.DOMINANT, [_tc("modify_resource", resource_id="fuel", delta=-1)], by=dom_by)
    mor = _p("m", ProposalFlavor.MORAL, [_tc("modify_resource", resource_id="food", delta=3)])
    lines = render_effects(ARCTIC, simulate_option(colony, mor, [dom, mor]))
    name = colony.agents[0].name
    assert lines == ["food +3", f"legitimacy −2 (overrules {name})", f"{name} grievance +2"]


def test_legitimacy_multiple_parts(colony: ColonyState) -> None:
    target = colony.agents[1]
    picked = _p("w", ProposalFlavor.WILDCARD, [
        _tc("modify_health", target_id=str(target.id), delta=-60),
        _tc("modify_resource", resource_id="fuel", delta=-40),
    ])
    lines = render_effects(ARCTIC, simulate_option(colony, picked, [picked]))
    assert lines[0] == "fuel −40"
    assert lines[1] == f"legitimacy −17 (severe harm to {target.name} −12, heavy fuel drain −5)"
    assert lines[2] == f"{target.name} −60 health"


def test_render_legitimacy_partial_clamp_at_floor(colony: ColonyState) -> None:
    colony = colony.model_copy(update={"legitimacy": 1})
    dom = _p("d", ProposalFlavor.DOMINANT, [], by=colony.agents[0].id)
    mor = _p("m", ProposalFlavor.MORAL, [_tc("modify_resource", resource_id="food", delta=1)])
    lines = render_effects(ARCTIC, simulate_option(colony, mor, [dom, mor]))
    assert f"legitimacy −1 (overrules {colony.agents[0].name})" in lines


def test_legitimacy_all_parts_clamped_reads_unchanged(colony: ColonyState) -> None:
    colony = colony.model_copy(update={"legitimacy": 0})
    dom = _p("d", ProposalFlavor.DOMINANT, [], by=colony.agents[0].id)
    mor = _p("m", ProposalFlavor.MORAL, [_tc("modify_resource", resource_id="food", delta=1)])
    lines = render_effects(ARCTIC, simulate_option(colony, mor, [dom, mor]))
    assert "legitimacy unchanged (at floor)" in lines


def test_lethal_advisor_hit_renders_dies(colony: ColonyState) -> None:
    target = colony.agents[2]
    colony = colony.model_copy(update={"agents": [
        a.model_copy(update={"health": 30}) if a.id == target.id else a for a in colony.agents
    ]})
    picked = _p("w", ProposalFlavor.WILDCARD, [_tc("modify_health", target_id=str(target.id), delta=-40)])
    lines = render_effects(ARCTIC, simulate_option(colony, picked, [picked]))
    assert f"{target.name} −30 health, dies" in lines


def test_lethal_player_hit_renders_run_ends(colony: ColonyState) -> None:
    colony = colony.model_copy(update={"player_health": 20})
    picked = _p("w", ProposalFlavor.WILDCARD, [_tc("modify_health", target_id="player", delta=-30)])
    lines = render_effects(ARCTIC, simulate_option(colony, picked, [picked]))
    assert lines == ["you −20 health", "run ends"]


def test_no_own_effect_reads_no_immediate_effect(colony: ColonyState) -> None:
    dom = _p("d", ProposalFlavor.DOMINANT, [], by=colony.agents[0].id)
    mor = _p("m", ProposalFlavor.MORAL, [])
    lines = render_effects(ARCTIC, simulate_option(colony, mor, [dom, mor]))
    assert lines[0] == "no immediate effect"
    assert lines[1].startswith("legitimacy −2")


def test_status_change_renders(colony: ColonyState) -> None:
    target = colony.agents[3]
    picked = _p("d", ProposalFlavor.DOMINANT, [
        _tc("set_advisor_status", target_id=str(target.id), new_status="DETAINED"),
    ])
    lines = render_effects(ARCTIC, simulate_option(colony, picked, [picked]))
    assert lines == [f"{target.name} detained"]


def test_rejected_nested_call_is_reported(colony: ColonyState) -> None:
    picked = _p("d", ProposalFlavor.DOMINANT, [_tc("modify_resource", resource_id="water", delta=-5)])
    out = simulate_option(colony, picked, [picked])
    assert [tc.rejection_reason for tc in out.rejected] == ["unknown_resource"]
    assert render_effects(ARCTIC, out) == ["no immediate effect"]


def test_simulate_does_not_mutate_input(colony: ColonyState) -> None:
    before = colony.model_dump()
    picked = _p("d", ProposalFlavor.DOMINANT, [_tc("modify_resource", resource_id="fuel", delta=-10)])
    simulate_option(colony, picked, [picked])
    assert colony.model_dump() == before


def test_outcome_from_recorded_calls_renders_identically(colony: ColonyState) -> None:
    dom = _p("d", ProposalFlavor.DOMINANT, [_tc("modify_resource", resource_id="fuel", delta=-10)], by=colony.agents[0].id)
    mor = _p("m", ProposalFlavor.MORAL, [_tc("modify_resource", resource_id="food", delta=4)])
    preview = render_effects(ARCTIC, simulate_option(colony, mor, [dom, mor]))
    derived = engine.derive_resolution_consequences(colony, mor, [dom, mor])
    after = engine.apply_tool_calls(colony, engine.stamp_phase([*mor.tool_calls_on_pick, *derived], "resolve"))
    recorded = after.turn_tool_calls[len(colony.turn_tool_calls):]
    assert render_effects(ARCTIC, outcome_from_calls(recorded, colony)) == preview


def test_render_close() -> None:
    assert render_close() == ["−1 legitimacy at day's end"]


def test_compact_state_round_trips_stats(colony: ColonyState) -> None:
    restored = state_from_compact(compact_state(colony))
    assert restored.resources == colony.resources
    assert restored.legitimacy == colony.legitimacy
    assert [a.name for a in restored.agents] == [a.name for a in colony.agents]
    assert restored.event_log == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_preview.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.game.preview'`

- [ ] **Step 3: Implement `backend/app/game/preview.py`**

```python
"""Engine-rendered option previews (spec §4.1).

`simulate_option` runs the exact path `nodes.resolve` runs (the picked
proposal's nested calls plus `derive_resolution_consequences`, phase
"resolve") on a deep copy. `outcome_from_calls` turns recorded calls into
the same `OptionOutcome`, so a preview and the audit of what happened go
through one renderer. End-of-turn decay is not part of an option; it is
shown once via `render_close`. Pure: no I/O, no LLM.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from app.game import engine
from app.game.resolution import OVERRULE_GRIEVANCE_REASON
from app.game.schemas import AdvisorStatus, ColonyState, Proposal, ToolCall
from app.game.setting_loader import Setting

_MINUS = "−"
_STATUS_WORD = {
    AdvisorStatus.DETAINED.value: "detained",
    AdvisorStatus.DISMISSED.value: "dismissed",
    AdvisorStatus.ACTIVE.value: "restored",
}


def fmt_delta(n: int) -> str:
    return f"+{n}" if n > 0 else f"{_MINUS}{abs(n)}" if n < 0 else "0"


@dataclass(frozen=True)
class LegitimacyPart:
    reason: str
    applied: int


@dataclass(frozen=True)
class OptionOutcome:
    resource_deltas: dict[str, int]
    resources_capped: frozenset[str]
    legitimacy_parts: tuple[LegitimacyPart, ...]
    player_health_delta: int
    player_health_capped: bool
    advisor_health_deltas: dict[UUID, int]
    advisor_health_capped: frozenset[UUID]
    deaths: frozenset[UUID]
    grievance_deltas: dict[UUID, int]
    grievance_capped: frozenset[UUID]
    status_changes: tuple[tuple[UUID, str, str], ...]  # (agent id, from, to)
    rejected: tuple[ToolCall, ...]
    n_calls: int
    own_effect: bool
    ends_run: bool
    end_state: str | None
    agent_names: dict[UUID, str]
    status_before: dict[UUID, str]
    vector: tuple[float, ...]

    @property
    def legitimacy_delta(self) -> int:
        return sum(p.applied for p in self.legitimacy_parts)

    def to_spread_dict(self, picked: Proposal) -> dict[str, Any]:
        """The dict shape `app.eval.choice_spread` has always returned."""
        statuses = [
            {"agent": self.agent_names[aid], "from": old, "to": new}
            for aid, old, new in self.status_changes
        ] + [
            {"agent": self.agent_names[aid], "from": self.status_before[aid], "to": "DEAD"}
            for aid in self.deaths
        ]
        return {
            "proposal_id": picked.id,
            "flavor": str(picked.flavor.value),
            "name": picked.name,
            "resource_deltas": dict(self.resource_deltas),
            "legitimacy_delta": self.legitimacy_delta,
            "player_health_delta": self.player_health_delta,
            "grievance_deltas": {
                self.agent_names[aid]: d for aid, d in self.grievance_deltas.items() if d
            },
            "status_changes": statuses,
            "n_calls": self.n_calls,
            "rejected_calls": [{"name": tc.name, "reason": tc.rejection_reason} for tc in self.rejected],
            "ends_run": self.end_state,
            "vector": list(self.vector),
        }


def simulate_option(
    state: ColonyState, picked: Proposal, proposals: list[Proposal]
) -> OptionOutcome:
    """Dry-run picking `picked` from `proposals`. Never mutates `state`."""
    before = state.model_copy(deep=True)
    derived = engine.derive_resolution_consequences(before, picked, proposals)
    calls = engine.stamp_phase([*picked.tool_calls_on_pick, *derived], "resolve")
    after = engine.apply_tool_calls(before, calls)
    return outcome_from_calls(after.turn_tool_calls[len(before.turn_tool_calls) :], before)


def outcome_from_calls(calls: list[ToolCall], state_before: ColonyState) -> OptionOutcome:
    """Build an OptionOutcome from recorded calls (with `applied_delta`)."""
    applied = [tc for tc in calls if tc.rejection_reason is None]
    after = engine.check_deaths(_replay(state_before, applied))
    end_state = engine.compute_end_state(after)

    resource_deltas = {rid: 0 for rid in state_before.resources}
    capped_res: set[str] = set()
    parts: list[LegitimacyPart] = []
    player_delta, player_capped = 0, False
    health: dict[UUID, int] = {}
    health_capped: set[UUID] = set()
    grievance: dict[UUID, int] = {}
    grievance_capped: set[UUID] = set()
    statuses: list[tuple[UUID, str, str]] = []
    status_before = {a.id: a.status.value for a in state_before.agents}
    own_effect = False

    for tc in applied:
        got = tc.applied_delta or 0
        asked = tc.args.get("delta")
        clamped_gain = isinstance(asked, int) and asked > 0 and got < asked
        if tc.name == "modify_resource":
            rid = tc.args["resource_id"]
            resource_deltas[rid] += got
            if clamped_gain:
                capped_res.add(rid)
            own_effect = own_effect or got != 0
        elif tc.name == "modify_legitimacy":
            parts.append(LegitimacyPart(str(tc.args.get("reason", "")), got))
        elif tc.name == "modify_health":
            own_effect = own_effect or got != 0
            if tc.args["target_id"] == "player":
                player_delta += got
                player_capped = player_capped or clamped_gain
            else:
                aid = UUID(tc.args["target_id"])
                health[aid] = health.get(aid, 0) + got
                if clamped_gain:
                    health_capped.add(aid)
        elif tc.name == "adjust_grievance":
            aid = UUID(tc.args["target_id"])
            grievance[aid] = grievance.get(aid, 0) + got
            if clamped_gain:
                grievance_capped.add(aid)
            if tc.args.get("reason") != OVERRULE_GRIEVANCE_REASON:
                own_effect = own_effect or got != 0
        elif tc.name == "set_advisor_status":
            aid = UUID(tc.args["target_id"])
            statuses.append((aid, status_before[aid], str(tc.args["new_status"])))
            own_effect = True

    alive_before = {a.id for a in state_before.agents if a.is_alive}
    deaths = frozenset(a.id for a in after.agents if a.id in alive_before and not a.is_alive)
    living_before = len(alive_before)
    living_after = sum(1 for a in after.agents if a.is_alive)
    ends_run = (
        (state_before.player_health > 0 and after.player_health <= 0)
        or any(
            state_before.resources[rid].value > 0 and after.resources[rid].value == 0
            for rid in state_before.resources
        )
        or (living_before > 0 and living_after == 0)
    )

    rids = sorted(state_before.resources)
    vector = tuple(
        [resource_deltas[rid] / max(1, state_before.resources[rid].max) for rid in rids]
        + [sum(p.applied for p in parts) / 100, player_delta / 100]
    )
    return OptionOutcome(
        resource_deltas=resource_deltas,
        resources_capped=frozenset(capped_res),
        legitimacy_parts=tuple(parts),
        player_health_delta=player_delta,
        player_health_capped=player_capped,
        advisor_health_deltas=health,
        advisor_health_capped=frozenset(health_capped),
        deaths=deaths,
        grievance_deltas=grievance,
        grievance_capped=frozenset(grievance_capped),
        status_changes=tuple(statuses),
        rejected=tuple(tc for tc in calls if tc.rejection_reason is not None),
        n_calls=len(calls),
        own_effect=own_effect,
        ends_run=ends_run,
        end_state=end_state.value if end_state is not None else None,
        agent_names={a.id: a.name for a in state_before.agents},
        status_before=status_before,
        vector=vector,
    )


def _replay(state: ColonyState, applied: list[ToolCall]) -> ColonyState:
    """Apply recorded applied deltas directly (no validation). Reconstruction only."""
    resources = dict(state.resources)
    legitimacy, player_health = state.legitimacy, state.player_health
    agents = {a.id: a for a in state.agents}
    for tc in applied:
        got = tc.applied_delta or 0
        if tc.name == "modify_resource":
            r = resources[tc.args["resource_id"]]
            resources[r.id] = r.model_copy(update={"value": r.value + got})
        elif tc.name in ("modify_legitimacy", "back_leader"):
            legitimacy += got
        elif tc.name == "modify_health" and tc.args["target_id"] == "player":
            player_health += got
        elif tc.name == "modify_health":
            a = agents[UUID(tc.args["target_id"])]
            agents[a.id] = a.model_copy(update={"health": a.health + got})
        elif tc.name == "adjust_grievance":
            a = agents[UUID(tc.args["target_id"])]
            agents[a.id] = a.model_copy(update={"grievance": a.grievance + got})
        elif tc.name == "set_advisor_status":
            a = agents[UUID(tc.args["target_id"])]
            agents[a.id] = a.model_copy(update={"status": AdvisorStatus(tc.args["new_status"])})
    return state.model_copy(
        update={
            "resources": resources,
            "legitimacy": legitimacy,
            "player_health": player_health,
            "agents": [agents[a.id] for a in state.agents],
        }
    )


def render_effects(setting: Setting, outcome: OptionOutcome) -> list[str]:
    """Player-facing preview lines. Order and wording: spec §4.1."""
    lines: list[str] = []
    for res in setting.resources:
        d = outcome.resource_deltas.get(res.id, 0)
        if d:
            cap = " (at cap)" if res.id in outcome.resources_capped else ""
            lines.append(f"{res.name.lower()} {fmt_delta(d)}{cap}")

    if outcome.legitimacy_parts:
        moving = [p for p in outcome.legitimacy_parts if p.applied != 0]
        if not moving:
            lines.append("legitimacy unchanged (at floor)")
        elif len(moving) == 1:
            lines.append(f"legitimacy {fmt_delta(moving[0].applied)} ({moving[0].reason})")
        else:
            total = sum(p.applied for p in moving)
            inner = ", ".join(f"{p.reason} {fmt_delta(p.applied)}" for p in moving)
            lines.append(f"legitimacy {fmt_delta(total)} ({inner})")

    if outcome.player_health_delta:
        cap = " (at cap)" if outcome.player_health_capped else ""
        lines.append(f"you {fmt_delta(outcome.player_health_delta)} health{cap}")

    for aid, d in outcome.advisor_health_deltas.items():
        if not d:
            continue
        name = outcome.agent_names[aid]
        suffix = ", dies" if aid in outcome.deaths else ""
        cap = " (at cap)" if aid in outcome.advisor_health_capped else ""
        lines.append(f"{name} {fmt_delta(d)} health{suffix}{cap}")

    for aid, d in outcome.grievance_deltas.items():
        if d:
            cap = " (at cap)" if aid in outcome.grievance_capped else ""
            lines.append(f"{outcome.agent_names[aid]} grievance {fmt_delta(d)}{cap}")

    for aid, _old, new in outcome.status_changes:
        lines.append(f"{outcome.agent_names[aid]} {_STATUS_WORD.get(new, new.lower())}")

    if outcome.ends_run:
        lines.append("run ends")
    if not outcome.own_effect:
        lines.insert(0, "no immediate effect")
    return lines


def render_close() -> list[str]:
    """The end-of-turn rule, shown once in the ChoosePanel header."""
    return [f"{fmt_delta(engine.CLOSE_LEGITIMACY_DELTA)} legitimacy at day's end"]


_COMPACT_EXCLUDE: dict[str, Any] = {
    "event_log": True,
    "consequence_seeds": True,
    "turn_tool_calls": True,
    "turn_proposals": True,
    "current_event": True,
    "agents": {"__all__": {"memory", "relationships"}},
}


def compact_state(state: ColonyState) -> dict[str, Any]:
    """Stats-only JSON of `state` for `turn_meta.pre_pick` (audit input)."""
    return state.model_dump(mode="json", exclude=_COMPACT_EXCLUDE)


def state_from_compact(data: dict[str, Any]) -> ColonyState:
    return ColonyState.model_validate(
        {
            **data,
            "event_log": [],
            "consequence_seeds": [],
            "turn_tool_calls": [],
            "turn_proposals": [],
            "current_event": None,
        }
    )
```

- [ ] **Step 4: Rewire `choice_spread.py`**

Delete `_agent_status` and `_option_outcome`. Import `from app.game.preview import simulate_option`. In `compute_choice_spread`:

```python
    options = [simulate_option(colony, p, proposals).to_spread_dict(p) for p in proposals]
```

Update the module docstring's "Scope" paragraph: `End-of-turn decay is excluded (it is the same for every option and shown once in the UI); autonomous actions and seeds come after the pick and are not simulated.`

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_preview.py tests/test_eval_choice_spread.py tests/test_sweep.py tests/test_play_test.py -q`
Expected: PASS. `to_spread_dict` keeps the old `status_changes` shape (`{"agent", "from", "to"}`, deaths as `"to": "DEAD"`).

- [ ] **Step 6: Run the full gates and commit**

```bash
git add backend/app/game/preview.py backend/app/eval/choice_spread.py backend/tests/test_preview.py
git commit -m "feat(game): engine-rendered option previews via simulate_option

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Narration and risk-note guard (`app/agent/narration_guard.py`)

**Files:**
- Create: `backend/app/agent/narration_guard.py`
- Test: `backend/tests/test_narration_guard.py` (new)

**Interfaces:**
- Consumes: Task 1's `Setting.player_title`, `Setting.lexicon`.
- Produces:
  - `Violation(code: str)` frozen dataclass with `.kind -> str` and `.hard -> bool`
  - `HARD_CODES = frozenset({"empty", "markup", "fourth_wall", "prompt_echo", "script"})` (`empty` is added beyond the spec table so a blank model reply is never kept)
  - `validate_narration(text: str, allowed_terms: frozenset[str], roster_names: frozenset[str]) -> list[Violation]` (spec wrote a `setting` argument; the guard needs the roster, not the setting)
  - `build_allowed_terms(setting: Setting, state: ColonyState, extra_texts: Iterable[str] = ()) -> frozenset[str]`
  - `build_strict_terms(setting: Setting, state: ColonyState) -> frozenset[str]`
  - `sanitize_risk_note(note: str | None, setting: Setting, allowed_terms: frozenset[str], roster_names: frozenset[str]) -> str | None`
  - `RISK_NOTE_MAX = 120`

- [ ] **Step 1: Write the failing tests** (`backend/tests/test_narration_guard.py`)

```python
"""Pure narration guard (spec §4.2) and risk_note sanitizer (spec §4.1)."""

from __future__ import annotations

from uuid import UUID

import pytest

from app.agent.narration_guard import (
    build_allowed_terms,
    build_strict_terms,
    sanitize_risk_note,
    validate_narration,
)
from app.game.generator import generate_run
from app.game.setting_loader import load_setting

ARCTIC = load_setting("arctic_station_v1")
COLONY = generate_run(
    user_id=UUID("bbbbbbbb-0000-0000-0000-000000000002"), setting_id="arctic_station_v1", seed=7
)
ROSTER = frozenset(a.name for a in COLONY.agents)  # Astrid, Dmitri, Nadia, Yuri
ALLOWED = build_allowed_terms(ARCTIC, COLONY)


def codes(text: str) -> list[str]:
    return [v.code for v in validate_narration(text, ALLOWED, ROSTER)]


def test_clean_narration_passes() -> None:
    assert codes("You backed Astrid. The pump held until dawn, and Nadia slept for once.") == []


@pytest.mark.parametrize(
    ("text", "code"),
    [
        ('<agent name="Astrid">Fine.</agent>', "markup"),
        ("The player chose the pump.", "fourth_wall"),
        ("Now write the recap.\nThe pump held.", "prompt_echo"),
        ("Context: arctic station.", "prompt_echo"),
        ("Keep a narrator-voice tone.", "prompt_echo"),
    ],
)
def test_hard_codes(text: str, code: str) -> None:
    found = validate_narration(text, ALLOWED, ROSTER)
    assert code in [v.code for v in found]
    assert all(v.hard for v in found if v.code == code)


def test_script_violation_on_cyrillic() -> None:
    found = validate_narration("Насос выдержал до рассвета. Никто не спал.", ALLOWED, ROSTER)
    assert [v.code for v in found] == ["script"]
    assert found[0].hard


def test_empty_is_hard() -> None:
    found = validate_narration("   ", ALLOWED, ROSTER)
    assert [v.code for v in found] == ["empty"] and found[0].hard


def test_too_long_is_soft() -> None:
    text = " ".join(["The pump held."] * 5)
    found = validate_narration(text, ALLOWED, ROSTER)
    assert [v.code for v in found] == ["too_long"]
    assert not found[0].hard


def test_unknown_name_mid_sentence_is_soft() -> None:
    found = validate_narration("You backed Astrid, and Ilyana watched.", ALLOWED, ROSTER)
    assert [v.code for v in found] == ["unknown_name:Ilyana"]
    assert not found[0].hard


@pytest.mark.parametrize(
    "text",
    [
        "Cold settled in. Nobody argued.",  # sentence-initial capital
        "The plan: Wait for dawn.",  # after a colon
        "The pump held.\nMorning came.",  # after a newline
        "It held — Barely.",  # after a dash
        "“Hold,” Astrid said.",  # curly quotes
        "Astrid’s hands shook.",  # possessive
        "On Monday the pump held.",  # stop-list
        "You held Vorgrad together.",  # lexicon
        "The Quartermaster counted twice.",  # role
        "The Director signed it.",  # player title
    ],
)
def test_known_or_initial_tokens_pass(text: str) -> None:
    assert codes(text) == []


def test_sentence_initial_name_from_another_roster_is_flagged() -> None:
    # "Ilya" is in the arctic Radio Operator name_pool but not on this roster.
    assert "unknown_name:Ilya" in codes("Ilya keyed the radio twice.")


def test_allowlist_includes_event_log_and_extra_texts() -> None:
    allowed = build_allowed_terms(ARCTIC, COLONY, ["Corporal Brandt waits outside."])
    assert validate_narration("You let Brandt in.", allowed, ROSTER) == []


def test_strict_terms_exclude_debate_inventions() -> None:
    strict = build_strict_terms(ARCTIC, COLONY)
    assert "Brandt" not in strict
    assert "Vorgrad" in strict


@pytest.mark.parametrize(
    "note",
    [
        "Fuel drops 10 if it fails.",  # digit
        "Legitimacy will suffer.",  # stat word
        "Your health is at risk.",  # stat word
        "The water could freeze.",  # another setting's resource (desert/orbital water)
        "<b>Careful</b>",  # markup
        "The player may regret it.",  # fourth wall
        "x" * 121,  # too long
        "Ask Ilyana what she saw.",  # unknown name
    ],
)
def test_risk_note_dropped(note: str) -> None:
    assert sanitize_risk_note(note, ARCTIC, ALLOWED, ROSTER) is None


def test_risk_note_kept_with_own_resource_name() -> None:
    note = "Fuel may run short before the convoy."
    assert sanitize_risk_note(note, ARCTIC, ALLOWED, ROSTER) == note


def test_risk_note_none_stays_none() -> None:
    assert sanitize_risk_note(None, ARCTIC, ALLOWED, ROSTER) is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_narration_guard.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.agent.narration_guard'`

- [ ] **Step 3: Implement `backend/app/agent/narration_guard.py`**

```python
"""Pure guard for narration text and proposal risk notes (spec §4.1, §4.2).

Hard violations (markup, fourth_wall, prompt_echo, script) force a retry
and, if they persist, the fallback line. Soft ones (too_long,
unknown_name) only steer which attempt is kept. No LLM, no I/O beyond the
cached setting YAML loads.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from functools import lru_cache

from app.game.schemas import ColonyState
from app.game.setting_loader import Setting, list_available_settings, load_setting

HARD_CODES = frozenset({"empty", "markup", "fourth_wall", "prompt_echo", "script"})
RISK_NOTE_MAX = 120
MAX_CHARS = 700
MAX_SENTENCES = 4
MAX_NON_LATIN_SHARE = 0.2

_MARKUP = re.compile(r"<[A-Za-z/][^>]*>")
_FOURTH_WALL = re.compile(r"\bthe player\b", re.IGNORECASE)
_ECHO_PREFIXES = ("now write", "write a", "context:", "characters:", "recap", "keep tone")
_TOKEN = re.compile(r"[A-ZÀ-Þ][a-zß-ÿ]+(?:[-'][A-Za-zÀ-ÿ]+)*")
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")
_STAT_WORDS = re.compile(r"\b(legitimacy|health|grievance)\b", re.IGNORECASE)
_QUOTES = str.maketrans({"“": '"', "”": '"', "‘": "'", "’": "'"})
_INITIAL_AFTER = set(".!?:;\n—–")
_STOP = frozenset(
    {
        "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday",
        "January", "February", "March", "April", "May", "June", "July", "August",
        "September", "October", "November", "December", "Day", "Turn", "God",
    }
)


@dataclass(frozen=True)
class Violation:
    code: str

    @property
    def kind(self) -> str:
        return self.code.split(":", 1)[0]

    @property
    def hard(self) -> bool:
        return self.kind in HARD_CODES


def _normalize(text: str) -> str:
    return text.translate(_QUOTES)


def _tokens(text: str) -> list[tuple[str, int]]:
    out = []
    for m in _TOKEN.finditer(text):
        tok = m.group(0)
        out.append((tok[:-2] if tok.endswith("'s") else tok, m.start()))
    return out


def _is_sentence_initial(text: str, start: int) -> bool:
    prefix = text[:start].rstrip(" \t\"'(")
    if not prefix:
        return True
    if prefix[-1] in _INITIAL_AFTER:
        return True
    return prefix[-1] == "-" and (len(prefix) == 1 or prefix[-2].isspace())


def _non_latin_share(text: str) -> float:
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return 0.0
    non_latin = sum(1 for c in letters if not unicodedata.name(c, "").startswith("LATIN"))
    return non_latin / len(letters)


@lru_cache(maxsize=1)
def _all_name_pool_names() -> frozenset[str]:
    names: set[str] = set()
    for sid in list_available_settings():
        for archetype in load_setting(sid).advisor_archetypes:
            names.update(archetype.name_pool)
    return frozenset(names)


@lru_cache(maxsize=None)
def _foreign_resource_words(setting_id: str) -> frozenset[str]:
    own = {w for r in load_setting(setting_id).resources for w in (r.id, r.name.lower())}
    every = {
        w
        for sid in list_available_settings()
        for r in load_setting(sid).resources
        for w in (r.id, r.name.lower())
    }
    return frozenset(every - own)


def _term_tokens(texts: Iterable[str]) -> frozenset[str]:
    out: set[str] = set()
    for raw in texts:
        text = _normalize(raw)
        out.update(tok for tok, _ in _tokens(text))
        out.update(w for w in re.split(r"\s+", text) if w)
    return frozenset(out)


def validate_narration(
    text: str, allowed_terms: frozenset[str], roster_names: frozenset[str]
) -> list[Violation]:
    """Every rule violated by `text`, in a stable order (spec §4.2 table)."""
    norm = _normalize(text)
    if not norm.strip():
        return [Violation("empty")]
    found: list[Violation] = []
    if _MARKUP.search(norm):
        found.append(Violation("markup"))
    if _FOURTH_WALL.search(norm):
        found.append(Violation("fourth_wall"))
    for line in norm.splitlines():
        low = line.strip().lower()
        if low.startswith(_ECHO_PREFIXES) or "narrator-voice" in low:
            found.append(Violation("prompt_echo"))
            break
    if _non_latin_share(norm) > MAX_NON_LATIN_SHARE:
        found.append(Violation("script"))
    sentences = [s for s in _SENTENCE_SPLIT.split(norm.strip()) if s]
    if len(sentences) > MAX_SENTENCES or len(norm) > MAX_CHARS:
        found.append(Violation("too_long"))

    pools = _all_name_pool_names()
    seen: set[str] = set()
    for tok, start in _tokens(norm):
        if tok in allowed_terms or tok in _STOP or tok in seen:
            continue
        foreign_name = tok in pools and tok not in roster_names
        if _is_sentence_initial(norm, start) and not foreign_name:
            continue
        seen.add(tok)
        found.append(Violation(f"unknown_name:{tok}"))
    return found


def build_allowed_terms(
    setting: Setting, state: ColonyState, extra_texts: Iterable[str] = ()
) -> frozenset[str]:
    """Names the player has seen this run, plus the setting's own vocabulary."""
    texts: list[str] = [setting.player_title, setting.name, *setting.lexicon]
    texts += [a.name for a in state.agents] + [a.role for a in state.agents]
    for resolved in state.event_log:
        texts += [resolved.event.title, resolved.event.body, resolved.summary]
    if state.current_event is not None:
        texts += [state.current_event.title, state.current_event.body]
    texts += [t for p in state.turn_proposals for t in (p.name, p.description)]
    texts += list(extra_texts)
    return _term_tokens(texts)


def build_strict_terms(setting: Setting, state: ColonyState) -> frozenset[str]:
    """Audit-only allowlist: roster, lexicon and authored setting text only."""
    texts: list[str] = [setting.player_title, setting.name, setting.lore, setting.narrative_voice]
    texts += list(setting.lexicon)
    texts += [a.name for a in state.agents] + [a.role for a in state.agents]
    for e in setting.event_categories:
        texts += [e.prompt_template, e.title or ""]
    return _term_tokens(texts)


def sanitize_risk_note(
    note: str | None,
    setting: Setting,
    allowed_terms: frozenset[str],
    roster_names: frozenset[str],
) -> str | None:
    """The note if it is short, qualitative and clean; otherwise None."""
    if note is None:
        return None
    note = note.strip()
    if not note or len(note) > RISK_NOTE_MAX:
        return None
    if re.search(r"\d", note) or _STAT_WORDS.search(note):
        return None
    words = {w.lower() for w in re.findall(r"[A-Za-z]+", note)}
    if words & _foreign_resource_words(setting.id):
        return None
    blocking = {"markup", "fourth_wall", "script", "unknown_name"}
    if any(v.kind in blocking for v in validate_narration(note, allowed_terms, roster_names)):
        return None
    return note
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_narration_guard.py -q`
Expected: PASS. If `test_clean_narration_passes` flags a word, check whether it is a legitimate English capital in mid-sentence; the fixtures above avoid those on purpose, so a failure means the tokenizer or `_is_sentence_initial` is wrong, not the fixture.

- [ ] **Step 5: Run the full gates and commit**

```bash
git add backend/app/agent/narration_guard.py backend/tests/test_narration_guard.py
git commit -m "feat(agent): pure narration guard and risk-note sanitizer

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Turn preparation (`app/agent/prepare.py`)

**Files:**
- Create: `backend/app/agent/prepare.py`
- Test: `backend/tests/test_prepare.py` (new)

**Interfaces:**
- Consumes: `engine.apply_tool_call`, `engine.record_rejected` (Task 2); `preview.simulate_option`, `preview.render_effects`, `preview.outcome_from_calls` (Task 3); `narration_guard.build_allowed_terms`, `sanitize_risk_note` (Task 4).
- Produces:
  - `DEBATE_TOOL_NAMES: frozenset[str] = frozenset({"propose_action"})`
  - `ProposalClass = Literal["valid", "degraded", "no_effect"]`
  - `PreparedTurn(state: ColonyState, proposals: list[Proposal], classes: list[ProposalClass], valid_count: int, risk_notes_dropped: int)` frozen dataclass. `classes` is parallel to `proposals` (surfaced only); `valid_count` counts every proposal of the response.
  - `prepare_proposals(state: ColonyState, debate_tool_calls: list[ToolCall], setting: Setting, extra_texts: Iterable[str] = ()) -> PreparedTurn`

Behaviour (spec §4.1 "Preparation step"):
1. Each debate call is stamped `phase="debate"`. `propose_action` goes through `engine.apply_tool_call`; anything else goes through `engine.record_rejected(..., "not_allowed_in_debate")`.
2. For each new proposal, in emission order, nested calls are applied one at a time to a running copy of the post-debate state. Rejected ones are dropped and recorded `phase="prepare"`, `rejection_reason=f"stripped:{reason}"`. Kept ones are recorded `phase="prepare"`, `rejection_reason=None`, `applied_delta=None` (validation records; never applied).
3. Class from the kept calls' applied deltas (a full clamp counts as no change; a valid `set_advisor_status` counts as a change).
4. Surfaced = sort by (class order valid < degraded < no_effect, emission index), first 3.
5. Each surfaced proposal is simulated against the surfaced list and rendered; `risk_note` sanitized.
6. Returned state: post-debate state with records appended to `turn_tool_calls` and `turn_proposals` replaced by the rendered surfaced list.

- [ ] **Step 1: Write the failing tests** (`backend/tests/test_prepare.py`)

```python
"""Preparation between debate and pause (spec §4.1)."""

from __future__ import annotations

import random
from typing import Any
from uuid import UUID

from app.agent.prepare import prepare_proposals
from app.game import engine
from app.game.generator import generate_run
from app.game.preview import outcome_from_calls, render_effects
from app.game.schemas import ColonyState, ToolCall
from app.game.setting_loader import load_setting

USER = UUID("bbbbbbbb-0000-0000-0000-000000000002")
ARCTIC = load_setting("arctic_station_v1")


def _colony(setting_id: str = "arctic_station_v1", seed: int = 7) -> ColonyState:
    return generate_run(user_id=USER, setting_id=setting_id, seed=seed).model_copy(
        update={"turn_number": 3, "legitimacy": 60}
    )


def _nested(name: str, **args: Any) -> dict[str, Any]:
    return {"name": name, "args": {"reason": "r", **args}}


def _propose(caller: UUID, name: str, flavor: str, nested: list[dict], **extra: Any) -> ToolCall:
    return ToolCall(
        name="propose_action",
        caller_id=caller,
        args={"name": name, "description": f"{name} now.", "flavor": flavor,
              "tool_calls_on_pick": nested, **extra},
    )


def test_debate_whitelist_records_and_skips_non_proposals() -> None:
    colony = _colony()
    ambient = ToolCall(name="modify_resource", caller_id=colony.agents[0].id,
                       args={"resource_id": "fuel", "delta": -5, "reason": "storm"})
    out = prepare_proposals(colony, [ambient], ARCTIC)
    assert out.state.resources["fuel"].value == colony.resources["fuel"].value
    rec = out.state.turn_tool_calls[-1]
    assert (rec.name, rec.phase, rec.rejection_reason) == ("modify_resource", "debate", "not_allowed_in_debate")


def test_strip_unknown_resource_and_degrade() -> None:
    colony = _colony()
    a = colony.agents[0]
    call = _propose(a.id, "Burn stores", "DOMINANT", [
        _nested("modify_resource", resource_id="fuel", delta=-5),
        _nested("modify_resource", resource_id="water", delta=-5),
    ], effects_preview=["+99 legitimacy"])
    out = prepare_proposals(colony, [call], ARCTIC)
    (p,) = out.proposals
    assert [tc.args["resource_id"] for tc in p.tool_calls_on_pick] == ["fuel"]
    assert p.effects_preview == ["fuel −5"]
    assert out.classes == ["degraded"]
    assert out.valid_count == 0
    stripped = [tc for tc in out.state.turn_tool_calls if tc.rejection_reason == "stripped:unknown_resource"]
    assert len(stripped) == 1 and stripped[0].phase == "prepare"


def test_kept_nested_calls_recorded_as_prepare_validation_records() -> None:
    colony = _colony()
    call = _propose(colony.agents[0].id, "Burn", "DOMINANT", [_nested("modify_resource", resource_id="fuel", delta=-5)])
    out = prepare_proposals(colony, [call], ARCTIC)
    kept = [tc for tc in out.state.turn_tool_calls if tc.phase == "prepare"]
    assert len(kept) == 1
    assert kept[0].rejection_reason is None
    assert kept[0].applied_delta is None


def test_selection_prefers_valid_then_degraded_then_no_effect() -> None:
    colony = _colony()
    ids = [a.id for a in colony.agents]
    calls = [
        _propose(ids[0], "Nothing", "DOMINANT", []),
        _propose(ids[1], "Half", "MORAL", [
            _nested("modify_resource", resource_id="food", delta=2),
            _nested("modify_resource", resource_id="oxygen", delta=2),
        ]),
        _propose(ids[2], "Good one", "WILDCARD", [_nested("modify_resource", resource_id="fuel", delta=-3)]),
        _propose(ids[3], "Good two", "MORAL", [_nested("modify_resource", resource_id="food", delta=-3)]),
    ]
    out = prepare_proposals(colony, calls, ARCTIC)
    assert [p.name for p in out.proposals] == ["Good one", "Good two", "Half"]
    assert out.classes == ["valid", "valid", "degraded"]
    assert out.valid_count == 2
    assert [p.name for p in out.state.turn_proposals] == ["Good one", "Good two", "Half"]


def test_prepare_handles_fewer_than_three_proposals() -> None:
    colony = _colony()
    call = _propose(colony.agents[0].id, "Lost cause", "DOMINANT", [_nested("modify_resource", resource_id="water", delta=-5)])
    out = prepare_proposals(colony, [call], ARCTIC)
    assert len(out.proposals) == 1
    assert out.proposals[0].effects_preview == ["no immediate effect"]
    assert out.classes == ["no_effect"]


def test_full_clamp_counts_as_no_effect() -> None:
    colony = _colony()
    fuel = colony.resources["fuel"]
    colony = colony.model_copy(update={"resources": {**colony.resources, "fuel": fuel.model_copy(update={"value": fuel.max})}})
    call = _propose(colony.agents[0].id, "Top up", "DOMINANT", [_nested("modify_resource", resource_id="fuel", delta=5)])
    assert prepare_proposals(colony, [call], ARCTIC).classes == ["no_effect"]


def test_risk_notes_sanitized_and_counted() -> None:
    colony = _colony()
    ids = [a.id for a in colony.agents]
    calls = [
        _propose(ids[0], "A", "DOMINANT", [_nested("modify_resource", resource_id="fuel", delta=-1)], risk_note="Fuel drops 10."),
        _propose(ids[1], "B", "MORAL", [_nested("modify_resource", resource_id="food", delta=-1)], risk_note="The cold could get worse."),
    ]
    out = prepare_proposals(colony, calls, ARCTIC)
    assert [p.risk_note for p in out.proposals] == [None, "The cold could get worse."]
    assert out.risk_notes_dropped == 1


def _random_turn(rng: random.Random, colony: ColonyState) -> list[ToolCall]:
    rids = [*colony.resources, "water", "oxygen"]
    flavors = ["DOMINANT", "MORAL", "WILDCARD"]
    calls = []
    for i, agent in enumerate(colony.agents[: rng.randint(2, 4)]):
        nested = []
        for _ in range(rng.randint(0, 3)):
            kind = rng.choice(["res", "res", "health", "grievance"])
            if kind == "res":
                nested.append(_nested("modify_resource", resource_id=rng.choice(rids), delta=rng.randint(-45, 45)))
            elif kind == "health":
                target = rng.choice([*(str(a.id) for a in colony.agents), "player"])
                nested.append(_nested("modify_health", target_id=target, delta=rng.randint(-70, 20)))
            else:
                target = str(rng.choice(colony.agents).id)
                nested.append(_nested("adjust_grievance", target_id=target, delta=rng.randint(-3, 3)))
        calls.append(_propose(agent.id, f"Plan {i}", flavors[i % 3], nested))
    return calls


def test_property_preview_equals_resolve_outcome() -> None:
    """Spec §7 property test: what is previewed is what resolve applies."""
    rng = random.Random(20261015)
    for setting_id in ("arctic_station_v1", "desert_outpost_v1", "orbital_colony_v1"):
        setting = load_setting(setting_id)
        for seed in range(15):
            colony = _colony(setting_id, seed).model_copy(update={"legitimacy": rng.randint(0, 100)})
            prepared = prepare_proposals(colony, _random_turn(rng, colony), setting)
            for picked in prepared.proposals:
                derived = engine.derive_resolution_consequences(prepared.state, picked, prepared.proposals)
                calls = engine.stamp_phase([*picked.tool_calls_on_pick, *derived], "resolve")
                after = engine.apply_tool_calls(prepared.state, calls)
                recorded = after.turn_tool_calls[len(prepared.state.turn_tool_calls):]
                assert all(tc.rejection_reason is None for tc in recorded), recorded
                rendered = render_effects(setting, outcome_from_calls(recorded, prepared.state))
                assert rendered == picked.effects_preview, (setting_id, seed, picked.name)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_prepare.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.agent.prepare'`

- [ ] **Step 3: Implement `backend/app/agent/prepare.py`**

```python
"""Turn preparation between the debate and the pause (spec §4.1).

Shared by `nodes.count_valid_proposals` (retry decision, throwaway result)
and the `apply_tool_calls` node (kept result). Pure.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

from app.agent.narration_guard import build_allowed_terms, sanitize_risk_note
from app.game import engine
from app.game.preview import render_effects, simulate_option
from app.game.schemas import ColonyState, Proposal, ToolCall
from app.game.setting_loader import Setting

DEBATE_TOOL_NAMES: frozenset[str] = frozenset({"propose_action"})
SURFACED_PROPOSALS = 3

ProposalClass = Literal["valid", "degraded", "no_effect"]
_CLASS_ORDER: dict[str, int] = {"valid": 0, "degraded": 1, "no_effect": 2}


@dataclass(frozen=True)
class PreparedTurn:
    state: ColonyState
    proposals: list[Proposal]
    classes: list[ProposalClass]
    valid_count: int
    risk_notes_dropped: int


def prepare_proposals(
    state: ColonyState,
    debate_tool_calls: list[ToolCall],
    setting: Setting,
    extra_texts: Iterable[str] = (),
) -> PreparedTurn:
    folded = state
    for tc in debate_tool_calls:
        stamped = tc.model_copy(update={"phase": "debate"})
        if tc.name in DEBATE_TOOL_NAMES:
            folded = engine.apply_tool_call(folded, stamped)
        else:
            folded = engine.record_rejected(folded, stamped, "not_allowed_in_debate")

    new_proposals = folded.turn_proposals[len(state.turn_proposals) :]
    candidates: list[tuple[int, Proposal, ProposalClass]] = []
    records: list[ToolCall] = []
    for idx, proposal in enumerate(new_proposals):
        kept, proposal_records, changed = _strip(folded, proposal)
        records += proposal_records
        stripped = len(kept) < len(proposal.tool_calls_on_pick)
        cls: ProposalClass = "no_effect" if not changed else "degraded" if stripped else "valid"
        candidates.append((idx, proposal.model_copy(update={"tool_calls_on_pick": kept}), cls))

    selected = sorted(candidates, key=lambda c: (_CLASS_ORDER[c[2]], c[0]))[:SURFACED_PROPOSALS]
    surfaced = [p for _, p, _ in selected]

    allowed = build_allowed_terms(
        setting,
        folded,
        [*extra_texts, *(t for p in new_proposals for t in (p.name, p.description))],
    )
    roster = frozenset(a.name for a in folded.agents)
    rendered: list[Proposal] = []
    dropped = 0
    for proposal in surfaced:
        outcome = simulate_option(folded, proposal, surfaced)
        note = sanitize_risk_note(proposal.risk_note, setting, allowed, roster)
        if proposal.risk_note and note is None:
            dropped += 1
        rendered.append(
            proposal.model_copy(
                update={"effects_preview": render_effects(setting, outcome), "risk_note": note}
            )
        )

    out_state = folded.model_copy(
        update={
            "turn_proposals": rendered,
            "turn_tool_calls": [*folded.turn_tool_calls, *records],
        }
    )
    return PreparedTurn(
        state=out_state,
        proposals=rendered,
        classes=[c for _, _, c in selected],
        valid_count=sum(1 for _, _, c in candidates if c == "valid"),
        risk_notes_dropped=dropped,
    )


def _strip(state: ColonyState, proposal: Proposal) -> tuple[list[ToolCall], list[ToolCall], bool]:
    """Apply nested calls one by one; drop rejects. Returns (kept, records, changed)."""
    running = state
    kept: list[ToolCall] = []
    records: list[ToolCall] = []
    changed = False
    for nested in proposal.tool_calls_on_pick:
        after = engine.apply_tool_call(running, nested)
        logged = after.turn_tool_calls[-1]
        if logged.rejection_reason is not None:
            records.append(
                logged.model_copy(
                    update={"phase": "prepare", "rejection_reason": f"stripped:{logged.rejection_reason}"}
                )
            )
            continue
        kept.append(nested)
        changed = changed or nested.name == "set_advisor_status" or bool(logged.applied_delta)
        records.append(logged.model_copy(update={"phase": "prepare", "applied_delta": None}))
        running = after
    return kept, records, changed
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_prepare.py -q`
Expected: PASS. If the property test fails, print the failing case; the cause is a renderer/recorder mismatch in Task 3, fix it there (add a regression test to `test_preview.py`), never in the property test.

- [ ] **Step 5: Run the full gates and commit**

```bash
git add backend/app/agent/prepare.py backend/tests/test_prepare.py
git commit -m "feat(agent): prepare_proposals strips, classifies, selects and renders options

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Graph wiring, tool schema and prompt fixes

**Files:**
- Modify: `backend/app/agent/state.py` (add `turn_meta`)
- Modify: `backend/app/agent/nodes.py` (`generate_event`, `count_valid_proposals`, `agent_debate`, `apply_tool_calls`, `resolve`, `autonomous_actions`)
- Modify: `backend/app/game/tools.py` (`propose_action` schema)
- Modify: `backend/app/agent/prompts.py` (preamble sections at lines ~87-155 and ~179; `build_debate_user_message`)
- Test: `backend/tests/test_nodes.py`, `backend/tests/test_prompts.py`, `backend/tests/test_tools.py`

**Interfaces:**
- Consumes: `prepare.prepare_proposals`, `prepare.PreparedTurn` (Task 5); `preview.compact_state` (Task 3); `engine.stamp_phase` (Task 2).
- Produces:
  - `GameTurnState.turn_meta: dict[str, Any]` with keys written so far: `debate_retried: bool` (agent_debate), `proposal_classes: list[str]`, `risk_notes_dropped: int`, `pre_pick: dict` (apply_tool_calls). Task 8 adds `narration`.
  - `nodes.count_valid_proposals(colony_state, tool_calls, setting=None, extra_texts=()) -> int`
  - Every call in `colony_state.turn_tool_calls` carries a `phase`.

- [ ] **Step 1: Write the failing node tests** (`backend/tests/test_nodes.py`)

Replace `test_apply_tool_calls_separates_applied_from_rejected` (line ~674; its ambient `modify_resource` is no longer applied) with:

```python
async def test_apply_tool_calls_separates_applied_from_rejected():
    colony = make_colony_state()
    good = ToolCall(
        name="propose_action",
        caller_id=colony.agents[0].id,
        args={
            "name": "Patch",
            "description": "Patch it.",
            "flavor": "DOMINANT",
            "tool_calls_on_pick": [
                {"name": "modify_resource", "args": {"resource_id": "fuel", "delta": -5, "reason": "patching"}}
            ],
        },
    )
    bad = ToolCall(name="propose_action", caller_id=colony.agents[1].id, args={"name": "Nope", "flavor": "MORAL"})
    result = await apply_tool_calls({"colony_state": colony, "debate_tool_calls": [good, bad]})
    assert [tc.args["name"] for tc in result["applied_tool_calls"]] == ["Patch"]
    assert [tc.rejection_reason for tc in result["rejected_tool_calls"]] == ["missing_description"]
```

Add:

```python
async def test_apply_node_rejects_ambient_debate_calls():
    colony = make_colony_state()
    ambient = ToolCall(
        name="modify_resource",
        caller_id=colony.agents[0].id,
        args={"resource_id": "fuel", "delta": -5, "reason": "storm"},
    )
    result = await apply_tool_calls({"colony_state": colony, "debate_tool_calls": [ambient]})
    assert result["colony_state"].resources["fuel"].value == 70
    rejected = result["rejected_tool_calls"]
    assert [(tc.phase, tc.rejection_reason) for tc in rejected] == [("debate", "not_allowed_in_debate")]


def _patch_call(colony: ColonyState, **extra: Any) -> ToolCall:
    return ToolCall(
        name="propose_action",
        caller_id=colony.agents[0].id,
        args={
            "name": "Patch",
            "description": "Patch it.",
            "flavor": "DOMINANT",
            "tool_calls_on_pick": [
                {"name": "modify_resource", "args": {"resource_id": "fuel", "delta": -5, "reason": "p"}}
            ],
            **extra,
        },
    )


async def test_apply_node_renders_preview_and_ignores_llm_preview():
    colony = make_colony_state()
    call = _patch_call(colony, effects_preview=["+99 legitimacy"])
    result = await apply_tool_calls({"colony_state": colony, "debate_tool_calls": [call]})
    assert result["proposals"][0].effects_preview == ["fuel −5"]
    assert result["colony_state"].turn_proposals[0].effects_preview == ["fuel −5"]


async def test_apply_node_writes_turn_meta():
    colony = make_colony_state()
    result = await apply_tool_calls(
        {"colony_state": colony, "debate_tool_calls": [_patch_call(colony)], "turn_meta": {"debate_retried": False}}
    )
    meta = result["turn_meta"]
    assert meta["debate_retried"] is False
    assert meta["proposal_classes"] == ["valid"]
    assert meta["risk_notes_dropped"] == 0
    assert meta["pre_pick"]["legitimacy"] == colony.legitimacy


async def test_resolve_stamps_resolve_phase():
    colony = make_colony_state()
    applied = await apply_tool_calls({"colony_state": colony, "debate_tool_calls": [_patch_call(colony)]})
    before = applied["colony_state"]
    out = await resolve(
        {
            "colony_state": before,
            "proposals": applied["proposals"],
            "player_choice": applied["proposals"][0].id,
            "current_event": _make_event(severity=1),
        }
    )
    new_calls = out["colony_state"].turn_tool_calls[len(before.turn_tool_calls):]
    assert new_calls and all(tc.phase == "resolve" for tc in new_calls)


async def test_autonomous_stamps_autonomy_phase(monkeypatch):
    colony = make_colony_state()
    remember = {
        "name": "remember",
        "input": {"fact": "The pump held.", "caller_agent_id": str(colony.agents[0].id)},
    }
    _install_fake(monkeypatch, [_debate_message(colony, [remember])])
    out = await autonomous_actions({"colony_state": colony, "narration": "x", "cost_cents_this_turn": 0.0})
    assert out["colony_state"].turn_tool_calls[-1].phase == "autonomy"
```

Add `autonomous_actions` and `resolve` to the `from app.agent.nodes import (...)` list if they are not already imported.

In `test_agent_debate_no_retry_when_three_valid` add `assert result["turn_meta"]["debate_retried"] is False`; in `test_agent_debate_retries_once_and_uses_better_retry` add `assert result["turn_meta"]["debate_retried"] is True`.

- [ ] **Step 2: Write the failing prompt and schema tests**

Append to `backend/tests/test_prompts.py`:

```python
import re

from app.agent import prompts
from app.game.schemas import Event, EventOrigin
from app.game.setting_loader import list_available_settings, load_setting


def test_preamble_has_no_llm_preview_contract() -> None:
    text = prompts.build_preamble_block()["text"]
    assert "effects_preview" not in text
    assert '"fuel"' not in text
    assert "legitimacy-positive" not in text
    assert "legitimacy-negative" not in text
    assert "clamps values at 0 and max" not in text
    assert "Standalone modify_resource" not in text
    assert "risk_note" in text


def test_no_prompt_text_contains_a_name_pool_name() -> None:
    names = {
        n
        for sid in list_available_settings()
        for a in load_setting(sid).advisor_archetypes
        for n in a.name_pool
    }
    texts = [v for k, v in vars(prompts).items() if k.isupper() and isinstance(v, str)]
    texts.append(prompts.build_preamble_block()["text"])
    for text in texts:
        for name in names:
            assert not re.search(rf"\b{re.escape(name)}\b", text), name


def test_debate_user_message_drops_preview_instruction() -> None:
    event = Event(id="e", category="c", title="T", body="B", severity=1, origin=EventOrigin.NATURAL)
    message = prompts.build_debate_user_message(event)
    assert "effects_preview" not in message
    assert "no numbers in name or description" in message
```

Append to `backend/tests/test_tools.py`:

```python
def test_propose_action_schema_offers_risk_note_not_preview() -> None:
    from app.game.tools import AGENT_TOOL_SCHEMAS

    schema = next(t for t in AGENT_TOOL_SCHEMAS if t["name"] == "propose_action")
    props = schema["input_schema"]["properties"]
    assert "effects_preview" not in props
    assert props["risk_note"]["type"] == "string"
    assert props["risk_note"]["maxLength"] == 120
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_nodes.py tests/test_prompts.py tests/test_tools.py -q`
Expected: FAIL (ambient call still applied, LLM preview still shown, no `turn_meta`, prompts unchanged).

- [ ] **Step 4: Add `turn_meta` to `GameTurnState`** (`backend/app/agent/state.py`)

Add `from typing import Any` and, before `is_run_over`:

```python
    # Per-turn integrity metadata persisted to turn_logs.turn_meta (spec §4.4).
    # Reset at generate_event; each node merges its keys into a copy.
    turn_meta: dict[str, Any]
```

- [ ] **Step 5: Wire the nodes** (`backend/app/agent/nodes.py`)

1. Imports: add `from collections.abc import Iterable`, `from app.agent.prepare import prepare_proposals`, `from app.game.preview import compact_state`, and `from app.game.setting_loader import Setting`.

2. `generate_event`: add `"turn_meta": {},` to the returned reset dict.

3. Replace `count_valid_proposals`:

```python
def count_valid_proposals(
    colony_state: ColonyState,
    tool_calls: list[ToolCall],
    setting: Setting | None = None,
    extra_texts: Iterable[str] = (),
) -> int:
    """Proposals the preparation step classifies `valid` (spec §4.1).

    Runs `prepare_proposals` on a throwaway result: nothing it records is
    kept, so a discarded first debate leaves no trace in the turn record.
    """
    setting = setting or setting_loader.load_setting(colony_state.setting_id)
    return prepare_proposals(colony_state, tool_calls, setting, extra_texts).valid_count
```

4. In `agent_debate`: load `setting = setting_loader.load_setting(colony_state.setting_id)` once; call `count_valid_proposals(colony_state, parsed.tool_calls, setting, parsed.per_agent_text.values())` (and the same with `retry_parsed`); track `retried = False`, set `retried = True` inside the retry branch; add to the return dict:

```python
        "turn_meta": {**(state.get("turn_meta") or {}), "debate_retried": retried},
```

5. Replace the `apply_tool_calls` node body:

```python
    colony_state = state["colony_state"]
    setting = setting_loader.load_setting(colony_state.setting_id)
    pre_tc_len = len(colony_state.turn_tool_calls)

    prepared = prepare_proposals(
        colony_state,
        state["debate_tool_calls"],
        setting,
        (state.get("debate_per_agent_text") or {}).values(),
    )

    newly_logged = prepared.state.turn_tool_calls[pre_tc_len:]
    applied = [tc for tc in newly_logged if tc.phase == "debate" and tc.rejection_reason is None]
    rejected = [tc for tc in newly_logged if tc.rejection_reason is not None]

    return {
        "colony_state": prepared.state,
        "applied_tool_calls": applied,
        "rejected_tool_calls": rejected,
        "proposals": prepared.proposals,
        "turn_meta": {
            **(state.get("turn_meta") or {}),
            "proposal_classes": list(prepared.classes),
            "risk_notes_dropped": prepared.risk_notes_dropped,
            "pre_pick": compact_state(prepared.state),
        },
    }
```

Update its docstring: debate calls are whitelisted to `propose_action`, proposals are prepared (stripped, classified, selected, rendered) by `app.agent.prepare`.

6. `resolve`: replace the `engine.apply_tool_calls(colony_state, [*picked.tool_calls_on_pick, *derived])` argument with `engine.stamp_phase([*picked.tool_calls_on_pick, *derived], "resolve")`.

7. `autonomous_actions`: replace `engine.apply_tool_calls(colony_state, autonomous_tool_calls)` with `engine.apply_tool_calls(colony_state, engine.stamp_phase(autonomous_tool_calls, "autonomy"))`.

- [ ] **Step 6: Update the tool schema** (`backend/app/game/tools.py`, `propose_action`)

Replace the `effects_preview` property with:

```python
                "risk_note": {
                    "type": "string",
                    "maxLength": 120,
                    "description": (
                        "Optional. One short line hinting at what could go wrong. "
                        "No numbers. The engine shows the player the exact effects."
                    ),
                },
```

and the tool `description` with `"Add a structured action to the player's choice pool for this turn; supply name, description, flavor, tool_calls_on_pick, and optionally a risk_note."`

- [ ] **Step 7: Fix the prompts** (`backend/app/agent/prompts.py`)

Keep the preamble roughly the same length (the Anthropic cache minimum is 4096 tokens cumulative; see `backend/app/agent/CLAUDE.md`). Replace the `AVAILABLE TOOLS (debate phase):` block through the end of the `DEBATE TARGETS` block with:

```text
AVAILABLE TOOLS (debate phase):
  propose_action       — add one DOMINANT / MORAL / WILDCARD choice to the
                         player's three-option pool for this turn. Include
                         a short name (≤40 chars), a description (≤200
                         chars), an optional risk_note (one short line on
                         what could go wrong, no numbers), AND a
                         tool_calls_on_pick list (up to 5 nested calls)
                         that fire if the player picks this proposal.
                         The nested calls are how your proposal moves
                         the world. See PROPOSAL EFFECTS below.
  propose_action is the only tool the debate phase applies. Anything else
  you emit during the debate is discarded unseen. Every effect goes inside
  a proposal's tool_calls_on_pick.

NESTED TOOLS (only inside tool_calls_on_pick):
  modify_resource      — shift a colony resource by a signed integer delta
                         bounded to [-50, +50]. Use only the resource ids
                         listed under `resources:` in <colony_state>; any
                         other id is rejected. Gains above max are capped;
                         spending more than the colony has is rejected.
                         Legitimacy, health and grievance are not
                         resources; the engine owns them.
  modify_health        — change an agent's or the player's health by a
                         signed delta. target_id is an agent UUID or the
                         literal string "player". Damage below 0 is
                         rejected if the target is already dead.
  adjust_grievance     — shift another agent's grievance in [-3, +3]. Use
                         sparingly and only when justified by the event —
                         the engine caps at ±3 per call.
  set_advisor_status   — detain, dismiss or restore an advisor. target_id
                         is an advisor UUID, new_status is ACTIVE,
                         DETAINED or DISMISSED, plus a reason. At most one
                         per proposal.

PROPOSAL EFFECTS (how a proposal mutates state when picked):
  Every propose_action MUST include `tool_calls_on_pick` — a list of
  structured tool calls describing exactly what changes if the player
  picks your proposal. Each item has shape:
      {"name": "<tool>", "args": {<args mirroring the nested tool>}}
  Do NOT include `caller_agent_id` inside `args` — these calls are
  engine-emitted at pick time. The engine computes what the player sees
  from these calls, so every effect size goes here and nowhere else:
  never write numbers in `name` or `description`.

  Example propose_action input (resource ids are placeholders; use the
  ids from <colony_state>):
      {
        "name": "Ration the stores",
        "description": "Cut every share by a third until the next resupply.",
        "flavor": "DOMINANT",
        "risk_note": "People will notice who still eats well.",
        "tool_calls_on_pick": [
          {"name": "modify_resource",
           "args": {"resource_id": "<resource id>", "delta": 12, "reason": "smaller shares"}},
          {"name": "modify_resource",
           "args": {"resource_id": "<another resource id>", "delta": -5, "reason": "resentment"}}
        ],
        "caller_agent_id": "<your-uuid>"
      }

DEBATE TARGETS (per-turn, aspirational — the engine caps at three):
  * Exactly three `propose_action` calls across the living advisors,
    one per flavor: one DOMINANT (the pragmatic / hard-edged option,
    resource-efficient but hard on people), one MORAL (principled /
    costly, often resource-negative), one WILDCARD (unorthodox / risky,
    high variance outcomes).
  * If four advisors are alive, one of them sits this turn out rather
    than duplicating a flavor. Pick the advisor whose axes least match
    the available flavor left.
```

`speak_to_agent` and `adjust_relationship` were listed as debate tools; they now only apply off-turn, and the `OFF-TURN (AUTONOMOUS) PHASE` section already describes both, so the replacement block above simply drops them.

In the VOICE section, replace the parenthetical `(for example "Ilya finds the message in the queue")` with `(for example, the event says you found the message yourself)`.

In `build_debate_user_message`, replace the sentence fragment from `every propose_action MUST include` to the end with:

```python
        "propose_action MUST include `tool_calls_on_pick` (1-5 nested "
        "modify_resource / modify_health / adjust_grievance calls); put no "
        "numbers in name or description; every tool_use MUST set "
        "`caller_agent_id` to your UUID."
```

Run `grep -n "effects_preview\|ambient" backend/app/agent/prompts.py` and remove any remaining instruction that asks the model for previews or ambient calls.

- [ ] **Step 8: Run the tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_nodes.py tests/test_prompts.py tests/test_tools.py tests/test_graph.py -q`
Expected: PASS. Then the full gates. Expected fallout to fix in this task: tests that asserted an LLM-written `effects_preview` on node output, tests that relied on an ambient debate `modify_resource`, and `tests/fixtures/_factories.py` bodies (they already carry nested calls; only remove assertions on preview text).

- [ ] **Step 9: Commit**

```bash
git add backend/app/agent/state.py backend/app/agent/nodes.py backend/app/game/tools.py backend/app/agent/prompts.py backend/tests/test_nodes.py backend/tests/test_prompts.py backend/tests/test_tools.py
git commit -m "feat(agent): debate applies proposals only; engine-prepared options; phase tags; prompt fixes

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Persist the turn record (`turn_logs.tool_calls`, `turn_logs.turn_meta`) and the validity metric

**Files:**
- Create: `backend/alembic/versions/a7c3e9d41f20_turn_logs_tool_calls_and_meta.py`
- Modify: `backend/app/game/models.py` (`TurnLog`)
- Modify: `backend/app/agent/nodes.py::advance_turn`
- Modify: `backend/app/eval/tool_validity.py`
- Modify: `backend/tests/fixtures/run_simulator.py::_tool_calls_from_turn_log` and its module docstring
- Test: `backend/tests/test_nodes.py` (DB), `backend/tests/test_eval_engine_metrics.py` or new `backend/tests/test_tool_validity.py`, `backend/tests/fixtures/test_run_simulator_units.py`

**Interfaces:**
- Consumes: `engine.close_turn` (Task 2), `GameTurnState.turn_meta` (Task 6).
- Produces:
  - `TurnLog.tool_calls: list | None`, `TurnLog.turn_meta: dict | None`
  - `tool_validity.LLM_PHASES = frozenset({"debate", "prepare", "autonomy"})`
  - `tool_validity_rate(tool_calls)` counts calls with `phase is None or phase in LLM_PHASES`
  - `tool_validity_breakdown(tool_calls)` unchanged signature; new `tool_validity_by_phase(tool_calls) -> dict[str, dict[str, int]]`

- [ ] **Step 1: Confirm the local migration head**

Run: `cd backend && DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:54322/postgres uv run alembic current`
Expected: `3ba6ba8568f0 (head)`. If it differs, stop and report; do not stamp or downgrade.

- [ ] **Step 2: Write the failing tests**

`backend/tests/test_tool_validity.py` (new):

```python
from app.eval.tool_validity import tool_validity_by_phase, tool_validity_rate
from app.game.schemas import ToolCall


def _tc(phase: str | None, rejected: bool = False) -> ToolCall:
    return ToolCall(
        name="modify_resource",
        caller_id=None,
        args={},
        phase=phase,
        rejection_reason="stripped:unknown_resource" if rejected else None,
    )


def test_rate_counts_llm_phases_only() -> None:
    calls = [
        _tc("debate"),
        _tc("prepare", rejected=True),
        _tc("prepare"),
        _tc("autonomy"),
        _tc("resolve"),
        _tc("close"),
    ]
    assert tool_validity_rate(calls) == 3 / 4


def test_legacy_calls_without_phase_still_count() -> None:
    assert tool_validity_rate([_tc(None), _tc(None, rejected=True)]) == 0.5


def test_by_phase_breakdown() -> None:
    out = tool_validity_by_phase([_tc("prepare", rejected=True), _tc("prepare"), _tc("close")])
    assert out == {"prepare": {"valid": 1, "rejected": 1}, "close": {"valid": 1, "rejected": 0}}
```

Append to `backend/tests/fixtures/test_run_simulator_units.py`:

```python
def test_tool_calls_from_turn_log_prefers_new_column() -> None:
    from types import SimpleNamespace

    from tests.fixtures.run_simulator import _tool_calls_from_turn_log

    record = [
        {"name": "modify_legitimacy", "caller_id": None, "args": {"delta": -1, "reason": "end of turn"},
         "phase": "close", "applied_delta": -1},
        {"name": "modify_resource", "caller_id": None, "args": {"resource_id": "water"},
         "phase": "prepare", "rejection_reason": "stripped:unknown_resource"},
    ]
    row = SimpleNamespace(tool_calls=record, chosen={}, proposals=[], autonomous_actions=[])
    calls = _tool_calls_from_turn_log(row)
    assert [tc.phase for tc in calls] == ["close", "prepare"]
    assert calls[1].rejection_reason == "stripped:unknown_resource"


def test_tool_calls_from_turn_log_falls_back_when_column_null() -> None:
    from types import SimpleNamespace

    from tests.fixtures.run_simulator import _tool_calls_from_turn_log

    row = SimpleNamespace(tool_calls=None, chosen={}, proposals=[], autonomous_actions=[])
    assert _tool_calls_from_turn_log(row) == []
```

Append to `backend/tests/test_nodes.py` (DB section):

```python
async def test_advance_turn_persists_tool_calls_and_turn_meta(persisted_colony):
    colony = persisted_colony
    event = _make_event(severity=2, event_id="evt_rec")
    colony = colony.model_copy(update={"current_event": event})
    meta = {"debate_retried": True, "proposal_classes": ["valid"], "risk_notes_dropped": 0}
    state: dict[str, Any] = {
        "game_id": str(colony.game_id),
        "user_id": str(colony.user_id),
        "colony_state": colony,
        "current_event": event,
        "proposals": [_make_proposal(pid="p_rec", flavor=ProposalFlavor.DOMINANT)],
        "player_choice": "p_rec",
        "narration": "It held.",
        "cost_cents_this_turn": 1.0,
        "turn_meta": meta,
    }
    result = await advance_turn(state)
    assert result["colony_state"].legitimacy == colony.legitimacy - 1

    async with async_session_factory() as s:
        row = (
            await s.execute(
                text("SELECT tool_calls, turn_meta FROM turn_logs WHERE game_id = :gid"),
                {"gid": colony.game_id},
            )
        ).one()
    close = [tc for tc in row.tool_calls if tc["phase"] == "close"]
    assert close and close[0]["name"] == "modify_legitimacy" and close[0]["applied_delta"] == -1
    assert row.turn_meta == meta


async def test_advance_turn_without_turn_meta(persisted_colony):
    """A game paused before this change resumes without turn_meta (Review Focus 5)."""
    colony = persisted_colony
    event = _make_event(severity=2, event_id="evt_old")
    colony = colony.model_copy(update={"current_event": event})
    state: dict[str, Any] = {
        "game_id": str(colony.game_id),
        "user_id": str(colony.user_id),
        "colony_state": colony,
        "current_event": event,
        "proposals": [],
        "player_choice": None,
        "narration": None,
        "cost_cents_this_turn": 0.0,
    }
    await advance_turn(state)
    async with async_session_factory() as s:
        row = (
            await s.execute(
                text("SELECT turn_meta FROM turn_logs WHERE game_id = :gid"), {"gid": colony.game_id}
            )
        ).one()
    assert row.turn_meta == {}
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `cd backend && DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:54322/postgres uv run pytest tests/test_tool_validity.py tests/fixtures/test_run_simulator_units.py tests/test_nodes.py -q -k "validity or turn_log or turn_meta"`
Expected: FAIL (`tool_validity_by_phase` missing; `tool_calls` column missing).

- [ ] **Step 4: Add the columns and migration**

In `backend/app/game/models.py::TurnLog`, after `turn_cost_cents`:

```python
    # Full phase-tagged tool-call record for the turn (spec §4.4). NULL on
    # rows written before 2026-10. Mapping these columns means every
    # select(TurnLog) needs the migration applied first.
    tool_calls: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    # Debate retry, proposal classes, pre-pick stats, narration outcome.
    turn_meta: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
```

`backend/alembic/versions/a7c3e9d41f20_turn_logs_tool_calls_and_meta.py`:

```python
"""turn_logs tool_calls and turn_meta

Revision ID: a7c3e9d41f20
Revises: 3ba6ba8568f0
Create Date: 2026-10-15

Additive and nullable. Must run on prod BEFORE the backend that maps these
columns deploys: every select(TurnLog) selects them.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a7c3e9d41f20"
down_revision: str | Sequence[str] | None = "3ba6ba8568f0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("turn_logs", sa.Column("tool_calls", postgresql.JSONB(), nullable=True))
    op.add_column("turn_logs", sa.Column("turn_meta", postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("turn_logs", "turn_meta")
    op.drop_column("turn_logs", "tool_calls")
```

Apply and round-trip locally:

```bash
cd backend
DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:54322/postgres uv run alembic upgrade head
DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:54322/postgres uv run alembic downgrade -1
DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:54322/postgres uv run alembic upgrade head
```

Expected: all three succeed; `alembic current` shows `a7c3e9d41f20 (head)`.

- [ ] **Step 5: Persist in `advance_turn`** (`backend/app/agent/nodes.py`)

Replace the two lines Task 2 added with:

```python
    closed = engine.close_turn(colony_state)
    turn_record = [tc.model_dump(mode="json") for tc in closed.turn_tool_calls]
    new_colony = engine.advance_turn(closed)
```

Add to the `TurnLog(...)` constructor:

```python
            tool_calls=turn_record,
            turn_meta=dict(state.get("turn_meta") or {}),
```

Update the node docstring: decay is `engine.close_turn` (phase `close`), and the full call record plus `turn_meta` are persisted.

- [ ] **Step 6: Phase-aware validity** (`backend/app/eval/tool_validity.py`)

```python
LLM_PHASES: frozenset[str] = frozenset({"debate", "prepare", "autonomy"})
"""Phases whose calls the LLM authored. `resolve` and `close` calls are
engine-derived and valid by construction; counting them would dilute the
gate. Calls with phase None predate phase tags and still count."""


def _counted(tc: ToolCall) -> bool:
    return tc.phase is None or tc.phase in LLM_PHASES


def tool_validity_rate(tool_calls: list[ToolCall]) -> float:
    """Fraction of LLM-originated calls with `rejection_reason is None`.

    An empty (or engine-only) list returns 1.0.
    """
    counted = [tc for tc in tool_calls if _counted(tc)]
    if not counted:
        return 1.0
    return sum(1 for tc in counted if tc.rejection_reason is None) / len(counted)


def tool_validity_by_phase(tool_calls: list[ToolCall]) -> dict[str, dict[str, int]]:
    """{phase: {"valid": n, "rejected": m}} over every call, engine phases included."""
    out: dict[str, dict[str, int]] = {}
    for tc in tool_calls:
        bucket = out.setdefault(tc.phase or "unknown", {"valid": 0, "rejected": 0})
        bucket["rejected" if tc.rejection_reason else "valid"] += 1
    return out
```

Keep `tool_validity_breakdown` as is.

- [ ] **Step 7: Read the new column in the simulator** (`backend/tests/fixtures/run_simulator.py`)

At the top of `_tool_calls_from_turn_log`:

```python
    if getattr(row, "tool_calls", None) is not None:
        return [ToolCall.model_validate(tc) for tc in row.tool_calls]
```

Rewrite its docstring: rows written since 2026-10 carry the full phase-tagged record in `turn_logs.tool_calls`; older rows fall back to the reconstruction below (picked `tool_calls_on_pick` + `autonomous_actions`, which under-counts rejects). Update the module docstring's "Tool-call provenance" paragraph the same way.

- [ ] **Step 8: Run the tests to verify they pass**

Run the Step 3 command again. Expected: PASS. Then the full gates.

- [ ] **Step 9: Commit**

```bash
git add backend/alembic/versions/a7c3e9d41f20_turn_logs_tool_calls_and_meta.py backend/app/game/models.py backend/app/agent/nodes.py backend/app/eval/tool_validity.py backend/tests/fixtures/run_simulator.py backend/tests/test_tool_validity.py backend/tests/fixtures/test_run_simulator_units.py backend/tests/test_nodes.py
git commit -m "feat(db): persist phase-tagged tool calls and turn_meta per turn

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: Narration: own system block, guard, retry, fallback

**Files:**
- Create: `backend/app/agent/narration.py`
- Modify: `backend/app/agent/nodes.py::narrate_turn`
- Test: `backend/tests/test_narration.py` (new), `backend/tests/test_nodes.py` (narrate tests at lines ~1137-1196)

**Interfaces:**
- Consumes: `narration_guard.validate_narration`, `build_allowed_terms`, `Violation` (Task 4); `Setting.player_title` (Task 1); `GameTurnState.turn_meta` (Task 6).
- Produces:
  - `narration.build_narration_system_block(setting: Setting, agents: list[Agent]) -> dict` (cache_control ephemeral)
  - `narration.build_narration_user_message(event: Event | None, picked: Proposal | None, proposer_name: str | None, dead_names: list[str]) -> str`
  - `narration.RETRY_NOTE: str` (format key `codes`)
  - `narration.fallback_line(picked: Proposal | None, proposer_name: str | None) -> str`
  - `turn_meta["narration"] = {"source": "llm"|"retry"|"fallback"|"cost_cap", "attempts": int, "first_attempt_violations": list[str], "kept_violations": list[str]}`

- [ ] **Step 1: Write the failing builder tests** (`backend/tests/test_narration.py`)

```python
from uuid import UUID

from app.agent.narration import (
    build_narration_system_block,
    build_narration_user_message,
    fallback_line,
)
from app.game.generator import generate_run
from app.game.schemas import Event, EventOrigin, Proposal, ProposalFlavor
from app.game.setting_loader import load_setting

DESERT = load_setting("desert_outpost_v1")
COLONY = generate_run(
    user_id=UUID("bbbbbbbb-0000-0000-0000-000000000002"), setting_id="desert_outpost_v1", seed=3
)
PICKED = Proposal(
    id="p", proposed_by_agent_id=COLONY.agents[0].id, flavor=ProposalFlavor.MORAL,
    name="Share the water", description="Open the tank to the caravan.",
    effects_preview=["water −10", "legitimacy −2 (overrules Cal)"], tool_calls_on_pick=[],
)
EVENT = Event(id="e", category="c", title="Caravan wants water", body="They wait by the pump.",
              severity=2, origin=EventOrigin.NATURAL)


def test_system_block_is_setting_specific_and_cached() -> None:
    block = build_narration_system_block(DESERT, COLONY.agents)
    assert block["cache_control"] == {"type": "ephemeral"}
    text = block["text"]
    assert DESERT.name in text and "Warden" in text
    for agent in COLONY.agents:
        assert f"{agent.name}, {agent.role}, alive" in text
    assert "<agent" not in text


def test_user_message_carries_full_event_choice_and_effects() -> None:
    msg = build_narration_user_message(EVENT, PICKED, COLONY.agents[0].name, ["Cal"])
    assert "<title>Caravan wants water</title>" in msg
    assert "<body>They wait by the pump.</body>" in msg
    assert f'<choice proposer="{COLONY.agents[0].name}" flavor="MORAL">Share the water: Open the tank to the caravan.</choice>' in msg
    assert "<effects>water −10; legitimacy −2 (overrules Cal)</effects>" in msg
    assert "<dead>Cal</dead>" in msg


def test_fallback_lines() -> None:
    assert fallback_line(PICKED, "Sol") == "You backed Sol: Share the water."
    assert fallback_line(PICKED, None) == "You chose: Share the water."
    assert fallback_line(None, None) == "The day closes."
```

- [ ] **Step 2: Write the failing node tests** (`backend/tests/test_nodes.py`; replace the three `test_narrate_turn_*` tests)

```python
def _narrate_state(colony: ColonyState, cost: float = 1.0) -> dict[str, Any]:
    picked = _make_proposal(pid="p_n", flavor=ProposalFlavor.DOMINANT).model_copy(
        update={"proposed_by_agent_id": colony.agents[0].id, "name": "Hold the line"}
    )
    return {
        "colony_state": colony,
        "cost_cents_this_turn": cost,
        "current_event": _make_event(),
        "proposals": [picked],
        "player_choice": "p_n",
        "turn_meta": {"debate_retried": False},
    }


@respx.mock
async def test_narrate_turn_skips_call_when_over_budget():
    colony = make_colony_state()
    route = respx.post(_ANTHROPIC_URL)
    result = await narrate_turn(_narrate_state(colony, cost=float(MAX_COST_CENTS_PER_TURN) + 1.0))
    assert route.call_count == 0
    assert result["narration"] == f"You backed {colony.agents[0].name}: Hold the line."
    assert result["turn_meta"]["narration"]["source"] == "cost_cap"
    assert result["turn_meta"]["debate_retried"] is False


@respx.mock
async def test_narrate_turn_first_pass_success():
    colony = make_colony_state()
    text = "The wall held. Nadia counted the rations twice."
    route = respx.post(_ANTHROPIC_URL).mock(return_value=Response(200, json=fake_sonnet_response(text)))
    result = await narrate_turn(_narrate_state(colony))
    assert route.call_count == 1
    assert result["narration"] == text
    assert result["turn_meta"]["narration"] == {
        "source": "llm", "attempts": 1, "first_attempt_violations": [], "kept_violations": [],
    }
    assert result["cost_cents_this_turn"] > 1.0


@respx.mock
async def test_narrate_turn_retry_then_success():
    colony = make_colony_state()
    bad = "<agent name=\"Nadia\">Held.</agent>"
    good = "The wall held. Nadia slept."
    route = respx.post(_ANTHROPIC_URL).mock(
        side_effect=[Response(200, json=fake_sonnet_response(bad)), Response(200, json=fake_sonnet_response(good))]
    )
    result = await narrate_turn(_narrate_state(colony))
    assert route.call_count == 2
    assert "retry_note" in route.calls[1].request.content.decode()
    assert result["narration"] == good
    meta = result["turn_meta"]["narration"]
    assert meta["source"] == "retry" and meta["attempts"] == 2
    assert "markup" in meta["first_attempt_violations"]


@respx.mock
async def test_narrate_turn_soft_violations_keep_fewer():
    colony = make_colony_state()
    first = "You backed Nadia, and Ilyana and Borek watched."
    second = "You backed Nadia, and Ilyana watched."
    respx.post(_ANTHROPIC_URL).mock(
        side_effect=[Response(200, json=fake_sonnet_response(first)), Response(200, json=fake_sonnet_response(second))]
    )
    result = await narrate_turn(_narrate_state(colony))
    assert result["narration"] == second
    assert result["turn_meta"]["narration"]["kept_violations"] == ["unknown_name:Ilyana"]


@respx.mock
async def test_narrate_hard_violation_twice_falls_back():
    colony = make_colony_state()
    russian = "Стена выдержала. Никто не спал."
    respx.post(_ANTHROPIC_URL).mock(
        side_effect=[Response(200, json=fake_sonnet_response(russian)), Response(200, json=fake_sonnet_response(russian))]
    )
    result = await narrate_turn(_narrate_state(colony))
    assert result["narration"] == f"You backed {colony.agents[0].name}: Hold the line."
    assert result["turn_meta"]["narration"]["source"] == "fallback"


@respx.mock
async def test_narrate_turn_falls_back_on_api_error():
    colony = make_colony_state()
    respx.post(_ANTHROPIC_URL).mock(return_value=Response(500, json={"error": "boom"}))
    result = await narrate_turn(_narrate_state(colony))
    assert result["narration"] == f"You backed {colony.agents[0].name}: Hold the line."
    assert result["turn_meta"]["narration"]["source"] == "fallback"
    assert "cost_cents_this_turn" not in result
```

`_make_proposal` already exists in `test_nodes.py`; check its signature and adapt the `model_copy` if it sets `proposed_by_agent_id` differently. The SDK retries 5xx responses before raising; if the API-error test is slow, keep it (the existing test had the same behaviour).

- [ ] **Step 3: Run the tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_narration.py tests/test_nodes.py -q -k narrat`
Expected: FAIL (`app.agent.narration` missing; old fallback text).

- [ ] **Step 4: Implement `backend/app/agent/narration.py`**

```python
"""Narration prompt builders (spec §4.2).

Narration gets its own system block: the debate preamble teaches
`<agent name=...>` output and leaked it into prose. The block is stable
per run (setting + roster), so it carries cache_control (invariant #5);
it is likely under the provider's cache minimum, which is accepted.
"""

from __future__ import annotations

from typing import Any

from app.game.schemas import Agent, Event, Proposal
from app.game.setting_loader import Setting

RETRY_NOTE = (
    "\n\n<retry_note>Your last draft broke these rules: {codes}. Write it again: "
    "two or three plain English sentences, second person, naming only people on "
    "the roster.</retry_note>"
)


def build_narration_system_block(setting: Setting, agents: list[Agent]) -> dict[str, Any]:
    roster = "\n".join(
        f"  {a.name}, {a.role}, {'alive' if a.is_alive else 'dead'}" for a in agents
    )
    text = (
        f"You write the closing lines of one day at {setting.name}.\n"
        "The reader runs the colony. Address them as \"you\". When a title is "
        f"needed, they are the {setting.player_title}.\n\n"
        f"VOICE:\n{setting.narrative_voice.strip()}\n\n"
        f"ROSTER (the only people you may name):\n{roster}\n\n"
        "RULES:\n"
        "  * Two or three sentences, in English.\n"
        "  * Name only people on the roster. Anyone else is a role or a "
        "description: \"a trucker\", \"the trader's cousin\". Never invent a name.\n"
        "  * Show what the choice did. Don't repeat the numbers from <effects>.\n"
        "  * Plain prose: no tags, no headings, no lists, no notes about the task.\n"
    )
    return {"type": "text", "text": text, "cache_control": {"type": "ephemeral"}}


def build_narration_user_message(
    event: Event | None,
    picked: Proposal | None,
    proposer_name: str | None,
    dead_names: list[str],
) -> str:
    parts: list[str] = []
    if event is not None:
        parts.append(
            f"<event>\n  <title>{event.title}</title>\n  <body>{event.body}</body>\n</event>"
        )
    if picked is not None:
        proposer = f' proposer="{proposer_name}"' if proposer_name else ""
        parts.append(
            f'<choice{proposer} flavor="{picked.flavor.value}">'
            f"{picked.name}: {picked.description}</choice>"
        )
        parts.append(f"<effects>{'; '.join(picked.effects_preview) or 'none'}</effects>")
    parts.append(f"<dead>{', '.join(dead_names) if dead_names else 'none'}</dead>")
    parts.append("Close out this day.")
    return "\n".join(parts)


def fallback_line(picked: Proposal | None, proposer_name: str | None) -> str:
    if picked is None:
        return "The day closes."
    if proposer_name:
        return f"You backed {proposer_name}: {picked.name}."
    return f"You chose: {picked.name}."
```

- [ ] **Step 5: Rewrite `narrate_turn`** (`backend/app/agent/nodes.py`)

Add imports: `from app.agent import narration` and `from app.agent.narration_guard import Violation, build_allowed_terms, validate_narration`. Remove `build_preamble_block` from the narration path only (it is still used by debate and autonomy). Replace the node:

```python
_NARRATION_ATTEMPTS = 2


async def narrate_turn(state: GameTurnState) -> GameTurnState:
    """Narrate the day with its own system block, guarded (spec §4.2).

    One retry on any violation (not once the turn's cost cap is reached).
    Keep the attempt with no hard violation and the fewest soft ones; if
    none qualifies, or the call errors, use the deterministic fallback.
    """
    colony_state = state["colony_state"]
    cost_so_far = state.get("cost_cents_this_turn", 0.0)
    setting = setting_loader.load_setting(colony_state.setting_id)
    proposals = state.get("proposals") or []
    picked = next((p for p in proposals if p.id == state.get("player_choice")), None)
    agents_by_id = {a.id: a for a in colony_state.agents}
    proposer = (
        agents_by_id.get(picked.proposed_by_agent_id)
        if picked is not None and picked.proposed_by_agent_id is not None
        else None
    )
    proposer_name = proposer.name if proposer is not None else None
    fallback = narration.fallback_line(picked, proposer_name)
    meta = dict(state.get("turn_meta") or {})

    if cost_so_far >= MAX_COST_CENTS_PER_TURN:
        meta["narration"] = _narration_meta("cost_cap", [], None)
        return {"narration": fallback, "turn_meta": meta}

    event = state.get("current_event") or (
        colony_state.event_log[-1].event if colony_state.event_log else None
    )
    system_blocks = [narration.build_narration_system_block(setting, colony_state.agents)]
    dead_names = [a.name for a in colony_state.agents if not a.is_alive]
    user_message = narration.build_narration_user_message(event, picked, proposer_name, dead_names)
    allowed = build_allowed_terms(
        setting,
        colony_state,
        [
            *(state.get("debate_per_agent_text") or {}).values(),
            *(t for p in proposals for t in (p.name, p.description)),
        ],
    )
    roster = frozenset(a.name for a in colony_state.agents)

    attempts: list[tuple[str, list[Violation]]] = []
    cost = 0.0
    message = user_message
    for attempt in range(_NARRATION_ATTEMPTS):
        if attempt > 0 and cost_so_far + cost >= MAX_COST_CENTS_PER_TURN:
            break
        try:
            response = await call_narration(system_blocks, message, max_tokens=500)
        except anthropic.APIError as exc:
            logger.warning("narrate_turn: narration call failed (%s)", exc)
            break
        cost += cost_cents_of(response, settings.LLM_MODEL_NARRATION)
        text = _first_text(response)
        violations = validate_narration(text, allowed, roster)
        attempts.append((text, violations))
        if not violations:
            break
        logger.warning(
            "narration_guard_failed game=%s turn=%s attempt=%d codes=%s",
            colony_state.game_id,
            colony_state.turn_number,
            attempt + 1,
            [v.code for v in violations],
        )
        message = user_message + narration.RETRY_NOTE.format(
            codes=", ".join(v.code for v in violations)
        )

    kept = _choose_attempt(attempts)
    if kept is None:
        text, source, kept_codes = fallback, "fallback", None
    else:
        text = attempts[kept][0]
        source = "llm" if kept == 0 else "retry"
        kept_codes = [v.code for v in attempts[kept][1]]
    meta["narration"] = _narration_meta(source, attempts, kept_codes)

    result: GameTurnState = {"narration": text, "turn_meta": meta}
    if cost:
        result["cost_cents_this_turn"] = cost_so_far + cost
    return result


def _first_text(response: Any) -> str:
    for block in getattr(response, "content", None) or []:
        if getattr(block, "type", None) == "text":
            return getattr(block, "text", "") or ""
    return ""


def _choose_attempt(attempts: list[tuple[str, list[Violation]]]) -> int | None:
    """Index of the attempt with no hard violation and fewest soft ones."""
    ok = [(len(vs), i) for i, (_, vs) in enumerate(attempts) if not any(v.hard for v in vs)]
    return min(ok)[1] if ok else None


def _narration_meta(
    source: str, attempts: list[tuple[str, list[Violation]]], kept_codes: list[str] | None
) -> dict[str, Any]:
    return {
        "source": source,
        "attempts": len(attempts),
        "first_attempt_violations": [v.code for v in attempts[0][1]] if attempts else [],
        "kept_violations": kept_codes or [],
    }
```

Add `from typing import Any` to the module imports if missing.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_narration.py tests/test_nodes.py -q -k narrat`
Expected: PASS. Then the full gates (the integration test `tests/integration/test_full_run.py` is not part of the default run; leave it).

- [ ] **Step 7: Commit**

```bash
git add backend/app/agent/narration.py backend/app/agent/nodes.py backend/tests/test_narration.py backend/tests/test_nodes.py
git commit -m "feat(agent): guarded narration with its own system block, retry and fallback

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: Post-mortem reads seeds from state

**Files:**
- Modify: `backend/app/game/post_mortem.py` (seed source, `_seed_to_ref`, `_build_counterfactuals`)
- Modify: `backend/app/game/models.py` (`ConsequenceSeed` docstring: deprecated)
- Modify: `backend/tests/test_post_mortem.py` (fixture at lines ~127-150 and ~313-342)
- Test: `backend/tests/test_post_mortem.py`

**Interfaces:**
- Consumes: `schemas.ConsequenceSeed`, `SeedPayload`, `DecisionRef` (existing).
- Produces:
  - `post_mortem._seeds_from_state(session) -> list[schemas.ConsequenceSeed]` (skips and logs malformed entries)
  - `post_mortem._seed_to_ref(seed: schemas.ConsequenceSeed, title_by_turn: dict[int, str]) -> PostMortemSeedRef`

- [ ] **Step 1: Write the failing tests**

Add a pure unit test to `backend/tests/test_post_mortem.py`:

```python
def test_post_mortem_skips_malformed_seed(caplog: pytest.LogCaptureFixture) -> None:
    from types import SimpleNamespace

    from app.game.post_mortem import _seeds_from_state

    game_id = uuid4()
    good = _seed_dict(uuid4(), game_id, written=0, fires=2, fired=False, dormant=True, description="Marko remembers")
    session = SimpleNamespace(id=game_id, state={"consequence_seeds": [good, {"bogus": 1}]})
    with caplog.at_level("WARNING"):
        seeds = _seeds_from_state(session)
    assert [s.payload.description for s in seeds] == ["Marko remembers"]
    assert "malformed seed" in caplog.text
```

Add this helper near `_empty_colony_state`:

```python
def _seed_dict(
    seed_id: UUID,
    game_id: UUID,
    *,
    written: int,
    fires: int,
    fired: bool,
    dormant: bool,
    description: str,
    foreshadowed: bool = False,
) -> dict:
    return {
        "id": str(seed_id),
        "game_id": str(game_id),
        "written_at_turn": written,
        "fires_at_turn": fires,
        "foreshadowed": foreshadowed,
        "foreshadow_turn": written if foreshadowed else None,
        "payload": {
            "event_id": f"aftermath_{seed_id.hex[:6]}",
            "severity": 2,
            "category": "infrastructure",
            "description": description,
        },
        "origin_decision": {"turn_number": written, "event_id": "evt", "chosen_proposal_id": "p1"},
        "fired": fired,
        "dormant": dormant,
    }
```

In `test_build_post_mortem_aggregates_completed_run`, delete the two `s.add(game_models.ConsequenceSeed(...))` blocks and instead build the `GameSession` with seeds in its state:

```python
                state={
                    **_empty_colony_state(game_id, user_id),
                    "consequence_seeds": [
                        _seed_dict(seed_fired_id, game_id, written=0, fires=1, fired=True,
                                   dormant=False, description="Aftermath of the leak",
                                   foreshadowed=True),
                        _seed_dict(seed_dormant_id, game_id, written=0, fires=5, fired=False,
                                   dormant=True, description="Marko remembers"),
                    ],
                },
```

and add to its assertions:

```python
    dormant_ref = next(s for s in cf.dormant_seeds if s.id == seed_dormant_id)
    assert dormant_ref.payload_summary == "Marko remembers"
    assert dormant_ref.origin_event_title == post_mortem.turns[0].event.title
```

Update `_empty_colony_state`'s docstring: the aggregator now reads `consequence_seeds` from this state.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:54322/postgres uv run pytest tests/test_post_mortem.py -q`
Expected: FAIL (`_seeds_from_state` missing; seeds from state not surfaced).

- [ ] **Step 3: Implement** (`backend/app/game/post_mortem.py`)

Imports: add `import logging`, `from pydantic import ValidationError`, `from app.game import schemas as game_schemas`; module-level `logger = logging.getLogger(__name__)`.

Replace the `seeds = (...)` query block in `build_post_mortem` with:

```python
    seeds = _seeds_from_state(session)
    title_by_turn = {tl.turn_number: str((tl.event or {}).get("title", "")) for tl in turn_logs}
```

Change the lookup type hints from `game_models.ConsequenceSeed` to `game_schemas.ConsequenceSeed`, every `_seed_to_ref(s, agents)` to `_seed_to_ref(s, title_by_turn)`, and the counterfactuals call to `_build_counterfactuals(seeds=seeds, title_by_turn=title_by_turn, grievance_history=grievance_history)`. Delete the "Phase 4 quirk" comment above the `origin_seed_id` fallback and replace it with `# A seed-fired event names its seed; use it when the firing-turn join finds none.`

Add / replace:

```python
def _seeds_from_state(session: Any) -> list[game_schemas.ConsequenceSeed]:
    """Seeds live in game_sessions.state; the consequence_seeds table is never written.

    A malformed entry (older engine versions) is skipped and logged, so an
    old shared link still renders.
    """
    out: list[game_schemas.ConsequenceSeed] = []
    for raw in (session.state or {}).get("consequence_seeds") or []:
        try:
            out.append(game_schemas.ConsequenceSeed.model_validate(raw))
        except ValidationError:
            logger.warning("post_mortem: skipping malformed seed in game %s", session.id)
    return out


def _seed_to_ref(
    seed: game_schemas.ConsequenceSeed, title_by_turn: dict[int, str]
) -> PostMortemSeedRef:
    """Project a seed into the public ref shape (spec §4.5)."""
    return PostMortemSeedRef(
        id=seed.id,
        written_at_turn=seed.written_at_turn,
        fires_at_turn=seed.fires_at_turn,
        foreshadowed=seed.foreshadowed,
        fired=seed.fired,
        dormant=seed.dormant,
        payload_summary=seed.payload.description,
        origin_event_title=title_by_turn.get(seed.origin_decision.turn_number) or None,
    )
```

`_build_counterfactuals` takes `seeds: list[game_schemas.ConsequenceSeed]` and `title_by_turn: dict[int, str]` (drop `agents`, which `_seed_to_ref` never used). Update the module docstring: it reads `game_sessions` (including `state.consequence_seeds`), `agents` and `turn_logs`.

In `backend/app/game/models.py`, prepend to the `ConsequenceSeed` docstring: `Deprecated: never written by app code; seeds live in game_sessions.state.consequence_seeds. Kept until a cleanup migration drops the table.`

- [ ] **Step 4: Run the tests to verify they pass**

Run the Step 2 command again. Expected: PASS. Then the full gates.

- [ ] **Step 5: Commit**

```bash
git add backend/app/game/post_mortem.py backend/app/game/models.py backend/tests/test_post_mortem.py
git commit -m "fix(post-mortem): read consequence seeds from game state

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 10: API, dashboard and persona view: `risk_note` and `close_preview`

**Files:**
- Modify: `backend/app/game/schemas_io.py` (`ProposalPublic`, `GameSessionResponse`, `PostMortemProposal`)
- Modify: `backend/app/agent/service.py` (`_proposal_public`, `_proposals_ready_payload`)
- Modify: `backend/app/game/router.py::_shape_response` (lines ~60-125)
- Modify: `backend/app/game/post_mortem.py` (proposal projection, line ~170)
- Modify: `backend/app/eval/playtest/turn_view.py`, `backend/app/eval/playtest/turn_message.py`
- Modify: `frontend/lib/api/client.ts`, `frontend/components/dashboard/ChoosePanel.tsx`, `frontend/components/dashboard/DashboardShell.tsx`, `frontend/components/dashboard/DashboardPreview.tsx`, `frontend/lib/dev/fixtures.ts`
- Test: `backend/tests/test_agent_service_payloads.py` (new), `backend/tests/eval/playtest/test_turn_view.py`, `backend/tests/eval/playtest/test_persona_session.py`

**Interfaces:**
- Consumes: `preview.render_close()` (Task 3), `Proposal.risk_note` (Task 2).
- Produces: API fields `ProposalPublic.risk_note: str | None`, `GameSessionResponse.close_preview: list[str]`, `PostMortemProposal.risk_note: str | None`, `proposals_ready.close_preview: list[str]`; TS types mirror them as optional.

- [ ] **Step 1: Write the failing backend tests**

`backend/tests/test_agent_service_payloads.py`:

```python
from app.agent.service import _proposal_public, _proposals_ready_payload
from app.game.schemas import Proposal, ProposalFlavor


def _p(note: str | None) -> Proposal:
    return Proposal(
        id="p", proposed_by_agent_id=None, flavor=ProposalFlavor.MORAL, name="n",
        description="d", effects_preview=["fuel −5"], tool_calls_on_pick=[], risk_note=note,
    )


def test_proposal_public_carries_risk_note() -> None:
    assert _proposal_public(_p("Stores may not last."))["risk_note"] == "Stores may not last."
    assert _proposal_public(_p(None))["risk_note"] is None


def test_proposals_ready_payload_has_close_preview() -> None:
    payload = _proposals_ready_payload({"colony_state": None, "proposals": [_p(None)]})
    assert payload["close_preview"] == ["−1 legitimacy at day's end"]
```

Append to `backend/tests/eval/playtest/test_turn_view.py`:

```python
def test_view_carries_close_preview_and_risk_note(colony: ColonyState) -> None:
    tracker = TurnViewTracker()
    props = _proposals(colony)
    props[0]["risk_note"] = "Stores may not last."
    frames = [
        {
            "event": "proposals_ready",
            "data": {
                "turn_number": 0,
                "current_event": _event("e1", "Coil Cracks"),
                "proposals": props,
                "close_preview": ["−1 legitimacy at day's end"],
            },
        }
    ]
    tracker.absorb(frames)
    view = tracker.build(turn=1, colony=colony, turn_frames=frames, proposals=props)
    assert view["close_preview"] == ["−1 legitimacy at day's end"]
    tracker.record_choice(props[0])
    assert tracker._last_choice["risk_note"] == "Stores may not last."
```

Append to `backend/tests/eval/playtest/test_persona_session.py` (UI-parity section):

```python
def test_user_message_renders_close_preview_and_risk_note(fixture_turn_input: dict[str, Any]) -> None:
    turn_input = {**fixture_turn_input, "close_preview": ["−1 legitimacy at day's end"]}
    first = turn_input["proposals"][0]
    turn_input["proposals"] = [{**first, "risk_note": "Stores may not last."}, *turn_input["proposals"][1:]]
    msg = _format_turn_user_message(turn_input=turn_input, displayed_order=_ORDER)
    assert "Every option also: −1 legitimacy at day's end" in msg
    assert "   Risk: Stores may not last." in msg
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_agent_service_payloads.py tests/eval/playtest/test_turn_view.py tests/eval/playtest/test_persona_session.py -q`
Expected: FAIL (`risk_note` key missing, no `close_preview`).

- [ ] **Step 3: Backend implementation**

`schemas_io.py`: add `risk_note: str | None = None` to `ProposalPublic` (after `effects_preview`) and to `PostMortemProposal`; add to `GameSessionResponse`:

```python
    # Engine rule lines shown once in the ChoosePanel header (spec §4.1).
    close_preview: list[str] = []
```

Update `ProposalPublic`'s docstring: the player sees the engine-rendered `effects_preview` and an optional sanitized `risk_note`.

`service.py::_proposal_public`: add `risk_note=p.risk_note,`. `_proposals_ready_payload`: add `"close_preview": render_close(),` (import `from app.game.preview import render_close`).

`router.py::_shape_response`: add `risk_note=p.risk_note,` to the `ProposalPublic(...)` call and `close_preview=render_close(),` to `GameSessionResponse(...)` (same import).

`post_mortem.py` proposal projection: add `risk_note=p.get("risk_note"),`.

`turn_view.py`: in `record_choice` add `"risk_note": proposal.get("risk_note"),`; in `build`, add `"close_preview": list(pr_data.get("close_preview") or []),` to `view`; add a row to the module docstring's field table: `close_preview | ChoosePanel header` and extend the proposals row with `risk_note`.

`turn_message.py::format_turn_user_message`: after `lines += ["", "## Proposals (in display order)"]` add:

```python
    if view.get("close_preview"):
        lines.append(f"Every option also: {'; '.join(view['close_preview'])}")
```

and after each `lines.append(f"   Effects: {effects}")`:

```python
        if p.get("risk_note"):
            lines.append(f"   Risk: {p['risk_note']}")
```

- [ ] **Step 4: Run the backend tests to verify they pass**

Run the Step 2 command again. Expected: PASS. Then the full Python gates.

- [ ] **Step 5: Frontend implementation**

`frontend/lib/api/client.ts`:
- `ProposalPublic` and `PostMortemProposal`: add `risk_note?: string | null;`
- `GameSessionResponse`: add `/** Engine rule lines shown once above the choices. */ close_preview?: string[];`
- `ProposalsReadyPayload`: add `close_preview?: string[];`

`frontend/components/dashboard/ChoosePanel.tsx`:
- `Props`: add `closePreview?: string[];` and destructure it.
- Replace the `CHOOSE` `<h2>` with:

```tsx
      <div className="mb-3 flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <h2 className="font-display text-[10px] uppercase tracking-widest text-px-accent">
          CHOOSE
        </h2>
        {closePreview && closePreview.length > 0 ? (
          <p
            data-testid="close-preview"
            className="font-terminal text-sm text-px-text-dim"
          >
            {closePreview.join(" · ")}
          </p>
        ) : null}
      </div>
```

- After the effects `<ul>` block inside each option, add:

```tsx
                  {p.risk_note ? (
                    <span
                      data-testid="risk-note"
                      className="font-body text-sm italic leading-snug text-px-text-dim"
                    >
                      {p.risk_note}
                    </span>
                  ) : null}
```

`DashboardShell.tsx`: pass `closePreview={initialState.close_preview ?? []}` to `ChoosePanel`.
`DashboardPreview.tsx`: pass `closePreview={DEV_GAME_STATE.close_preview ?? []}`.
`frontend/lib/dev/fixtures.ts`: rewrite the three `effects_preview` arrays to engine format (`["fuel −12", "warmth +20", "morale −4"]`, `["fuel −8", "warmth +6", "medicine +10"]`, `["fuel −8", "warmth +10"]`), add `risk_note: "Stores may not last the week."` to the first proposal and `risk_note: null` to the others, and add `close_preview: ["−1 legitimacy at day's end"]` to the dev game state object.

- [ ] **Step 6: Frontend gates**

```bash
cd frontend && pnpm lint && pnpm build
```

Expected: both pass (there is no `pnpm test` script; spec §7 replaces it with a screenshot check).

- [ ] **Step 7: Screenshot check**

Start the dev server (`pnpm dev`), open `http://localhost:3000/dev/dashboard`, drive the preview until the choices show (use the preview's own controls), and take screenshots at widths 1440 and 768 with the Playwright MCP (`browser_resize`, `browser_take_screenshot`). Confirm: the header shows `−1 legitimacy at day's end`; the first option shows its italic risk note under its effects; options without a note show none; nothing overflows at 768. Save the screenshots under the session scratchpad, not the repo. Stop the dev server.

- [ ] **Step 8: Commit**

```bash
git add backend/app/game/schemas_io.py backend/app/agent/service.py backend/app/game/router.py backend/app/game/post_mortem.py backend/app/eval/playtest/turn_view.py backend/app/eval/playtest/turn_message.py backend/tests/test_agent_service_payloads.py backend/tests/eval/playtest/test_turn_view.py backend/tests/eval/playtest/test_persona_session.py frontend/lib/api/client.ts frontend/components/dashboard/ChoosePanel.tsx frontend/components/dashboard/DashboardShell.tsx frontend/components/dashboard/DashboardPreview.tsx frontend/lib/dev/fixtures.ts
git commit -m "feat(ui): show the end-of-day rule once and each option's risk note

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 11: Exit-gate audit script

**Files:**
- Create: `backend/scripts/audit_integrity.py`
- Test: `backend/tests/test_audit_integrity.py` (new)

**Interfaces:**
- Consumes: everything above; `scripts._harness.require_local_database`; `post_mortem.build_post_mortem`.
- Produces: CLI `uv run python -m scripts.audit_integrity (--since ISO8601 | --game-ids id,id,...)`; exit 1 if any gated check fails. Pure helpers `title_ok`, `tic_phrase`, `numbers_near_stats`, `audit_turn`, `build_report`, dataclass `Tally`.

- [ ] **Step 1: Write the failing tests** (`backend/tests/test_audit_integrity.py`)

```python
"""Pure parts of the spec-1 exit-gate audit."""

from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID

from app.agent.prepare import prepare_proposals
from app.game import engine
from app.game.generator import generate_run
from app.game.preview import compact_state
from app.game.schemas import Event, EventOrigin, ToolCall
from app.game.setting_loader import load_setting
from scripts.audit_integrity import (
    Tally,
    audit_turn,
    build_report,
    numbers_near_stats,
    tic_phrase,
    title_ok,
)

ARCTIC = load_setting("arctic_station_v1")
USER = UUID("bbbbbbbb-0000-0000-0000-000000000002")


def _row(tamper: bool = False, meta: bool = True) -> SimpleNamespace:
    colony = generate_run(user_id=USER, setting_id="arctic_station_v1", seed=7)
    call = ToolCall(
        name="propose_action",
        caller_id=colony.agents[0].id,
        args={
            "name": "Burn stores",
            "description": "Burn them.",
            "flavor": "DOMINANT",
            "tool_calls_on_pick": [
                {"name": "modify_resource", "args": {"resource_id": "fuel", "delta": -5, "reason": "r"}}
            ],
        },
    )
    prepared = prepare_proposals(colony, [call], ARCTIC)
    picked = prepared.proposals[0]
    derived = engine.derive_resolution_consequences(prepared.state, picked, prepared.proposals)
    after = engine.apply_tool_calls(
        prepared.state, engine.stamp_phase([*picked.tool_calls_on_pick, *derived], "resolve")
    )
    closed = engine.close_turn(after)
    proposals = [p.model_dump(mode="json") for p in prepared.proposals]
    if tamper:
        proposals[0]["effects_preview"] = ["fuel −50"]
    event = Event(
        id="e", category="weather_blizzard", title="Blizzard from the northeast",
        body="Snow.", severity=2, origin=EventOrigin.NATURAL,
    )
    return SimpleNamespace(
        game_id=colony.game_id,
        turn_number=0,
        event=event.model_dump(mode="json"),
        proposals=proposals,
        chosen={"chosen_proposal_id": picked.id},
        agent_responses=[],
        narration=f"You backed {colony.agents[0].name}. The stores burned down.",
        tool_calls=[tc.model_dump(mode="json") for tc in closed.turn_tool_calls],
        turn_meta=(
            {
                "debate_retried": False,
                "proposal_classes": prepared.classes,
                "risk_notes_dropped": 0,
                "pre_pick": compact_state(prepared.state),
                "narration": {
                    "source": "llm", "attempts": 1,
                    "first_attempt_violations": [], "kept_violations": [],
                },
            }
            if meta
            else None
        ),
    )


def test_clean_turn_passes() -> None:
    t = Tally()
    audit_turn(_row(), ARCTIC, [], t)
    assert (t.turns, t.preview_mismatch, t.picked_rejected, t.stored_hard) == (1, 0, 0, 0)
    assert t.titles_bad == []


def test_tampered_preview_is_a_mismatch() -> None:
    t = Tally()
    audit_turn(_row(tamper=True), ARCTIC, [], t)
    assert t.preview_mismatch == 1


def test_pre_branch_turn_is_skipped() -> None:
    t = Tally()
    audit_turn(_row(meta=False), ARCTIC, [], t)
    assert t.turns == 0


def test_title_rules() -> None:
    assert title_ok("Blizzard from the northeast", ARCTIC)
    assert title_ok("Aftermath of Blizzard from the northeast", ARCTIC)
    assert title_ok("Window frost", ARCTIC)  # respite template
    assert title_ok("A coded message arrives addressed to the…", ARCTIC)
    assert not title_ok("A coded message arrives addressed to the commissar by his ol", ARCTIC)


def test_tic_phrase_and_numbers() -> None:
    assert tic_phrase('Sometimes closes a line with "yes?" when pushing.') == "yes?"
    assert tic_phrase("Reaches for clinical framing.") is None
    assert numbers_near_stats("Cut fuel −10 now", ARCTIC)
    assert numbers_near_stats("+5 legitimacy if it works", ARCTIC)
    assert not numbers_near_stats("Wait three days", ARCTIC)


def test_report_fails_on_mismatch() -> None:
    t = Tally()
    audit_turn(_row(tamper=True), ARCTIC, [], t)
    rows = build_report(t, dormant_failures=0, costs=[10, 12])
    status = {check: s for check, _value, _target, s in rows}
    assert status["Preview ≠ resolve render"] == "FAIL"
    assert status["Picked calls rejected at resolve"] == "PASS"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_audit_integrity.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'scripts.audit_integrity'`

- [ ] **Step 3: Implement `backend/scripts/audit_integrity.py`**

```python
"""Exit-gate audit for spec 1, truth & integrity (spec §8).

Reads game_sessions + turn_logs for the selected games from the LOCAL
database and prints one table. Exit code 1 if any gated check fails.

    cd backend && DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:54322/postgres \\
      uv run python -m scripts.audit_integrity --since 2026-10-20T00:00:00+00:00
"""

from __future__ import annotations

import argparse
import asyncio
import re
import statistics
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select

from app.agent.narration_guard import (
    HARD_CODES,
    build_allowed_terms,
    build_strict_terms,
    validate_narration,
)
from app.config import settings
from app.database import async_session_factory
from app.eval.tool_validity import (
    TOOL_VALIDITY_THRESHOLD,
    tool_validity_by_phase,
    tool_validity_rate,
)
from app.game.events import _RESPITE_TEMPLATES
from app.game.models import GameSession, TurnLog
from app.game.post_mortem import build_post_mortem
from app.game.preview import outcome_from_calls, render_effects, state_from_compact
from app.game.schemas import Event, ResolvedEvent, ToolCall
from app.game.setting_loader import Setting, load_setting
from scripts._harness import require_local_database

COLD_WORDS = ("frost", "ice", "snow", "blizzard", "frozen", "sub-zero")
COLD_BASELINE = {"desert_outpost_v1": 23, "orbital_colony_v1": 12}
_COLD = re.compile(r"\b(" + "|".join(COLD_WORDS) + r")\b", re.IGNORECASE)
_SIGNED_THEN_WORD = re.compile(r"[+\-−]\s?\d+\s*%?\s*([A-Za-z]+)")
_WORD_THEN_SIGNED = re.compile(r"([A-Za-z]+)\s*[+\-−]\s?\d+")
_QUOTED = re.compile(r"\"([^\"]{2,})\"")
_AFTERMATH = "Aftermath of "
_TITLE_MAX = 48


@dataclass
class Tally:
    turns: int = 0
    preview_mismatch: int = 0
    mismatch_examples: list[str] = field(default_factory=list)
    picked_rejected: int = 0
    debate_stat_changes: int = 0
    stored_hard: int = 0
    first_hard: int = 0
    stored_unknown: int = 0
    strict_unknown: int = 0
    fallbacks: int = 0
    titles: int = 0
    titles_bad: list[str] = field(default_factory=list)
    cold_words: Counter[str] = field(default_factory=Counter)
    tic_lines: Counter[tuple[str, str]] = field(default_factory=Counter)
    tic_hits: Counter[tuple[str, str]] = field(default_factory=Counter)
    classes: Counter[str] = field(default_factory=Counter)
    numbers_in_text: int = 0
    risk_dropped: int = 0
    retried: int = 0
    calls: list[ToolCall] = field(default_factory=list)


def title_ok(title: str, setting: Setting) -> bool:
    core = title[len(_AFTERMATH) :] if title.startswith(_AFTERMATH) else title
    authored = {e.title for e in setting.event_categories if e.title}
    respite = {t for t, _ in _RESPITE_TEMPLATES}
    return core in authored or core in respite or core.endswith("…") or len(core) <= _TITLE_MAX


def tic_phrase(tic: str) -> str | None:
    m = _QUOTED.search(tic)
    return m.group(1).rstrip(" —-–") if m else None


def numbers_near_stats(text: str, setting: Setting) -> bool:
    words = {"legitimacy", "health", "grievance"}
    words |= {r.id for r in setting.resources} | {r.name.lower() for r in setting.resources}
    hits = [m.group(1).lower() for m in _SIGNED_THEN_WORD.finditer(text)]
    hits += [m.group(1).lower() for m in _WORD_THEN_SIGNED.finditer(text)]
    return any(h in words for h in hits)


def audit_turn(row: Any, setting: Setting, prior: list[ResolvedEvent], t: Tally) -> None:
    """Fold one turn_logs row into the tally. Rows without turn_meta predate the branch."""
    meta = row.turn_meta
    if meta is None or row.tool_calls is None:
        return
    t.turns += 1
    calls = [ToolCall.model_validate(c) for c in row.tool_calls]
    t.calls += calls
    t.retried += bool(meta.get("debate_retried"))
    t.classes.update(meta.get("proposal_classes") or [])
    t.risk_dropped += int(meta.get("risk_notes_dropped") or 0)
    t.debate_stat_changes += sum(
        1
        for c in calls
        if c.phase == "debate" and c.rejection_reason is None and c.name != "propose_action"
    )
    resolve = [c for c in calls if c.phase == "resolve"]
    t.picked_rejected += sum(1 for c in resolve if c.rejection_reason)

    pre = state_from_compact(meta["pre_pick"]) if meta.get("pre_pick") else None
    chosen_id = (row.chosen or {}).get("chosen_proposal_id")
    picked = next((p for p in row.proposals or [] if p.get("id") == chosen_id), None)
    if picked is not None and pre is not None:
        rendered = render_effects(setting, outcome_from_calls(resolve, pre))
        if rendered != list(picked.get("effects_preview") or []):
            t.preview_mismatch += 1
            t.mismatch_examples.append(
                f"{row.game_id} t{row.turn_number}: {picked.get('effects_preview')} vs {rendered}"
            )
    for p in row.proposals or []:
        if numbers_near_stats(f"{p.get('name', '')} {p.get('description', '')}", setting):
            t.numbers_in_text += 1

    title = str((row.event or {}).get("title", ""))
    t.titles += 1
    if not title_ok(title, setting):
        t.titles_bad.append(title)

    narration_meta = meta.get("narration") or {}
    t.fallbacks += narration_meta.get("source") == "fallback"
    first = narration_meta.get("first_attempt_violations") or []
    t.first_hard += any(code.split(":", 1)[0] in HARD_CODES for code in first)

    if row.narration and pre is not None:
        event = Event.model_validate(row.event) if row.event else None
        state = pre.model_copy(update={"event_log": prior, "current_event": event})
        extra = [str(r.get("text", "")) for r in row.agent_responses or []]
        extra += [str(p.get(k, "")) for p in row.proposals or [] for k in ("name", "description")]
        roster = frozenset(a.name for a in pre.agents)
        found = validate_narration(row.narration, build_allowed_terms(setting, state, extra), roster)
        t.stored_hard += any(v.hard for v in found)
        t.stored_unknown += any(v.kind == "unknown_name" for v in found)
        strict = validate_narration(row.narration, build_strict_terms(setting, pre), roster)
        t.strict_unknown += any(v.kind == "unknown_name" for v in strict)
        if setting.id in COLD_BASELINE:
            t.cold_words[setting.id] += len(_COLD.findall(row.narration))

    if pre is not None:
        role_by_agent = {str(a.id): a.role for a in pre.agents}
        for r in row.agent_responses or []:
            role = role_by_agent.get(str(r.get("agent_id")))
            arch = next((a for a in setting.advisor_archetypes if a.role_name == role), None)
            phrase = tic_phrase(arch.verbal_tic) if arch else None
            if role and phrase:
                key = (setting.id, role)
                t.tic_lines[key] += 1
                t.tic_hits[key] += phrase.lower() in str(r.get("text", "")).lower()


def _share(n: int, d: int) -> float:
    return n / d if d else 0.0


def build_report(t: Tally, *, dormant_failures: int, costs: list[int]) -> list[tuple[str, str, str, str]]:
    """Rows of (check, value, target, PASS|FAIL|INFO)."""
    rows: list[tuple[str, str, str, str]] = []

    def gate(name: str, value: float, target: str, ok: bool, fmt: str = "{:.0f}") -> None:
        rows.append((name, fmt.format(value), target, "PASS" if ok else "FAIL"))

    def info(name: str, value: str, target: str = "reported") -> None:
        rows.append((name, value, target, "INFO"))

    n = t.turns
    gate("Preview ≠ resolve render", t.preview_mismatch, "0", t.preview_mismatch == 0)
    gate("Picked calls rejected at resolve", t.picked_rejected, "0", t.picked_rejected == 0)
    gate("Stat changes applied in debate", t.debate_stat_changes, "0", t.debate_stat_changes == 0)
    gate("Stored narrations with a hard violation", t.stored_hard, "0", t.stored_hard == 0)
    gate("First attempts with a hard violation", _share(t.first_hard, n), "≤ 5%",
         _share(t.first_hard, n) <= 0.05, "{:.1%}")
    gate("Stored narrations with unknown_name", _share(t.stored_unknown, n), "≤ 2%",
         _share(t.stored_unknown, n) <= 0.02, "{:.1%}")
    info("Stored narrations, strict unknown_name", f"{_share(t.strict_unknown, n):.1%}")
    gate("Narration fallbacks", _share(t.fallbacks, n), "≤ 5%", _share(t.fallbacks, n) <= 0.05, "{:.1%}")
    gate("Event titles outside the allowed sources", len(t.titles_bad), "0", not t.titles_bad)
    for sid, base in COLD_BASELINE.items():
        info(f"Cold words in {sid} narration", str(t.cold_words[sid]), f"< baseline {base}")
    for (sid, role), lines in sorted(t.tic_lines.items()):
        info(f"Tic phrase share, {sid} {role}", f"{_share(t.tic_hits[(sid, role)], lines):.0%}")
    gate("Games with dormant seeds but empty post-mortem", dormant_failures, "0", dormant_failures == 0)
    rate = tool_validity_rate(t.calls)
    gate("tool_validity_rate (LLM phases)", rate, f"≥ {TOOL_VALIDITY_THRESHOLD:.2f}",
         rate >= TOOL_VALIDITY_THRESHOLD, "{:.3f}")
    info("Validity by phase", str(tool_validity_by_phase(t.calls)))
    surfaced = sum(t.classes.values())
    info("Surfaced degraded / no_effect",
         f"{_share(t.classes['degraded'], surfaced):.1%} / {_share(t.classes['no_effect'], surfaced):.1%}")
    info("Proposals with numbers next to stat words", str(t.numbers_in_text))
    info("risk_note drops", str(t.risk_dropped))
    gate("Debate retry rate", _share(t.retried, n), "≤ 25%", _share(t.retried, n) <= 0.25, "{:.1%}")
    p95 = statistics.quantiles(costs, n=20)[18] if len(costs) >= 2 else float(max(costs, default=0))
    gate("Cost per game p95 (cents)", p95, "≤ 25", p95 <= 25, "{:.1f}")
    return rows


async def _run(game_ids: list[UUID] | None, since: datetime | None) -> int:
    t = Tally()
    dormant_failures = 0
    costs: list[int] = []
    async with async_session_factory() as db:
        query = select(GameSession)
        if game_ids:
            query = query.where(GameSession.id.in_(game_ids))
        if since is not None:
            query = query.where(GameSession.started_at >= since)
        games = (await db.execute(query)).scalars().all()
        for game in games:
            setting = load_setting(game.setting_id)
            logs = (
                await db.execute(
                    select(TurnLog).where(TurnLog.game_id == game.id).order_by(TurnLog.turn_number)
                )
            ).scalars().all()
            prior: list[ResolvedEvent] = []
            for row in logs:
                audit_turn(row, setting, list(prior), t)
                if row.event:
                    chosen = str((row.chosen or {}).get("chosen_proposal_id") or "")
                    picked = next((p for p in row.proposals or [] if p.get("id") == chosen), {})
                    prior.append(
                        ResolvedEvent(
                            turn_number=row.turn_number,
                            event=Event.model_validate(row.event),
                            chosen_proposal_id=chosen,
                            summary=str(picked.get("description", "")),
                        )
                    )
            if game.end_state is not None:
                costs.append(int(game.cost_cents))
                has_dormant = any(
                    s.get("dormant") for s in (game.state or {}).get("consequence_seeds") or []
                )
                if has_dormant:
                    pm = await build_post_mortem(game_id=game.id, db=db)
                    dormant_failures += not pm.counterfactuals.dormant_seeds

    rows = build_report(t, dormant_failures=dormant_failures, costs=costs)
    print(f"Audited {len(games)} games, {t.turns} turns with turn_meta.\n")
    width = max(len(r[0]) for r in rows)
    for check, value, target, status in rows:
        print(f"{status:<5} {check:<{width}}  {value:>12}  target {target}")
    for example in t.mismatch_examples[:10]:
        print(f"  mismatch: {example}")
    for title in t.titles_bad[:10]:
        print(f"  bad title: {title!r}")
    return 1 if any(r[3] == "FAIL" for r in rows) else 0


def main() -> None:  # pragma: no cover — CLI entry
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--since", type=datetime.fromisoformat, help="ISO 8601 start time")
    group.add_argument("--game-ids", help="Comma-separated game ids")
    args = parser.parse_args()
    require_local_database(settings.DATABASE_URL)
    ids = [UUID(x) for x in args.game_ids.split(",")] if args.game_ids else None
    sys.exit(asyncio.run(_run(ids, args.since)))


if __name__ == "__main__":  # pragma: no cover
    main()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_audit_integrity.py -q`
Expected: PASS. Then the full gates.

- [ ] **Step 5: Smoke-run the CLI against local**

Run: `cd backend && DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:54322/postgres uv run python -m scripts.audit_integrity --since 2026-10-01T00:00:00+00:00`
Expected: a table prints (old games are skipped as pre-branch turns; zero audited turns is fine here). Also run once with `DATABASE_URL=postgresql+psycopg://u:p@db.example.com:5432/x` and confirm it exits non-zero with the "Refusing to run against non-local database host" message and never connects.

- [ ] **Step 6: Commit**

```bash
git add backend/scripts/audit_integrity.py backend/tests/test_audit_integrity.py
git commit -m "feat(scripts): exit-gate audit for preview, record and narration integrity

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 12: Docs, eval gate, and exit run

**Files:**
- Modify: `CLAUDE.md` (root, Anti-patterns)
- Modify: `backend/app/agent/CLAUDE.md`, `backend/app/game/CLAUDE.md`, `backend/app/eval/CLAUDE.md`
- Modify: `docs/PLAN.md` (Errors & Learnings)
- Modify: `docs/superpowers/specs/2026-05-02-axis-driven-proposals-design.md` (§3.4 note)

- [ ] **Step 1: Update the CLAUDE.md files** (edit existing lines where a convention changed; don't duplicate)

Root `CLAUDE.md`, Anti-patterns, add:
- `Letting the LLM author any number the player sees. Previews are rendered by app/game/preview.py from validated calls; the LLM supplies only tool_calls_on_pick and an optional, sanitized risk_note.`
- `Deploying a backend that maps new turn_logs columns before running the migration on prod. Every select(TurnLog) selects them, so post-mortems, share links and turn persistence all fail.`

`backend/app/agent/CLAUDE.md`:
- "Debate retry" section: replace the counting description with: `count_valid_proposals` runs `app.agent.prepare.prepare_proposals` on a throwaway result and counts proposals classified `valid` (nothing stripped, at least one non-zero applied change). A missing or all-stripped `tool_calls_on_pick` is `no_effect`, not valid.
- New section "Debate phase applies proposals only": `prepare.DEBATE_TOOL_NAMES = {"propose_action"}`; any other debate call is recorded `not_allowed_in_debate` and never applied. Preparation strips rejected nested calls (`phase="prepare"`, `stripped:<reason>`), selects valid → degraded → no_effect (max 3), renders `effects_preview`, sanitizes `risk_note`, and writes the triple to both `state["proposals"]` and `colony_state.turn_proposals`.
- New section "Narration": own system block (`app/agent/narration.py`), `validate_narration` hard vs soft violations, one retry, fallback `You backed {proposer}: {name}.`; outcome in `turn_meta["narration"]`.
- `turn_meta` line under the node pattern: reset in `generate_event`; each node returns `{**state.get("turn_meta") or {}, ...}`; persisted by `advance_turn`.
- Prompt rule: examples never use proper nouns or a real resource id (placeholders only); a test enforces no `name_pool` name in any prompt constant.

`backend/app/game/CLAUDE.md`:
- Tool-call pipeline: valid calls get `applied_delta`; every node stamps `phase` (`debate`, `prepare`, `resolve`, `autonomy`, `close`); decay is `engine.close_turn` (`modify_legitimacy`, phase `close`); the full record persists to `turn_logs.tool_calls`.
- Validation: overflow clamps (no `would_overflow`), underflow rejects.
- "When to split engine.py": Section 7 now lives in `resolution.py` (re-exported from `engine`); one `modify_legitimacy` per triggered rule; reasons name the advisor and say "you" for the player.
- Gotchas: replace the "derive_resolution_consequences reads args before validation" entry with: rejected nested calls are stripped at preparation, so the derived rules only see calls that will apply.
- Post-mortem reads seeds from `game_sessions.state.consequence_seeds`; the `consequence_seeds` table is deprecated.
- File map: add `resolution.py` and `preview.py`.

`backend/app/eval/CLAUDE.md`:
- Replace the anti-pattern at line ~65 ("Reading tool calls from turn_logs.state_snapshot ...") with: `tool_validity_rate` counts LLM-originated phases (`debate`, `prepare`, `autonomy`, plus untagged legacy calls) from `turn_logs.tool_calls`; `resolve` and `close` are engine-derived and excluded. `tool_validity_by_phase` gives the split.
- `choice_spread.py` entry: outcomes come from `app.game.preview.simulate_option` and exclude end-of-turn decay.

- [ ] **Step 2: PLAN.md and the axis spec**

Append to `docs/PLAN.md` "Errors & Learnings" four entries (symptom / root cause / fix / files):
1. Previews promised legitimacy gains the engine never applied: LLM-written `effects_preview` → engine-rendered previews (`app/game/preview.py`, `app/agent/prepare.py`).
2. Phantom fuel in desert and orbital: the cached preamble's worked example used `resource_id: "fuel"` → placeholder ids, preview strips rejects (`app/agent/prompts.py`).
3. Post-mortem seed panels always empty: read the never-written `consequence_seeds` table, and `_seed_to_ref` used invented keys → read `state.consequence_seeds` (`app/game/post_mortem.py`).
4. Narration leaked XML, "the player" and invented names: it shared the debate preamble and got a 60-char title → own system block, full event, guard with retry and fallback (`app/agent/narration.py`, `narration_guard.py`).

In `docs/superpowers/specs/2026-05-02-axis-driven-proposals-design.md` §3.4 add one line: `Update 2026-10: effects_preview is engine-rendered (app/game/preview.py); the LLM no longer writes it. See 2026-10-07-truth-and-integrity-design.md.`

- [ ] **Step 3: Full verification**

```bash
cd backend
DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:54322/postgres uv run pytest tests/ -q
uv run ruff check app/ tests/ scripts/
uv run ruff format --check app/ tests/ scripts/
cd ../frontend && pnpm lint && pnpm build
```

Expected: all pass.

- [ ] **Step 4: Commit the docs**

```bash
git add CLAUDE.md backend/app/agent/CLAUDE.md backend/app/game/CLAUDE.md backend/app/eval/CLAUDE.md docs/PLAN.md docs/superpowers/specs/2026-05-02-axis-driven-proposals-design.md
git commit -m "docs: record engine-rendered previews, phase-tagged record and narration guard

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

- [ ] **Step 5: Real-LLM gates (ask the user first)**

These spend real tokens. Ask the user before each, stating the estimated cost, and run only after a yes:
1. `cd backend && DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:54322/postgres uv run pytest -m eval` (tool validity ≥ 0.85, personality consistency ≥ 0.72; invariant #10).
2. The 90-game sweep against local (~$17): `cd backend && DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:54322/postgres uv run python -m scripts.sweep --seeds 10`; note the start time first, then `uv run python -m scripts.audit_integrity --since <start time>` with the same `DATABASE_URL`.
3. Two-persona smoke run: `cd backend && DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:54322/postgres uv run python -m scripts.play_test --personas optimizer,narrative_driven --runs-per-persona 1` (check the exact flag names with `--help`; this uses the `claude` CLI subscription). Read two archives and confirm previews, risk notes and narration look right.

Report every gate's actual numbers to the user, including failures. If a §8 target fails, spec §12 lists the pre-agreed relaxations; propose the matching one instead of changing a threshold silently.

- [ ] **Step 6: Pre-deploy checklist (for the eventual PR description; do not push)**

Write `docs/superpowers/plans/2026-10-15-truth-and-integrity-deploy.md`:

```markdown
# Deploy checklist: truth & integrity

Order is mandatory (spec §9):
1. Run `uv run alembic upgrade head` against prod (adds nullable `turn_logs.tool_calls` and `turn_logs.turn_meta`; safe for the old backend).
2. Confirm `alembic current` on prod shows `a7c3e9d41f20`.
3. Deploy the backend. Without step 1, every `select(TurnLog)` fails: post-mortems, share links and turn persistence.
4. Deploy the frontend.

Known separate blocker: Railway lacks the `LLM_*` variables.
In-flight games: one turn may show the old LLM previews after the deploy; accepted.
```

Commit it with `git add docs/superpowers/plans/2026-10-15-truth-and-integrity-deploy.md` and a `docs:` message.
