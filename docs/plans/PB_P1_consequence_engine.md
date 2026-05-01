# Pb + P1 Consequence Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the hardcoded `flavor → legitimacy` table with payload-driven consequence dispatch (Pb), and add an advisor status lifecycle (`ACTIVE | DETAINED | DISMISSED`) wired through validated tool calls (P1). Both ride in one PR because Pb is the within-turn consequence and P1 is the cross-turn consequence — they share the same dispatch pathway.

**Architecture:** A new pure helper `engine.derive_resolution_consequences(state, picked, all_proposals) → list[ToolCall]` reads the picked proposal's structured `tool_calls_on_pick` payload and the cross-proposal context (overruled DOMINANT proposers) and emits a list of validated tool calls. Two new engine-only tools join the surface: `modify_legitimacy(delta, reason)` and `set_advisor_status(target_id, new_status, reason)`. Both flow through the existing `validator → _apply_valid_effect` pipeline, restoring invariant #11 (the "documented exception" disappears). `nodes.resolve` becomes a thin folder over derived consequences + the proposal's own nested calls. Persona / colony-state rendering filters `DISMISSED` out of cast and surfaces `DETAINED` in the per-turn colony block (not the cache-stable personas block).

**Tech Stack:** Python 3.12, Pydantic v2, FastAPI, LangGraph, pytest (`asyncio_mode=auto`), ruff. Tests are pure-Python, no DB, no Anthropic SDK.

---

## Test Design (upfront)

All tests are unit-level pure-Python, co-located by module. The harness verification (Phase 4) lives outside this plan.

| Coverage area | Test file | Test pattern |
|---|---|---|
| Status enum round-trips through JSONB | `tests/test_game_schemas.py` | dump → `model_validate` cycle, default `ACTIVE`, unknown raises |
| `modify_legitimacy` validator: delta range, caller-id rejection, missing reason | `tests/test_validator.py` | Table-driven, asserts `ValidationResult.reason` strings |
| `modify_legitimacy` effect: clamp to [0, 100] | `tests/test_engine.py` | Apply tool call, assert `state.legitimacy` |
| `set_advisor_status` validator: allowed transitions, dead target, cast-collapse floor, caller-id rejection, missing reason | `tests/test_validator.py` | One test per failure mode + one happy path per allowed transition |
| `set_advisor_status` effect: state mutates, `is_alive` untouched, dead targets unreachable | `tests/test_engine.py` | Apply tool call, assert `agents[i].status` |
| `derive_resolution_consequences` rules: severe-health → big legit hit, ≥30%-of-max resource drain → medium hit, overruled-DOMINANT-proposer → grievance + small hit, baseline shift, no double-emit | `tests/test_engine.py` | Each rule in isolation + one composite scenario |
| `nodes.resolve` integration: derived calls fold through `apply_tool_calls`, no engine-direct legitimacy mutation, rejected derived calls land in `turn_tool_calls` with `rejection_reason` | `tests/test_nodes.py` | Pre-existing `_make_proposal` / `make_colony_state` fabricators |
| Persona/colony-state: `DISMISSED` excluded from `alive_agents`, `DETAINED` rendered in colony state, persona block stays cache-stable across detain/release | `tests/test_prompts.py` | Render block, regex-match expected XML attributes |

**Acceptance gates** the plan must satisfy at the end (verified in Phase 4 by harness, not by this plan's tests):

- A `modify_health(target=advisor_x, delta=-100)` proposal craters legitimacy ≥10 points regardless of proposal-flavor label.
- Detaining advisor X means X does not author a proposal next turn.
- Dismissed advisor X is removed from cast; subsequent turns produce 3-advisor proposal triples, not 4.
- `tool_validity_rate` corpus stat stays at 1.0 (the new tools must validate cleanly under expected payloads).

---

## File Map

**Create:**
- (none — no new files)

**Modify:**
- `backend/app/game/schemas.py` — add `AdvisorStatus` enum + `Agent.status` field (Task 1)
- `backend/app/game/tools.py` — add `ENGINE_TOOL_NAMES` set (Task 2, Task 3)
- `backend/app/game/validator.py` — add `_validate_modify_legitimacy`, `_validate_set_advisor_status`, expand `_NESTED_TOOL_WHITELIST`, expand the unknown-tool check (Task 2, Task 3)
- `backend/app/game/engine.py` — add effect handlers + `derive_resolution_consequences` (Task 2, Task 3, Task 4)
- `backend/app/agent/nodes.py` — remove `_FLAVOR_LEGITIMACY_SHIFT` and the engine-direct `model_copy` shift; wire `derive_resolution_consequences`; expand the `alive_agents` filter (Task 5, Task 6)
- `backend/app/agent/prompts.py` — render `status` in colony-state block; persona block remains cache-stable on `is_alive`-only filtering (Task 6)
- `backend/app/agent/CLAUDE.md` — strike the "documented exception" paragraph; document the new derived-consequences pattern (Task 5)
- `backend/app/game/CLAUDE.md` — document `ENGINE_TOOL_NAMES`, the derived-consequences pattern, and the AdvisorStatus lifecycle (Task 5)

**Test (modify):**
- `backend/tests/test_game_schemas.py` (Task 1)
- `backend/tests/test_validator.py` (Task 2, Task 3)
- `backend/tests/test_engine.py` (Task 2, Task 3, Task 4)
- `backend/tests/test_nodes.py` (Task 5)
- `backend/tests/test_prompts.py` (Task 6)

---

## Task 1: Add `AdvisorStatus` enum + `Agent.status` field

**Files:**
- Modify: `backend/app/game/schemas.py`
- Test: `backend/tests/test_game_schemas.py`

- [ ] **Step 1.1: Write the failing test for the new field**

Append to `backend/tests/test_game_schemas.py`:

```python
def test_agent_status_defaults_to_active():
    """Agents are ACTIVE by default; existing test fixtures keep working."""
    from app.game.schemas import Agent, AdvisorStatus, Quadrant
    from uuid import UUID, uuid4

    agent = Agent(
        id=uuid4(),
        game_id=UUID("aaaaaaaa-0000-0000-0000-000000000001"),
        name="Vera",
        role="Quartermaster",
        archetype_quadrant=Quadrant.ACCOUNTANT,
        ethics=0.0,
        temperament=0.0,
        loyalty=0.0,
        portrait_seed="seed",
        health=100,
        morale=50,
        grievance=0,
        is_alive=True,
    )
    assert agent.status == AdvisorStatus.ACTIVE


def test_agent_status_round_trips_through_jsonb_dump():
    """StrEnum serializes to its .value (per backend/app/game/CLAUDE.md);
    a JSON dump → model_validate cycle preserves status."""
    from app.game.schemas import Agent, AdvisorStatus, Quadrant
    from uuid import UUID, uuid4

    agent = Agent(
        id=uuid4(),
        game_id=UUID("aaaaaaaa-0000-0000-0000-000000000001"),
        name="Vera",
        role="Quartermaster",
        archetype_quadrant=Quadrant.ACCOUNTANT,
        ethics=0.0,
        temperament=0.0,
        loyalty=0.0,
        portrait_seed="seed",
        health=100,
        morale=50,
        grievance=0,
        is_alive=True,
        status=AdvisorStatus.DETAINED,
    )
    payload = agent.model_dump(mode="json")
    assert payload["status"] == "DETAINED"
    revived = Agent.model_validate(payload)
    assert revived.status == AdvisorStatus.DETAINED


def test_agent_status_rejects_unknown_value():
    """Unknown status strings raise on model_validate."""
    import pytest
    from pydantic import ValidationError
    from app.game.schemas import Agent

    with pytest.raises(ValidationError):
        Agent.model_validate({
            "id": "11111111-0000-0000-0000-000000000001",
            "game_id": "aaaaaaaa-0000-0000-0000-000000000001",
            "name": "Vera",
            "role": "Quartermaster",
            "archetype_quadrant": "ACCOUNTANT",
            "ethics": 0.0,
            "temperament": 0.0,
            "loyalty": 0.0,
            "portrait_seed": "seed",
            "health": 100,
            "morale": 50,
            "grievance": 0,
            "is_alive": True,
            "status": "BANISHED_TO_THE_VOID",
        })
```

- [ ] **Step 1.2: Run the tests; confirm they fail**

```bash
cd backend
uv run pytest tests/test_game_schemas.py::test_agent_status_defaults_to_active \
              tests/test_game_schemas.py::test_agent_status_round_trips_through_jsonb_dump \
              tests/test_game_schemas.py::test_agent_status_rejects_unknown_value -v
```

Expected: FAIL with `ImportError` or `AttributeError` on `AdvisorStatus`.

- [ ] **Step 1.3: Add the enum and field**

In `backend/app/game/schemas.py`, in the `# Enums` section (after `class EndState(StrEnum):`, around line 47), insert:

```python
class AdvisorStatus(StrEnum):
    """Non-death lifecycle for an advisor.

    Death stays orthogonal via Agent.is_alive (SPEC §3 invariant: dead means
    dead). DISMISSED is terminal-alive (one-way), parallels death for cast
    purposes without entangling the death pipeline.
    """

    ACTIVE = "ACTIVE"
    DETAINED = "DETAINED"  # reversible; doesn't author proposals
    DISMISSED = "DISMISSED"  # one-way; removed from cast
```

In the same file, in `class Agent(BaseModel):`, after the `is_alive: bool` field (around line 218), add:

```python
    status: AdvisorStatus = AdvisorStatus.ACTIVE
```

- [ ] **Step 1.4: Run the new tests; confirm pass**

```bash
uv run pytest tests/test_game_schemas.py::test_agent_status_defaults_to_active \
              tests/test_game_schemas.py::test_agent_status_round_trips_through_jsonb_dump \
              tests/test_game_schemas.py::test_agent_status_rejects_unknown_value -v
```

Expected: PASS.

- [ ] **Step 1.5: Run the full schemas test file to confirm no regressions**

```bash
uv run pytest tests/test_game_schemas.py -v
```

Expected: all green.

- [ ] **Step 1.6: Run ruff**

```bash
uv run ruff check app/game/schemas.py tests/test_game_schemas.py
uv run ruff format --check app/game/schemas.py tests/test_game_schemas.py
```

Expected: no findings.

- [ ] **Step 1.7: Commit**

```bash
git add backend/app/game/schemas.py backend/tests/test_game_schemas.py
git commit -m "feat(schemas): add AdvisorStatus enum and Agent.status field

Default ACTIVE preserves existing fixtures. StrEnum round-trips through
JSONB. Death stays orthogonal via is_alive (SPEC §3)."
```

---

## Task 2: Add `modify_legitimacy` engine-only tool

**Files:**
- Modify: `backend/app/game/tools.py`
- Modify: `backend/app/game/validator.py`
- Modify: `backend/app/game/engine.py`
- Test: `backend/tests/test_validator.py`
- Test: `backend/tests/test_engine.py`

- [ ] **Step 2.1: Write failing validator tests**

Append to `backend/tests/test_validator.py` (use the existing file's imports and `_colony` / `_agent` fabricators if present; otherwise import from `tests.test_engine`):

```python
def test_modify_legitimacy_valid_when_engine_emitted():
    from app.game.schemas import ToolCall
    from app.game.validator import validate_tool_call
    from tests.test_engine import _colony

    state = _colony(legitimacy=50)
    tc = ToolCall(
        name="modify_legitimacy",
        caller_id=None,
        args={"delta": -5, "reason": "overrode dominant advisor"},
    )
    result = validate_tool_call(tc, state)
    assert result.valid


def test_modify_legitimacy_rejects_caller_id():
    """Engine-only tool: any caller_id is invalid."""
    from uuid import uuid4
    from app.game.schemas import ToolCall
    from app.game.validator import validate_tool_call
    from tests.test_engine import _colony

    tc = ToolCall(
        name="modify_legitimacy",
        caller_id=uuid4(),
        args={"delta": -5, "reason": "x"},
    )
    result = validate_tool_call(tc, _colony())
    assert not result.valid
    assert result.reason == "engine_only_tool"


def test_modify_legitimacy_rejects_out_of_range_delta():
    from app.game.schemas import ToolCall
    from app.game.validator import validate_tool_call
    from tests.test_engine import _colony

    tc = ToolCall(
        name="modify_legitimacy",
        caller_id=None,
        args={"delta": 999, "reason": "x"},
    )
    result = validate_tool_call(tc, _colony())
    assert not result.valid
    assert result.reason == "delta_out_of_range"


def test_modify_legitimacy_requires_reason():
    from app.game.schemas import ToolCall
    from app.game.validator import validate_tool_call
    from tests.test_engine import _colony

    tc = ToolCall(name="modify_legitimacy", caller_id=None, args={"delta": -5})
    result = validate_tool_call(tc, _colony())
    assert not result.valid
    assert result.reason == "missing_reason"
```

- [ ] **Step 2.2: Write failing effect-handler test**

Append to `backend/tests/test_engine.py`:

```python
def test_modify_legitimacy_effect_clamps_to_bounds():
    from app.game.engine import apply_tool_call
    from app.game.schemas import ToolCall

    state = _colony(legitimacy=5)
    tc = ToolCall(
        name="modify_legitimacy",
        caller_id=None,
        args={"delta": -100, "reason": "catastrophic"},
    )
    new_state = apply_tool_call(state, tc)
    assert new_state.legitimacy == 0  # clamped
    assert new_state.turn_tool_calls[-1].rejection_reason is None


def test_modify_legitimacy_effect_clamps_high():
    from app.game.engine import apply_tool_call
    from app.game.schemas import ToolCall

    state = _colony(legitimacy=98)
    tc = ToolCall(
        name="modify_legitimacy",
        caller_id=None,
        args={"delta": 50, "reason": "broad backing"},
    )
    new_state = apply_tool_call(state, tc)
    assert new_state.legitimacy == 100
```

- [ ] **Step 2.3: Run; confirm both fail**

```bash
cd backend
uv run pytest tests/test_validator.py::test_modify_legitimacy_valid_when_engine_emitted \
              tests/test_validator.py::test_modify_legitimacy_rejects_caller_id \
              tests/test_validator.py::test_modify_legitimacy_rejects_out_of_range_delta \
              tests/test_validator.py::test_modify_legitimacy_requires_reason \
              tests/test_engine.py::test_modify_legitimacy_effect_clamps_to_bounds \
              tests/test_engine.py::test_modify_legitimacy_effect_clamps_high -v
```

Expected: all FAIL with `unknown_tool` / unknown effect.

- [ ] **Step 2.4: Add `ENGINE_TOOL_NAMES` to `tools.py`**

In `backend/app/game/tools.py`, after the `AGENT_TOOL_NAMES = {...}` definition (around line 22), insert:

```python
# Engine-only tools — never exposed to the LLM, never in AGENT_TOOL_SCHEMAS.
# Constructed by the engine with caller_id=None; validator rejects any
# caller_id-bearing instance with reason "engine_only_tool".
ENGINE_TOOL_NAMES = {
    "modify_legitimacy",
    "set_advisor_status",
}

KNOWN_TOOL_NAMES = AGENT_TOOL_NAMES | ENGINE_TOOL_NAMES
```

The existing `AGENT_TOOL_SCHEMAS` list and the bottom-of-file assertion stay unchanged — engine-only tools have no Anthropic schema entry.

- [ ] **Step 2.5: Add the validator**

In `backend/app/game/validator.py`:

1. Update the import on line 18:

```python
from app.game.tools import AGENT_TOOL_NAMES, KNOWN_TOOL_NAMES
```

2. After `_validate_back_leader` (around line 329), add:

```python
def _validate_modify_legitimacy(tc: ToolCall, state: ColonyState) -> ValidationResult:
    """Engine-only tool. Rejects any caller_id."""
    if tc.caller_id is not None:
        return _fail("engine_only_tool")

    args = tc.args
    delta = args.get("delta")
    if delta is None or not isinstance(delta, int) or isinstance(delta, bool):
        return _fail("missing_delta")
    if not (-100 <= delta <= 100):
        return _fail("delta_out_of_range")

    fail_reason = _require_reason(args)
    if fail_reason:
        return _fail(fail_reason)

    return _OK
```

3. Register in the `_VALIDATORS` dict (around line 336):

```python
_VALIDATORS = {
    "propose_action": _validate_propose_action,
    "modify_resource": _validate_modify_resource,
    "modify_health": _validate_modify_health,
    "adjust_grievance": _validate_adjust_grievance,
    "speak_to_agent": _validate_speak_to_agent,
    "adjust_relationship": _validate_adjust_relationship,
    "adjust_own_grievance": _validate_adjust_own_grievance,
    "remember": _validate_remember,
    "initiate_coup": _validate_initiate_coup,
    "back_leader": _validate_back_leader,
    "modify_legitimacy": _validate_modify_legitimacy,
}
```

4. Update the unknown-tool gate at the bottom (line 356):

```python
def validate_tool_call(tc: ToolCall, state: ColonyState) -> ValidationResult:
    if tc.name not in KNOWN_TOOL_NAMES:
        return _fail("unknown_tool")
    validator = _VALIDATORS[tc.name]
    return validator(tc, state)
```

- [ ] **Step 2.6: Add the effect handler**

In `backend/app/game/engine.py`:

1. In the dispatch in `_apply_valid_effect` (around line 140), add a branch before the unreachable comment:

```python
    if name == "modify_legitimacy":
        return _effect_modify_legitimacy(state, args)
```

2. After `_effect_back_leader` (around line 278), add:

```python
def _effect_modify_legitimacy(state: ColonyState, args: dict) -> ColonyState:
    delta: int = args["delta"]
    new_legitimacy = _clamp(state.legitimacy + delta, 0, 100)
    return state.model_copy(update={"legitimacy": new_legitimacy})
```

- [ ] **Step 2.7: Run the new tests; confirm pass**

```bash
uv run pytest tests/test_validator.py::test_modify_legitimacy_valid_when_engine_emitted \
              tests/test_validator.py::test_modify_legitimacy_rejects_caller_id \
              tests/test_validator.py::test_modify_legitimacy_rejects_out_of_range_delta \
              tests/test_validator.py::test_modify_legitimacy_requires_reason \
              tests/test_engine.py::test_modify_legitimacy_effect_clamps_to_bounds \
              tests/test_engine.py::test_modify_legitimacy_effect_clamps_high -v
```

Expected: PASS.

- [ ] **Step 2.8: Run full validator + engine tests for regressions**

```bash
uv run pytest tests/test_validator.py tests/test_engine.py tests/test_tools.py -v
```

Expected: all green. (`test_tools.py` may have a check that asserts `AGENT_TOOL_SCHEMAS` is non-empty or names match — it should still pass since we did not modify that list.)

- [ ] **Step 2.9: Run ruff**

```bash
uv run ruff check app/game/tools.py app/game/validator.py app/game/engine.py \
                  tests/test_validator.py tests/test_engine.py
uv run ruff format --check app/game/tools.py app/game/validator.py app/game/engine.py \
                            tests/test_validator.py tests/test_engine.py
```

Expected: no findings.

- [ ] **Step 2.10: Commit**

```bash
git add backend/app/game/tools.py backend/app/game/validator.py backend/app/game/engine.py \
        backend/tests/test_validator.py backend/tests/test_engine.py
git commit -m "feat(engine): add modify_legitimacy engine-only tool

Validated through the standard pipeline; rejects any caller_id with
'engine_only_tool'. Reinstates invariant #11 for legitimacy mutations."
```

---

## Task 3: Add `set_advisor_status` engine-only tool

**Files:**
- Modify: `backend/app/game/validator.py`
- Modify: `backend/app/game/engine.py`
- Test: `backend/tests/test_validator.py`
- Test: `backend/tests/test_engine.py`

- [ ] **Step 3.1: Write failing validator tests**

Append to `backend/tests/test_validator.py`:

```python
def _status_args(target_id: str, new_status: str = "DETAINED", reason: str = "x") -> dict:
    return {"target_id": target_id, "new_status": new_status, "reason": reason}


def test_set_advisor_status_active_to_detained_valid():
    from app.game.schemas import AdvisorStatus, ToolCall
    from app.game.validator import validate_tool_call
    from tests.test_engine import _agent, _colony

    a, b, c, d = _agent(name="A"), _agent(name="B"), _agent(name="C"), _agent(name="D")
    state = _colony(agents=[a, b, c, d])
    tc = ToolCall(
        name="set_advisor_status",
        caller_id=None,
        args=_status_args(str(a.id), "DETAINED", "compromised"),
    )
    assert validate_tool_call(tc, state).valid


def test_set_advisor_status_detained_to_active_valid():
    from app.game.schemas import AdvisorStatus, ToolCall
    from app.game.validator import validate_tool_call
    from tests.test_engine import _agent, _colony

    detained = _agent(name="A", status=AdvisorStatus.DETAINED)
    state = _colony(agents=[detained, _agent(name="B"), _agent(name="C"), _agent(name="D")])
    tc = ToolCall(
        name="set_advisor_status",
        caller_id=None,
        args=_status_args(str(detained.id), "ACTIVE", "released"),
    )
    assert validate_tool_call(tc, state).valid


def test_set_advisor_status_dismissed_one_way():
    """DISMISSED → ACTIVE / DETAINED is rejected."""
    from app.game.schemas import AdvisorStatus, ToolCall
    from app.game.validator import validate_tool_call
    from tests.test_engine import _agent, _colony

    dismissed = _agent(name="A", status=AdvisorStatus.DISMISSED)
    state = _colony(agents=[dismissed, _agent(name="B"), _agent(name="C"), _agent(name="D")])
    tc = ToolCall(
        name="set_advisor_status",
        caller_id=None,
        args=_status_args(str(dismissed.id), "ACTIVE", "regret"),
    )
    result = validate_tool_call(tc, state)
    assert not result.valid
    assert result.reason == "invalid_status_transition"


def test_set_advisor_status_dead_target_rejected():
    from app.game.schemas import ToolCall
    from app.game.validator import validate_tool_call
    from tests.test_engine import _agent, _colony

    dead = _agent(name="A", alive=False, health=0)
    state = _colony(agents=[dead, _agent(name="B"), _agent(name="C"), _agent(name="D")])
    tc = ToolCall(
        name="set_advisor_status",
        caller_id=None,
        args=_status_args(str(dead.id), "DETAINED", "x"),
    )
    result = validate_tool_call(tc, state)
    assert not result.valid
    assert result.reason == "target_dead"


def test_set_advisor_status_dismiss_blocked_by_cast_floor():
    """Dismissing when only 2 active+detained others remain is blocked."""
    from app.game.schemas import ToolCall
    from app.game.validator import validate_tool_call
    from tests.test_engine import _agent, _colony

    a = _agent(name="A")
    b = _agent(name="B")
    c = _agent(name="C", alive=False, health=0)  # dead, doesn't count
    d = _agent(name="D")
    state = _colony(agents=[a, b, c, d])
    tc = ToolCall(
        name="set_advisor_status",
        caller_id=None,
        args=_status_args(str(a.id), "DISMISSED", "treason"),
    )
    result = validate_tool_call(tc, state)
    assert not result.valid
    assert result.reason == "cast_collapse_blocked"


def test_set_advisor_status_dismiss_allowed_with_three_others_active():
    from app.game.schemas import ToolCall
    from app.game.validator import validate_tool_call
    from tests.test_engine import _agent, _colony

    a, b, c, d = _agent(name="A"), _agent(name="B"), _agent(name="C"), _agent(name="D")
    state = _colony(agents=[a, b, c, d])
    tc = ToolCall(
        name="set_advisor_status",
        caller_id=None,
        args=_status_args(str(a.id), "DISMISSED", "treason"),
    )
    assert validate_tool_call(tc, state).valid


def test_set_advisor_status_rejects_caller_id():
    from uuid import uuid4
    from app.game.schemas import ToolCall
    from app.game.validator import validate_tool_call
    from tests.test_engine import _agent, _colony

    a = _agent(name="A")
    state = _colony(agents=[a, _agent(name="B"), _agent(name="C"), _agent(name="D")])
    tc = ToolCall(
        name="set_advisor_status",
        caller_id=uuid4(),
        args=_status_args(str(a.id), "DETAINED", "x"),
    )
    result = validate_tool_call(tc, state)
    assert not result.valid
    assert result.reason == "engine_only_tool"


def test_set_advisor_status_unknown_target():
    from uuid import uuid4
    from app.game.schemas import ToolCall
    from app.game.validator import validate_tool_call
    from tests.test_engine import _colony

    tc = ToolCall(
        name="set_advisor_status",
        caller_id=None,
        args=_status_args(str(uuid4()), "DETAINED", "x"),
    )
    result = validate_tool_call(tc, _colony())
    assert not result.valid
    assert result.reason == "unknown_target"


def test_set_advisor_status_unknown_status_string():
    from app.game.schemas import ToolCall
    from app.game.validator import validate_tool_call
    from tests.test_engine import _agent, _colony

    a = _agent(name="A")
    state = _colony(agents=[a, _agent(name="B"), _agent(name="C"), _agent(name="D")])
    tc = ToolCall(
        name="set_advisor_status",
        caller_id=None,
        args=_status_args(str(a.id), "BANISHED", "x"),
    )
    result = validate_tool_call(tc, state)
    assert not result.valid
    assert result.reason == "invalid_status_transition"
```

- [ ] **Step 3.2: Write failing effect-handler tests**

Append to `backend/tests/test_engine.py`:

```python
def test_set_advisor_status_effect_active_to_detained():
    from app.game.engine import apply_tool_call
    from app.game.schemas import AdvisorStatus, ToolCall

    a, b, c, d = _agent(name="A"), _agent(name="B"), _agent(name="C"), _agent(name="D")
    state = _colony(agents=[a, b, c, d])
    tc = ToolCall(
        name="set_advisor_status",
        caller_id=None,
        args={"target_id": str(a.id), "new_status": "DETAINED", "reason": "x"},
    )
    new_state = apply_tool_call(state, tc)
    by_id = {ag.id: ag for ag in new_state.agents}
    assert by_id[a.id].status == AdvisorStatus.DETAINED
    assert by_id[a.id].is_alive is True  # is_alive untouched


def test_set_advisor_status_effect_dismissed_does_not_kill():
    """DISMISSED is alive but out of cast — distinct from death."""
    from app.game.engine import apply_tool_call
    from app.game.schemas import AdvisorStatus, ToolCall

    a, b, c, d = _agent(name="A"), _agent(name="B"), _agent(name="C"), _agent(name="D")
    state = _colony(agents=[a, b, c, d])
    tc = ToolCall(
        name="set_advisor_status",
        caller_id=None,
        args={"target_id": str(a.id), "new_status": "DISMISSED", "reason": "x"},
    )
    new_state = apply_tool_call(state, tc)
    by_id = {ag.id: ag for ag in new_state.agents}
    assert by_id[a.id].status == AdvisorStatus.DISMISSED
    assert by_id[a.id].is_alive is True
    assert by_id[a.id].health == a.health
```

- [ ] **Step 3.3: Run; confirm fail**

```bash
cd backend
uv run pytest tests/test_validator.py -k set_advisor_status \
              tests/test_engine.py::test_set_advisor_status_effect_active_to_detained \
              tests/test_engine.py::test_set_advisor_status_effect_dismissed_does_not_kill -v
```

Expected: all FAIL.

- [ ] **Step 3.4: Add the validator**

In `backend/app/game/validator.py`, after `_validate_modify_legitimacy`, add:

```python
_ALLOWED_STATUS_TRANSITIONS: frozenset[tuple[str, str]] = frozenset(
    {
        ("ACTIVE", "DETAINED"),
        ("DETAINED", "ACTIVE"),
        ("ACTIVE", "DISMISSED"),
        ("DETAINED", "DISMISSED"),
    }
)


def _validate_set_advisor_status(tc: ToolCall, state: ColonyState) -> ValidationResult:
    """Engine-only. Allowed transitions are listed in _ALLOWED_STATUS_TRANSITIONS.

    Dismiss requires ≥2 other ACTIVE-or-DETAINED advisors remaining post-transition
    so the cast cannot collapse mid-run via status manipulation.
    """
    if tc.caller_id is not None:
        return _fail("engine_only_tool")

    args = tc.args
    target_id = args.get("target_id")
    agent = _get_agent_by_id(state, str(target_id)) if target_id is not None else None
    if agent is None:
        return _fail("unknown_target")

    if not agent.is_alive:
        return _fail("target_dead")

    new_status = args.get("new_status")
    if new_status not in {"ACTIVE", "DETAINED", "DISMISSED"}:
        return _fail("invalid_status_transition")

    current = agent.status.value if hasattr(agent.status, "value") else str(agent.status)
    if (current, new_status) not in _ALLOWED_STATUS_TRANSITIONS:
        return _fail("invalid_status_transition")

    fail_reason = _require_reason(args)
    if fail_reason:
        return _fail(fail_reason)

    if new_status == "DISMISSED":
        # Count alive ACTIVE + DETAINED *other* advisors.
        others_in_cast = sum(
            1
            for a in state.agents
            if a.is_alive
            and a.id != agent.id
            and a.status.value in ("ACTIVE", "DETAINED")
        )
        if others_in_cast < 2:
            return _fail("cast_collapse_blocked")

    return _OK
```

Register in `_VALIDATORS`:

```python
_VALIDATORS = {
    # ... existing entries ...
    "modify_legitimacy": _validate_modify_legitimacy,
    "set_advisor_status": _validate_set_advisor_status,
}
```

Add to `_NESTED_TOOL_WHITELIST` (line 76):

```python
_NESTED_TOOL_WHITELIST: frozenset[str] = frozenset(
    {"modify_resource", "modify_health", "adjust_grievance", "set_advisor_status"}
)
```

- [ ] **Step 3.5: Add the effect handler**

In `backend/app/game/engine.py`:

1. Update the import on line 16-24 to include `AdvisorStatus`:

```python
from app.game.schemas import (
    Agent,
    AdvisorStatus,
    ColonyState,
    EndState,
    Proposal,
    ProposalFlavor,
    Resource,
    ToolCall,
)
```

2. Add a dispatch branch in `_apply_valid_effect`:

```python
    if name == "set_advisor_status":
        return _effect_set_advisor_status(state, args)
```

3. After `_effect_modify_legitimacy`, add:

```python
def _effect_set_advisor_status(state: ColonyState, args: dict) -> ColonyState:
    target_id = UUID(args["target_id"])
    new_status = AdvisorStatus(args["new_status"])
    agent = _agent_by_id(state, target_id)
    assert agent is not None  # validator already checked
    updated = agent.model_copy(update={"status": new_status})
    return _replace_agent(state, updated)
```

- [ ] **Step 3.6: Update the propose_action schema description in `tools.py`**

In `backend/app/game/tools.py`, update the `tool_calls_on_pick` description (around line 47-54) and the nested `enum` (line 60-65) to include `set_advisor_status`. The new description and enum:

```python
                "tool_calls_on_pick": {
                    "type": "array",
                    "maxItems": 5,
                    "description": (
                        "Nested mutations that fire when the player picks this proposal. "
                        "Each item is a structured tool call with its own name + args. "
                        "Allowed names: modify_resource, modify_health, adjust_grievance, "
                        "set_advisor_status. Args mirror the standalone tool's args; do NOT "
                        "include caller_agent_id (these calls are engine-emitted at pick time)."
                    ),
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {
                                "type": "string",
                                "enum": [
                                    "modify_resource",
                                    "modify_health",
                                    "adjust_grievance",
                                    "set_advisor_status",
                                ],
                            },
                            "args": {
                                "type": "object",
                                "additionalProperties": True,
                            },
                        },
                        "required": ["name", "args"],
                        "additionalProperties": False,
                    },
                },
```

- [ ] **Step 3.7: Run the new tests; confirm pass**

```bash
uv run pytest tests/test_validator.py -k set_advisor_status \
              tests/test_engine.py::test_set_advisor_status_effect_active_to_detained \
              tests/test_engine.py::test_set_advisor_status_effect_dismissed_does_not_kill -v
```

Expected: PASS.

- [ ] **Step 3.8: Run full validator/engine/tools/schemas test suite**

```bash
uv run pytest tests/test_validator.py tests/test_engine.py tests/test_tools.py \
              tests/test_game_schemas.py -v
```

Expected: all green.

- [ ] **Step 3.9: Run ruff**

```bash
uv run ruff check app/game/validator.py app/game/engine.py app/game/tools.py \
                  tests/test_validator.py tests/test_engine.py
uv run ruff format --check app/game/validator.py app/game/engine.py app/game/tools.py \
                            tests/test_validator.py tests/test_engine.py
```

Expected: no findings.

- [ ] **Step 3.10: Commit**

```bash
git add backend/app/game/validator.py backend/app/game/engine.py backend/app/game/tools.py \
        backend/tests/test_validator.py backend/tests/test_engine.py
git commit -m "feat(engine): add set_advisor_status with cast-collapse floor

Engine-only tool (caller_id must be None). Allowed transitions:
ACTIVE↔DETAINED and {ACTIVE,DETAINED}→DISMISSED. DISMISSED is one-way.
Dismiss blocked when fewer than 2 active-or-detained others remain.
Now nested-whitelisted so it can ride in tool_calls_on_pick."
```

---

## Task 4: Implement `engine.derive_resolution_consequences`

**Files:**
- Modify: `backend/app/game/engine.py`
- Test: `backend/tests/test_engine.py`

- [ ] **Step 4.1: Write failing tests for each rule + a composite scenario**

Append to `backend/tests/test_engine.py`:

```python
# ---------------------------------------------------------------------------
# derive_resolution_consequences (Pb)
# ---------------------------------------------------------------------------


def _proposal(
    *,
    flavor: ProposalFlavor = ProposalFlavor.WILDCARD,
    proposed_by_agent_id=None,
    tool_calls_on_pick: list | None = None,
    pid: str = "p1",
) -> Proposal:
    return Proposal(
        id=pid,
        proposed_by_agent_id=proposed_by_agent_id,
        flavor=flavor,
        name=f"Proposal {pid}",
        description="x",
        effects_preview=[],
        tool_calls_on_pick=tool_calls_on_pick or [],
    )


def test_derive_severe_health_drain_emits_big_legitimacy_hit():
    from app.game.engine import derive_resolution_consequences

    target = _agent(name="A")
    state = _colony(agents=[target, _agent(name="B"), _agent(name="C"), _agent(name="D")])
    nested = ToolCall(
        name="modify_health",
        caller_id=None,
        args={"target_id": str(target.id), "delta": -100, "reason": "left in ice"},
    )
    picked = _proposal(flavor=ProposalFlavor.WILDCARD, tool_calls_on_pick=[nested])

    derived = derive_resolution_consequences(state, picked, [picked])

    legit_calls = [tc for tc in derived if tc.name == "modify_legitimacy"]
    assert legit_calls, "expected at least one modify_legitimacy"
    total_delta = sum(tc.args["delta"] for tc in legit_calls)
    assert total_delta <= -10, f"severe-health rule: expected <= -10, got {total_delta}"


def test_derive_resource_drain_thirty_percent_emits_medium_hit():
    from app.game.engine import derive_resolution_consequences

    fuel = _resource(rid="fuel", value=100, max_=100, crit=20)
    state = _colony(resources={"fuel": fuel})
    nested = ToolCall(
        name="modify_resource",
        caller_id=None,
        args={"resource_id": "fuel", "delta": -30, "reason": "burn it all"},
    )
    picked = _proposal(flavor=ProposalFlavor.DOMINANT, tool_calls_on_pick=[nested])

    derived = derive_resolution_consequences(state, picked, [picked])

    legit_calls = [tc for tc in derived if tc.name == "modify_legitimacy"]
    total_delta = sum(tc.args["delta"] for tc in legit_calls)
    assert -10 < total_delta <= -3, (
        f"resource-drain rule: expected medium hit (-3 to -10), got {total_delta}"
    )


def test_derive_overruled_dominant_proposer_grievance_and_small_legit_hit():
    from app.game.engine import derive_resolution_consequences

    proposer = _agent(name="DominantProposer")
    state = _colony(
        agents=[proposer, _agent(name="B"), _agent(name="C"), _agent(name="D")]
    )
    dominant = _proposal(
        flavor=ProposalFlavor.DOMINANT,
        proposed_by_agent_id=proposer.id,
        pid="p_dom",
    )
    picked = _proposal(flavor=ProposalFlavor.MORAL, pid="p_moral")

    derived = derive_resolution_consequences(state, picked, [dominant, picked])

    grievance_calls = [
        tc
        for tc in derived
        if tc.name == "adjust_grievance"
        and tc.args.get("target_id") == str(proposer.id)
        and tc.args["delta"] > 0
    ]
    assert grievance_calls, "expected adjust_grievance(+) for overruled DOMINANT proposer"


def test_derive_picking_dominant_does_not_grievance_self():
    """When the player picks the DOMINANT proposal, the proposer is NOT overruled."""
    from app.game.engine import derive_resolution_consequences

    proposer = _agent(name="DominantProposer")
    state = _colony(
        agents=[proposer, _agent(name="B"), _agent(name="C"), _agent(name="D")]
    )
    dominant = _proposal(
        flavor=ProposalFlavor.DOMINANT,
        proposed_by_agent_id=proposer.id,
        pid="p_dom",
    )

    derived = derive_resolution_consequences(state, dominant, [dominant])

    grievance_calls = [
        tc
        for tc in derived
        if tc.name == "adjust_grievance"
        and tc.args.get("target_id") == str(proposer.id)
    ]
    assert not grievance_calls


def test_derive_no_payload_no_consequences():
    """Empty tool_calls_on_pick + no overrules = no derived consequences."""
    from app.game.engine import derive_resolution_consequences

    state = _colony()
    picked = _proposal(flavor=ProposalFlavor.WILDCARD, tool_calls_on_pick=[])

    derived = derive_resolution_consequences(state, picked, [picked])

    assert derived == [], f"expected no derived calls, got {derived}"


def test_derive_emitted_calls_have_caller_id_none():
    """All derived tool calls must be engine-emitted."""
    from app.game.engine import derive_resolution_consequences

    target = _agent(name="A")
    state = _colony(agents=[target, _agent(name="B"), _agent(name="C"), _agent(name="D")])
    nested = ToolCall(
        name="modify_health",
        caller_id=None,
        args={"target_id": str(target.id), "delta": -100, "reason": "x"},
    )
    picked = _proposal(flavor=ProposalFlavor.WILDCARD, tool_calls_on_pick=[nested])

    derived = derive_resolution_consequences(state, picked, [picked])
    assert all(tc.caller_id is None for tc in derived)
```

- [ ] **Step 4.2: Run; confirm fail**

```bash
cd backend
uv run pytest tests/test_engine.py -k derive_ -v
```

Expected: FAIL with `ImportError`.

- [ ] **Step 4.3: Implement the function**

Append to `backend/app/game/engine.py` (in section 5 or a new section 7 — keep ordering self-consistent):

```python
# ---------------------------------------------------------------------------
# Section 7: derive_resolution_consequences (Pb)
# ---------------------------------------------------------------------------

# Rule constants — taste-tunable, in band with SPEC §5.3 ranges (small
# orders 1-2, medium 5-8, extreme 15-25). Single severe action sits in the
# "extreme" band; resource drain is "medium"; overrule of a DOMINANT
# proposer is "small". Tests assert directional bands, not exact values.
_LEGIT_PER_50_HEALTH_DRAIN = -12  # severe health drain on a single target
_LEGIT_PER_30PCT_RESOURCE_DRAIN = -5  # ≥30%-of-max single-resource drain
_LEGIT_OVERRULE_DOMINANT = -2  # picking non-DOMINANT when a DOMINANT proposal existed
_GRIEVANCE_OVERRULE_DOMINANT = 2  # for the overruled DOMINANT proposer


def derive_resolution_consequences(
    state: ColonyState,
    picked: Proposal,
    all_proposals: list[Proposal],
) -> list[ToolCall]:
    """Pure: read picked.tool_calls_on_pick + cross-proposal context, emit
    a list of validated tool calls (caller_id=None) representing the
    consequence of the player's choice.

    Replaces the hardcoded _FLAVOR_LEGITIMACY_SHIFT in nodes.resolve. Every
    legitimacy / grievance change now flows through validate_tool_call so
    the Tool-Call Validity metric sees them. SPEC §6.7 / invariant #11.
    """
    derived: list[ToolCall] = []
    legit_delta = 0
    legit_reasons: list[str] = []

    # Rule 1: severe-health-drain on any target (advisor or player).
    for nested in picked.tool_calls_on_pick:
        if nested.name == "modify_health":
            delta = nested.args.get("delta", 0)
            if isinstance(delta, int) and delta <= -50:
                legit_delta += _LEGIT_PER_50_HEALTH_DRAIN
                tgt = nested.args.get("target_id", "?")
                legit_reasons.append(f"severe harm to {tgt}")

    # Rule 2: resource drain ≥30% of that resource's max.
    for nested in picked.tool_calls_on_pick:
        if nested.name == "modify_resource":
            rid = nested.args.get("resource_id")
            delta = nested.args.get("delta", 0)
            res = state.resources.get(rid) if isinstance(rid, str) else None
            if res is not None and isinstance(delta, int) and delta < 0:
                if abs(delta) >= 0.30 * res.max:
                    legit_delta += _LEGIT_PER_30PCT_RESOURCE_DRAIN
                    legit_reasons.append(f"heavy {rid} drain")

    # Rule 3: overruling a DOMINANT proposer. Triggered when (a) picked is
    # not DOMINANT and (b) some DOMINANT proposal exists in the triple.
    if picked.flavor != ProposalFlavor.DOMINANT:
        for prop in all_proposals:
            if (
                prop.flavor == ProposalFlavor.DOMINANT
                and prop.proposed_by_agent_id is not None
            ):
                proposer = _agent_by_id(state, prop.proposed_by_agent_id)
                if proposer is not None and proposer.is_alive:
                    derived.append(
                        ToolCall(
                            name="adjust_grievance",
                            caller_id=None,
                            args={
                                "target_id": str(proposer.id),
                                "delta": _GRIEVANCE_OVERRULE_DOMINANT,
                                "reason": "their dominant proposal was overruled",
                            },
                        )
                    )
                    legit_delta += _LEGIT_OVERRULE_DOMINANT
                    legit_reasons.append("overruled dominant advisor")

    # Emit one consolidated modify_legitimacy if any rule triggered.
    if legit_delta != 0:
        derived.append(
            ToolCall(
                name="modify_legitimacy",
                caller_id=None,
                args={
                    "delta": legit_delta,
                    "reason": "; ".join(legit_reasons) or "consequence",
                },
            )
        )

    return derived
```

- [ ] **Step 4.4: Run the new tests; confirm pass**

```bash
uv run pytest tests/test_engine.py -k derive_ -v
```

Expected: PASS.

- [ ] **Step 4.5: Composite scenario test (catches double-emit)**

Append:

```python
def test_derive_composite_severe_health_plus_overrule_stacks_cleanly():
    from app.game.engine import derive_resolution_consequences

    proposer = _agent(name="Vera")
    target = _agent(name="Marko")
    state = _colony(
        agents=[proposer, target, _agent(name="C"), _agent(name="D")]
    )
    dominant = _proposal(
        flavor=ProposalFlavor.DOMINANT,
        proposed_by_agent_id=proposer.id,
        pid="p_dom",
    )
    nested = ToolCall(
        name="modify_health",
        caller_id=None,
        args={"target_id": str(target.id), "delta": -100, "reason": "x"},
    )
    picked = _proposal(
        flavor=ProposalFlavor.WILDCARD,
        tool_calls_on_pick=[nested],
        pid="p_wild",
    )

    derived = derive_resolution_consequences(state, picked, [dominant, picked])

    legit_calls = [tc for tc in derived if tc.name == "modify_legitimacy"]
    grievance_calls = [tc for tc in derived if tc.name == "adjust_grievance"]
    assert len(legit_calls) == 1, "consolidate to one modify_legitimacy"
    assert len(grievance_calls) == 1, "one grievance hit per overruled proposer"
    assert legit_calls[0].args["delta"] <= -8, "severe-health stacks with overrule"
```

Run:

```bash
uv run pytest tests/test_engine.py::test_derive_composite_severe_health_plus_overrule_stacks_cleanly -v
```

Expected: PASS.

- [ ] **Step 4.6: Run ruff**

```bash
uv run ruff check app/game/engine.py tests/test_engine.py
uv run ruff format --check app/game/engine.py tests/test_engine.py
```

Expected: no findings.

- [ ] **Step 4.7: Commit**

```bash
git add backend/app/game/engine.py backend/tests/test_engine.py
git commit -m "feat(engine): payload-driven derive_resolution_consequences

Pure helper: (state, picked, all_proposals) -> list[ToolCall]. Reads
tool_calls_on_pick + cross-proposal context and emits validated
modify_legitimacy / adjust_grievance calls. Replaces flavor-label
dispatch (Pb)."
```

---

## Task 5: Wire `derive_resolution_consequences` into `nodes.resolve`; remove the invariant #11 exception

**Files:**
- Modify: `backend/app/agent/nodes.py`
- Modify: `backend/app/agent/CLAUDE.md`
- Modify: `backend/app/game/CLAUDE.md`
- Test: `backend/tests/test_nodes.py`

- [ ] **Step 5.1: Write failing test against the new resolve behavior**

Append to `backend/tests/test_nodes.py`:

```python
async def test_resolve_uses_payload_driven_consequences_not_flavor_label():
    """A WILDCARD with no real effects costs ≈0 legitimacy; a WILDCARD with
    severe-health payload craters legitimacy ≥10. Confirms Pb fix."""
    from app.agent.nodes import resolve
    from app.game.schemas import ProposalFlavor, ToolCall

    target = make_alive_agent(name="Marko")
    other_a = make_alive_agent(name="Vera")
    other_b = make_alive_agent(name="Sigrid")
    other_c = make_alive_agent(name="Torsten")
    state = make_colony_state(
        agents=[target, other_a, other_b, other_c],
        legitimacy=80,
    )

    benign = _make_proposal(
        flavor=ProposalFlavor.WILDCARD,
        proposed_by_agent_id=other_a.id,
        pid="benign",
        tool_calls_on_pick=[],
    )

    severe = _make_proposal(
        flavor=ProposalFlavor.WILDCARD,
        proposed_by_agent_id=other_a.id,
        pid="severe",
        tool_calls_on_pick=[
            ToolCall(
                name="modify_health",
                caller_id=None,
                args={"target_id": str(target.id), "delta": -100, "reason": "ice"},
            )
        ],
    )

    base_event = _make_event(severity=2, event_id="e1")

    # Run benign through resolve.
    benign_state_in = {
        "colony_state": state,
        "proposals": [benign],
        "player_choice": "benign",
        "current_event": base_event,
    }
    benign_out = await resolve(benign_state_in)

    # Run severe through resolve (fresh state).
    severe_state_in = {
        "colony_state": state,
        "proposals": [severe],
        "player_choice": "severe",
        "current_event": base_event,
    }
    severe_out = await resolve(severe_state_in)

    benign_legit = benign_out["colony_state"].legitimacy
    severe_legit = severe_out["colony_state"].legitimacy

    # Benign should be ≈unchanged (no payload, no overrules in single-proposal triple).
    assert benign_legit >= 78, f"benign WILDCARD cost too much: {80 - benign_legit}"
    # Severe must crater legitimacy by at least 10.
    assert severe_legit <= 70, f"severe WILDCARD did not crater legitimacy: {80 - severe_legit}"
```

You'll need `_make_proposal` to accept `pid` and `tool_calls_on_pick`. Check `tests/test_nodes.py:566` for the existing signature; if it doesn't already accept those, extend it:

```python
def _make_proposal(
    *,
    flavor: ProposalFlavor = ProposalFlavor.DOMINANT,
    proposed_by_agent_id=None,
    pid: str = "p1",
    tool_calls_on_pick: list | None = None,
) -> Proposal:
    return Proposal(
        id=pid,
        proposed_by_agent_id=proposed_by_agent_id,
        flavor=flavor,
        name=f"Proposal {pid}",
        description="x",
        effects_preview=[],
        tool_calls_on_pick=tool_calls_on_pick or [],
    )
```

(If the existing helper conflicts with this signature, rename your test's helper or extend the file's helper to accept these kwargs.)

- [ ] **Step 5.2: Run; confirm fail**

```bash
cd backend
uv run pytest tests/test_nodes.py::test_resolve_uses_payload_driven_consequences_not_flavor_label -v
```

Expected: FAIL — current `resolve` applies the flat `_FLAVOR_LEGITIMACY_SHIFT[WILDCARD] = -3` to both, so benign and severe diverge by 1 (passive decay differential isn't applied here since no `advance_turn`); the test asserts ≥10-point gap.

- [ ] **Step 5.3: Patch `nodes.resolve`**

In `backend/app/agent/nodes.py`:

1. Update the import on line 43-46 to include the new helper:

```python
from app.game import consequence, engine, events, setting_loader
from app.game.engine import derive_resolution_consequences
```

(or call it as `engine.derive_resolution_consequences` — pick one and stay consistent.)

2. Delete the `_FLAVOR_LEGITIMACY_SHIFT` dict (lines 78-85).

3. Replace the engine-direct legitimacy block (lines 252-259) with the derived-consequences fold. The new `resolve` body, replacing lines 240-259:

```python
    # Apply the picked proposal's nested effects + the engine's derived
    # consequences (legitimacy, grievance overrules) — all through the
    # single validated mutation pipeline. SPEC §6.7 / invariant #11.
    derived = derive_resolution_consequences(colony_state, picked, proposals)
    new_colony = engine.apply_tool_calls(
        colony_state,
        [*picked.tool_calls_on_pick, *derived],
    )

    # Append the resolved event to the colony's log.
    current_event = state["current_event"]
    resolved = ResolvedEvent(
        turn_number=new_colony.turn_number,
        event=current_event,
        chosen_proposal_id=picked.id,
        summary=picked.description,
    )
    new_colony = new_colony.model_copy(update={"event_log": [*new_colony.event_log, resolved]})
```

The consequence-seed scheduling block (lines 261-293) stays exactly as-is.

- [ ] **Step 5.4: Run the new resolve test; confirm pass**

```bash
uv run pytest tests/test_nodes.py::test_resolve_uses_payload_driven_consequences_not_flavor_label -v
```

Expected: PASS.

- [ ] **Step 5.5: Run the full nodes test suite + agent integration tests for regressions**

```bash
uv run pytest tests/test_nodes.py tests/test_graph.py tests/test_agent_router.py -v
```

Expected: all green. Pre-existing tests that asserted on `_FLAVOR_LEGITIMACY_SHIFT` magnitudes (if any) now need updating — search and fix any test that hard-coded `legitimacy == 60 - 3` style assertions, replacing with payload-driven equivalents or removing the assertion if the test was testing the wrong thing. Common patterns to grep:

```bash
grep -n "_FLAVOR_LEGITIMACY_SHIFT\|FLAVOR_LEGITIMACY" backend/tests/ backend/app/ -r
```

Expected: no remaining references.

- [ ] **Step 5.6: Update `backend/app/agent/CLAUDE.md`**

In `backend/app/agent/CLAUDE.md`, find the "Invariant #11 — mutation through validator" section. Replace the "**One documented exception**" paragraph with:

```markdown
**No exceptions.** Every state mutation, including legitimacy shifts, flows
through `engine.apply_tool_calls`. The `resolve` node calls
`engine.derive_resolution_consequences(state, picked, all_proposals)` to
turn the picked proposal's payload + cross-proposal context into a list of
validated tool calls (`modify_legitimacy`, `adjust_grievance`), then folds
them through `engine.apply_tool_calls` alongside `picked.tool_calls_on_pick`.
The previous flavor → legitimacy table (`_FLAVOR_LEGITIMACY_SHIFT`) was
removed in favour of payload-driven dispatch — see `engine.py` Section 7.
```

- [ ] **Step 5.7: Update `backend/app/game/CLAUDE.md`**

In `backend/app/game/CLAUDE.md`, in the "Tool-call → validator → apply pipeline (invariant #11)" section, append:

```markdown
## Engine-only tools

`tools.ENGINE_TOOL_NAMES` lists tools that do NOT appear in
`AGENT_TOOL_SCHEMAS` and that the LLM never authors. Validator rejects any
instance with `caller_id is not None` (reason: `engine_only_tool`). Current
members:

- `modify_legitimacy(delta, reason)` — emitted by
  `derive_resolution_consequences` when a picked proposal's payload or
  the cross-proposal context warrants a legitimacy shift.
- `set_advisor_status(target_id, new_status, reason)` — emitted by the
  engine at pick time when an advisor-status transition rides in
  `tool_calls_on_pick`. Allowed transitions and the cast-collapse floor
  live in `validator._validate_set_advisor_status`.

`set_advisor_status` is in `_NESTED_TOOL_WHITELIST` (LLM can include it in
a proposal's nested calls). `modify_legitimacy` is NOT — only the engine
emits it.
```

- [ ] **Step 5.8: Run ruff + full test suite (fast tier)**

```bash
uv run ruff check app/ tests/
uv run ruff format --check app/ tests/
uv run pytest tests/ -v -x --ignore=tests/integration --ignore=tests/eval
```

Expected: all green.

- [ ] **Step 5.9: Commit**

```bash
git add backend/app/agent/nodes.py backend/app/agent/CLAUDE.md backend/app/game/CLAUDE.md \
        backend/tests/test_nodes.py
git commit -m "fix(engine): payload-driven dispatch in resolve; remove invariant #11 exception

Removes _FLAVOR_LEGITIMACY_SHIFT and the engine-direct model_copy on
legitimacy. resolve now folds derive_resolution_consequences output and
picked.tool_calls_on_pick through engine.apply_tool_calls. Tool-Call
Validity metric now covers every legitimacy mutation."
```

---

## Task 6: Persona / colony-state respects status; cast filter excludes DISMISSED

**Files:**
- Modify: `backend/app/agent/prompts.py`
- Modify: `backend/app/agent/nodes.py`
- Test: `backend/tests/test_prompts.py`

- [ ] **Step 6.1: Write failing render and filter tests**

Append to `backend/tests/test_prompts.py`:

```python
def test_colony_state_block_renders_status_attribute():
    from app.agent.prompts import build_colony_state_block
    from app.game.schemas import AdvisorStatus
    from tests.test_engine import _agent, _colony

    detained = _agent(name="A", status=AdvisorStatus.DETAINED)
    state = _colony(agents=[detained, _agent(name="B"), _agent(name="C"), _agent(name="D")])

    text = build_colony_state_block(state)["text"]
    assert 'status="DETAINED"' in text
    assert 'status="ACTIVE"' in text  # the other three


def test_cast_filter_excludes_dismissed_for_persona_block():
    """Helper used by agent_debate / autonomous_actions: alive AND not DISMISSED."""
    from app.agent.nodes import filter_cast
    from app.game.schemas import AdvisorStatus
    from tests.test_engine import _agent, _colony

    a = _agent(name="A")
    b = _agent(name="B", status=AdvisorStatus.DETAINED)
    c = _agent(name="C", status=AdvisorStatus.DISMISSED)
    d = _agent(name="D", alive=False, health=0)
    state = _colony(agents=[a, b, c, d])

    cast = filter_cast(state.agents)
    names = {ag.name for ag in cast}
    assert names == {"A", "B"}, f"expected ACTIVE+DETAINED only, got {names}"
```

- [ ] **Step 6.2: Run; confirm fail**

```bash
cd backend
uv run pytest tests/test_prompts.py::test_colony_state_block_renders_status_attribute \
              tests/test_prompts.py::test_cast_filter_excludes_dismissed_for_persona_block -v
```

Expected: FAIL.

- [ ] **Step 6.3: Update `_render_agent_state` in `prompts.py`**

In `backend/app/agent/prompts.py:298-317`, replace `_render_agent_state` with:

```python
def _render_agent_state(agent: Agent) -> str:
    alive_flag = "true" if agent.is_alive else "false"
    status_value = agent.status.value if hasattr(agent.status, "value") else str(agent.status)
    lines = [
        f'<agent_state id="{agent.id}" name="{agent.name}" '
        f'grievance="{agent.grievance}" morale="{agent.morale}" '
        f'health="{agent.health}" is_alive="{alive_flag}" '
        f'status="{status_value}">',
    ]
    if agent.memory:
        lines.append("  memories:")
        for m in agent.memory:
            lines.append(f"    - {m}")
    else:
        lines.append("  memories: (none)")
    if agent.relationships:
        rel_parts = [f"→{tgt}: {score:+d}" for tgt, score in agent.relationships.items()]
        lines.append("  relationships: " + ", ".join(rel_parts))
    else:
        lines.append("  relationships: (none)")
    lines.append("</agent_state>")
    return "\n".join(lines)
```

The personas block (the cache-stable system block) stays untouched. Status is per-turn volatile; it lives in the user message via `build_colony_state_block`.

- [ ] **Step 6.4: Add `filter_cast` to `nodes.py` and use it**

In `backend/app/agent/nodes.py`:

1. Update imports to include `AdvisorStatus`:

```python
from app.game.schemas import AdvisorStatus, DecisionRef, ProposalFlavor, ResolvedEvent, SeedPayload
```

2. Add the helper near the top, after `AUTONOMOUS_TOOL_NAMES` (around line 76):

```python
def filter_cast(agents: list) -> list:
    """Authors of proposals / debate participants: alive AND not DISMISSED.

    DETAINED stays in the cast (the persona block is cache-stable on
    is_alive-only filtering; status is rendered per-turn via the colony
    state block, so the model knows when an advisor is detained without
    invalidating the personas cache). DISMISSED removes from cast: persona
    block does change, accepting one cache miss at the dismissal turn.
    """
    return [
        a for a in agents
        if a.is_alive and a.status != AdvisorStatus.DISMISSED
    ]
```

3. Replace the two existing `alive_agents = [a for a in colony_state.agents if a.is_alive]` lines (in `agent_debate` ~line 158 and `autonomous_actions` ~line 306) with:

```python
    alive_agents = filter_cast(colony_state.agents)
```

- [ ] **Step 6.5: Run the new tests; confirm pass**

```bash
uv run pytest tests/test_prompts.py::test_colony_state_block_renders_status_attribute \
              tests/test_prompts.py::test_cast_filter_excludes_dismissed_for_persona_block -v
```

Expected: PASS.

- [ ] **Step 6.6: Run full prompts/nodes/graph regressions**

```bash
uv run pytest tests/test_prompts.py tests/test_nodes.py tests/test_graph.py -v
```

Expected: all green. Some existing prompts/nodes tests may have hardcoded `<agent_state ...>` strings without `status="ACTIVE"` — update those to include the new attribute (or use a substring match that doesn't require attribute order).

- [ ] **Step 6.7: Run the full backend fast-tier suite**

```bash
uv run ruff check app/ tests/
uv run ruff format --check app/ tests/
uv run pytest tests/ -v -x --ignore=tests/integration --ignore=tests/eval
```

Expected: all green.

- [ ] **Step 6.8: Commit**

```bash
git add backend/app/agent/prompts.py backend/app/agent/nodes.py backend/tests/test_prompts.py
git commit -m "feat(agent): filter DISMISSED from cast; render status in colony block

Cast = alive AND not DISMISSED. DETAINED stays in cast; status surfaces
in the per-turn colony_state block (cache-stable persona block stays on
is_alive-only filtering). One persona-cache miss per dismissal turn —
acceptable trade-off for an event-rare transition."
```

---

## Phase 4 Hand-off (out of scope for this plan)

After Task 6 ships:

1. Run the playtest harness on a 3-run sample with deterministic seeds:
   - `arctic_station_v1` seed `1126193726` (the original detention probe)
   - `orbital_colony_v1` seed `1504826123` (the original Pb probe)
   - `desert_outpost_v1` (any deterministic seed; pick one with severity ≥4 events)

2. Verify acceptance gates:
   - Severe-health WILDCARD craters legitimacy ≥10 points (vs. flat −3 before)
   - At least one run with a `set_advisor_status(... DETAINED)` shows the detained advisor not authoring a proposal next turn
   - At least one run with `set_advisor_status(... DISMISSED)` shows a 3-author proposal triple after dismissal

3. Invoke `superpowers:requesting-code-review` before merging.

---

## Notes & Errors-and-Learnings (live document — append as you go)

### Task 3: cast-collapse floor was off-by-one in the plan

- **Symptom:** `test_set_advisor_status_dismiss_blocked_by_cast_floor` failed when the validator allowed dismissal with exactly 2 others remaining.
- **Root cause:** The plan's prose said "≥2 other ACTIVE-or-DETAINED advisors remaining" (allows when 2), but the same plan's test (and the user's acceptance criterion "subsequent turns produce 3-advisor proposal triples") imply ≥3 others are required to preserve SPEC §7.3's exactly-three-actions invariant with one advisor per proposal.
- **Fix:** Changed the validator threshold from `< 2` to `< 3`. The test was the source of truth; my prose was wrong.
- **File:** `backend/app/game/validator.py` `_validate_set_advisor_status`.

### Task 2: dropped a dangling `AGENT_TOOL_NAMES` import

- The plan had `from app.game.tools import AGENT_TOOL_NAMES, KNOWN_TOOL_NAMES` in `validator.py`, but `AGENT_TOOL_NAMES` was no longer referenced after the unknown-tool gate switched to `KNOWN_TOOL_NAMES`. Ruff caught it; dropped the unused name.

### Task 5: AdvisorStatus needs to be in the LangGraph checkpointer allowlist

- **Symptom:** `tests/test_checkpointer.py::test_round_trip_emits_no_unregistered_warnings` failed with `Blocked deserialization of app.game.schemas.AdvisorStatus`.
- **Root cause:** `app/agent/checkpointer.py` pins an explicit allowlist (`_ALLOWED_MSGPACK_TYPES`) of types the LangGraph checkpoint serializer accepts. Adding a new enum to `Agent` requires extending the list — the plan's File Map missed this.
- **Fix:** Added `("app.game.schemas", "AdvisorStatus")` to `_ALLOWED_MSGPACK_TYPES` in `app/agent/checkpointer.py`.
- **General lesson:** any new schema enum or class introduced into `ColonyState` must be added to this allowlist. Reasonable place to flag this in `backend/app/agent/CLAUDE.md`.

### Phase 4: filter_cast did not match the user's acceptance gate for DETAINED

- **Symptom:** Phase 1 brainstorming output said "DETAINED stays in cast (cache-stable)"; user's acceptance gate said "Detaining advisor X means X does not author a proposal next turn." Implementation matched the brainstorm, not the gate.
- **Root cause:** I optimized for cache stability (one fewer persona-block invalidation) over the explicit acceptance criterion. The brainstorm's nuanced "severity 4+ exception" never got built; only the colony-state status rendering did.
- **Fix:** `filter_cast` in `backend/app/agent/nodes.py` now returns `alive AND status == ACTIVE` only. DETAINED and DISMISSED both excluded. Cost: one cache miss when an advisor enters/leaves DETAINED (rare event; acceptable).
- **Test update:** `test_cast_filter_excludes_dismissed_for_persona_block` renamed to `test_cast_filter_includes_only_active_alive_advisors` with updated assertion `names == {"A"}`.
- **General lesson:** when a brainstormed design conflicts with an explicit acceptance gate, the gate wins. Verify the gate against the implementation before claiming Phase 3 done — don't wait for Phase 4 to surface this.
