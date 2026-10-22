# Flat Debate Format Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The debate and the off-turn (autonomous) call each become one forced, structured tool call that cheap models handle reliably, and every LLM call records its billed cost.

**Architecture:** A new `app/agent/debate_format.py` builds per-game tool schemas (`submit_debate`, `submit_offturn`) and adapts their single tool call back into the `DebateParse` / `ToolCall` shapes the engine already consumes, so the validator, preparation and invariants are untouched. Nodes call the model with a forced `tool_choice`, falling back to `auto` when a host rejects forced tools. `cost_cents_of` prefers OpenRouter's billed `usage.cost`, and each call's usage lands in `turn_meta["llm_calls"]`.

**Tech Stack:** Python 3.12, `anthropic` SDK against OpenRouter's Anthropic-compatible Messages endpoint, pytest (`asyncio_mode = "auto"`), ruff.

**Spec:** `docs/superpowers/specs/2026-10-22-outpost-evolved-design.md` §6 W1b, plus the evidence in §14. The format being built is the one that won the 2026-10-22 format test ("flat"): the full preamble with its response contract swapped, forced single tool, enums for advisor names and resource ids, and no per-effect reason from the model. The bake-off kit holds the tested spike: `FLAT_CONTRACT`, `flat_tool`, `flat_user` and `adapt_flat`. It is gitignored and lives only in the main checkout, at `/Users/luclacombe/Documents/Outpost/docs/playtest/2026-10-22-model-bakeoff/kit/bakeoff.py`.

## Global Constraints

- Invariants (root `CLAUDE.md`) hold: #2 no numbers from free text (effects arrive as typed tool input), #5 prompt caching (stable preamble first, `cache_control` kept), #6 one batched debate call per attempt, #11 every state change is a validated tool call, #12 thread-id discipline (untouched).
- `app/game/` is not modified. The engine, validator and `prepare_proposals` stay as they are.
- Unit tests never call a real LLM; follow the `_FakeCall` / `monkeypatch.setattr(nodes, "call_fast", fake)` pattern in `tests/test_nodes.py`.
- Run both `uv run ruff check app/ tests/` and `uv run ruff format --check app/ tests/` before every commit, from `backend/`. 100-char lines, type hints everywhere.
- Conventional Commits; add explicit paths (never `git add -A`); end each commit message with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Work only in `/Users/luclacombe/Documents/Outpost/.claude/worktrees/flat-debate-format` on branch `feat/flat-debate-format`.

## Review Focus

1. A host rejects forced `tool_choice` (OpenRouter returns 400 or 404 mentioning tool choice, tool compatibility or "No endpoints found"): the call must retry once with `tool_choice` omitted, not fail the turn. Pinned in Task 5.
2. The model answers with text only and no tool call: parsing returns an empty `DebateParse`, which triggers the existing retry rather than raising. Pinned in Task 2.
3. Advisor names arrive in a different case, or name a dead or unknown advisor: matching is case-insensitive; unknown reactions are dropped and unknown proposers get `caller_id=None`. Pinned in Task 2.
4. A provider ignores the schema enums and sends an unknown resource id: the adapter passes it through and `prepare_proposals` strips it as a rejected nested call. Pinned in Task 2.
5. Non-Latin script appears inside a reaction or option (seen once from MiMo: "頻道"): that reaction or option is dropped and logged. Pinned in Task 2 and Task 4.

---

### Task 1: Billed cost and a per-call usage record

**Files:**
- Modify: `backend/app/agent/anthropic_client.py` (`cost_cents_of`, new `usage_record`)
- Modify: `backend/app/agent/constants.py` (model ids and `MODEL_PRICING`)
- Test: `backend/tests/test_anthropic_client.py`

**Interfaces:**
- Produces: `cost_cents_of(response: Any, model: str) -> float` (unchanged signature, new source preference); `usage_record(response: Any, model: str, purpose: str) -> dict[str, Any]` with keys `purpose`, `model`, `input_tokens`, `cache_read_tokens`, `cache_write_tokens`, `output_tokens`, `cost_cents`, `billed` (bool: True when the cost came from the provider). Constants `MODEL_MIMO_V2_6_FLASH = "xiaomi/mimo-v2.6-flash"`, `MODEL_DEEPSEEK_V4_1_FLASH_1017 = "deepseek/deepseek-v4.1-flash-1017"`, `MODEL_GPT_6_LUNA = "openai/gpt-6-luna"`.

- [ ] **Step 1: Write the failing tests**

```python
def test_cost_prefers_billed_cost_when_provider_reports_it():
    usage = _usage(input_tokens=1000, output_tokens=500, extra={"cost": 0.0033})
    assert cost_cents_of(_resp(usage), "deepseek/deepseek-v4-pro-0813") == pytest.approx(0.33)

def test_cost_falls_back_to_price_table_without_billed_cost():
    usage = _usage(input_tokens=1_000_000, output_tokens=0, extra={})
    assert cost_cents_of(_resp(usage), "xiaomi/mimo-v2.6-flash") == pytest.approx(14.0)

def test_usage_record_fields():
    usage = _usage(input_tokens=100, output_tokens=50, cache_read=4000, extra={"cost": 0.001})
    rec = usage_record(_resp(usage), "openai/gpt-6-luna", "debate")
    assert rec == {"purpose": "debate", "model": "openai/gpt-6-luna", "input_tokens": 100,
                   "cache_read_tokens": 4000, "cache_write_tokens": 0, "output_tokens": 50,
                   "cost_cents": pytest.approx(0.1), "billed": True}

@pytest.mark.parametrize("model", ["xiaomi/mimo-v2.6-flash", "deepseek/deepseek-v4.1-flash-1017",
                                   "openai/gpt-6-luna"])
def test_new_models_have_prices(model):
    assert pricing_for(model) is not None
```

`_usage` builds a stand-in whose `model_extra` holds `extra` (the anthropic SDK keeps unknown usage fields there, which is where OpenRouter's `cost` in USD arrives; confirmed in a live response on 2026-10-22).

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/test_anthropic_client.py -v`
Expected: the four new tests FAIL (`usage_record` undefined, unknown models).

- [ ] **Step 3: Implement**

`cost_cents_of`: if `getattr(response.usage, "model_extra", None)` has a numeric `cost`, return `cost * 100`; otherwise the existing table formula. Add pricing in cents per million: MiMo `ModelPricing(input=14, cache_write=14, cache_read=0.28, output=28)`; V4.1 Flash 1017 `(15, 15, 0.3, 60)`; GPT-6 Luna `(10, 10, 1, 50)` (OpenRouter `/api/v1/models`, fetched 2026-10-22). Update the `cost_cents_of` docstring to say billed cost wins.

- [ ] **Step 4: Run the tests and lint**

Run: `uv run pytest tests/test_anthropic_client.py tests/test_llm_provider.py -v && uv run ruff check app/ tests/ && uv run ruff format --check app/ tests/`
Expected: all PASS, ruff clean.

- [ ] **Step 5: Commit** `feat(agent): prefer provider-billed cost and add per-call usage records`

---

### Task 2: The `submit_debate` tool and its parser

**Files:**
- Create: `backend/app/agent/debate_format.py`
- Modify: `backend/app/agent/narration_guard.py` (add `has_non_latin`)
- Test: `backend/tests/test_debate_format.py`, `backend/tests/test_narration_guard.py`

**Interfaces:**
- Consumes: `DebateParse` from `app.agent.prompts`; `ToolCall`, `Agent` from `app.game.schemas`.
- Produces: `DEBATE_TOOL_NAME = "submit_debate"`; `build_debate_tool(alive_agents: list[Agent], resource_ids: list[str]) -> dict[str, Any]`; `parse_submit_debate(response: Any, alive_agents: list[Agent]) -> DebateParse`; `has_non_latin(text: str) -> bool` in `narration_guard.py`.

`build_debate_tool` returns the spike's `flat_tool` schema exactly. Top level: `reactions` (array of `{advisor, line}`) and `options` (array, `minItems` 3, `maxItems` 3). Each option has `flavor` (enum `DOMINANT`/`MORAL`/`WILDCARD`), `advisor`, `title`, `description`, optional `risk_note`, and `effects` (1–5 items). Each effect is `{kind: resource|health|grievance, resource, target, delta}`, with `delta` an integer. Enums: advisor names from `alive_agents`; resource ids from `resource_ids`; `target` takes the names plus the literal `"player"`.

`parse_submit_debate` maps the first `submit_debate` tool_use block, as `adapt_flat` does:
- Reactions map by advisor name, case-insensitive.
- Each option becomes `ToolCall(name="propose_action", caller_id=<advisor id or None>, args={"name": title, "description", "flavor", "risk_note": risk_note or "", "tool_calls_on_pick": [...]})`.
- Each effect becomes a nested `{"name": "modify_resource" | "modify_health" | "adjust_grievance", "args": {...}}`. Every nested call gets `"reason": title[:60]`, because the validator requires a reason. Health and grievance targets resolve a name to `str(agent.id)`, and `"player"` stays literal.
- A reaction whose line, or an option whose title or description, contains non-Latin letters is dropped and logged at warning level.

- [ ] **Step 1: Write the failing tests** (fake responses are `SimpleNamespace(content=[SimpleNamespace(type="tool_use", name="submit_debate", input={...})])`; agents from `tests/_factories.py`)

```python
def test_schema_enums_hold_roster_and_resources(): ...
    # tool["input_schema"]["properties"]["options"]["items"]["properties"]["advisor"]["enum"] == names
    # effect "resource" enum == resource_ids; "target" enum == names + ["player"]; options minItems == maxItems == 3

def test_parse_maps_reactions_and_options():
    # 4 reactions, 3 options -> per_agent_text has 4 ids; 3 propose_action ToolCalls with caller ids;
    # a resource effect becomes {"name": "modify_resource", "args": {"resource_id": "fuel", "delta": -5, "reason": <title[:60]>}}

def test_parse_is_case_insensitive_and_drops_unknown_names():
    # reaction advisor "vera" maps to Vera; reaction from "Nobody" is dropped; option from "Nobody" has caller_id None

def test_parse_health_target_player_and_agent():
    # target "player" -> target_id "player"; target "Vera" -> str(vera.id)

def test_parse_passes_unknown_resource_through_for_prepare_to_strip():
    # effect resource "unobtainium" -> nested modify_resource with resource_id "unobtainium";
    # prepare_proposals(state, parsed.tool_calls, setting) classifies that proposal "no_effect" or "degraded", not a crash

def test_parse_text_only_response_is_empty():
    # content=[SimpleNamespace(type="text", text="I refuse")] -> DebateParse() with no reactions, no calls

def test_parse_drops_non_latin_reaction_and_option():
    # reaction "Vorgrad's 頻道 is bleeding static" dropped; option with Cyrillic title dropped

def test_has_non_latin():  # in test_narration_guard.py
    assert has_non_latin("plain words, café") is False
    assert has_non_latin("static on the 頻道") is True
```

- [ ] **Step 2: Run them to see them fail** — `uv run pytest tests/test_debate_format.py tests/test_narration_guard.py -v` → new tests FAIL (module missing).

- [ ] **Step 3: Implement** `debate_format.py` and `has_non_latin` (true when any alphabetic character's `unicodedata.name` does not start with `LATIN`; reuse the logic behind `_non_latin_share`).

- [ ] **Step 4: Run tests and lint** — same two pytest files plus `tests/test_prepare.py`, then both ruff commands. Expected PASS, clean.

- [ ] **Step 5: Commit** `feat(agent): submit_debate tool schema and parser`

---

### Task 3: Prompt text for the flat contract

**Files:**
- Modify: `backend/app/agent/prompts.py` (`_PREAMBLE_TEXT`, `build_debate_user_message`, `build_autonomous_user_message`)
- Test: `backend/tests/test_prompts.py`

**Interfaces:**
- Produces: the same builder signatures; new text only.

**Preamble changes:**
- Replace everything from `RESPONSE CONTRACT (obey exactly):` up to, but not including, `DEBATE TARGETS` with the spike's `FLAT_CONTRACT` text, edited as follows:
  - Its first bullet becomes: "In the debate, reply with exactly one call to the `submit_debate` tool and nothing else: no prose, no XML, no other tools."
  - Add a bullet: "Off-turn, reply with exactly one call to `submit_offturn` (see OFF-TURN below)."
  - Keep the example option as written.
- In DEBATE TARGETS, replace "Exactly three `propose_action` calls across the living advisors" with "Exactly three options across the living advisors".
- In OFF-TURN (AUTONOMOUS) PHASE:
  - Replace the opening sentence with: "Between turns, reply with one call to `submit_offturn` listing zero or more actions. Each action's `kind` is one of:"
  - Replace the closing paragraph ("Off-turn tool calls do NOT need ...") with: "Off-turn actions never move resources or health."
  - Leave the six tool descriptions as they are; their names are the `kind` values.

**User messages:**
- `build_debate_user_message` returns the spike's `flat_user` text.
- `build_autonomous_user_message` keeps the `<turn_summary>` wrapper and ends with: "Call submit_offturn once. List an action only where an advisor has a reason to act; an empty list is fine."

- [ ] **Step 1: Failing tests**

```python
def test_preamble_describes_flat_contract():
    t = build_preamble_block()["text"]
    assert "submit_debate" in t and "submit_offturn" in t
    assert "tool_calls_on_pick" not in t and "caller_agent_id" not in t and "<agent name=" not in t

def test_debate_user_message_asks_for_one_call():
    assert "Call submit_debate once" in build_debate_user_message(_event())

def test_autonomous_user_message_asks_for_submit_offturn():
    assert "submit_offturn" in build_autonomous_user_message("The colony chose to ration.")
```

Update or delete existing `test_prompts.py` assertions that pin the old XML or `propose_action` wording; say which ones in the commit body.

- [ ] **Step 2: Run to fail**, **Step 3: implement**, **Step 4:** `uv run pytest tests/test_prompts.py -v` plus both ruff commands → PASS, clean.
- [ ] **Step 5: Commit** `feat(agent): flat response contract in the preamble and user messages`

---

### Task 4: The `submit_offturn` tool and its parser

**Files:**
- Modify: `backend/app/agent/debate_format.py`
- Test: `backend/tests/test_debate_format.py`

**Interfaces:**
- Produces: `OFFTURN_TOOL_NAME = "submit_offturn"`; `build_offturn_tool(alive_agents: list[Agent]) -> dict[str, Any]`; `parse_submit_offturn(response: Any, alive_agents: list[Agent], decision_id: str | None) -> list[ToolCall]`.

**Schema:** `actions`, an array with `maxItems` 8, of `{advisor (enum names), kind, text (string), target (enum names), delta (integer)}`.
- `required` is `["advisor", "kind"]`.
- `kind` is an enum of `remember`, `adjust_own_grievance`, `speak_to_agent`, `adjust_relationship`, `back_leader`, `initiate_coup`.

**Mapping to `ToolCall(name=kind, caller_id=advisor id, args=...)`:**

| kind | args |
|---|---|
| remember | `{"fact": text[:200]}` |
| adjust_own_grievance | `{"delta": delta, "reason": text[:200] or "off-turn"}` |
| speak_to_agent | `{"target_id": str(target.id), "message": text[:300]}` |
| adjust_relationship | `{"target_id": str(target.id), "delta": delta}` |
| back_leader | `{"decision_id": decision_id, "legitimacy_bonus": min(max(delta or 1, 1), 5)}`; skipped when `decision_id` is None |
| initiate_coup | `{}` |

**Skipped actions:** an unknown advisor, a missing or unknown target where one is needed, or `text` containing non-Latin letters.

- [ ] **Step 1: Failing tests:**
  - `test_offturn_schema_enums`
  - `test_offturn_maps_each_kind`: one action per kind, checking the exact args above
  - `test_offturn_empty_list_gives_no_calls`
  - `test_offturn_skips_unknown_target_and_non_latin_text`
  - `test_offturn_back_leader_needs_decision_id`
- [ ] **Step 2–4:** fail, implement, then pass with `uv run pytest tests/test_debate_format.py -v` and both ruff commands.
- [ ] **Step 5: Commit** `feat(agent): submit_offturn tool schema and parser`

---

### Task 5: Wire the nodes to forced structured calls

**Files:**
- Modify: `backend/app/agent/anthropic_client.py` (`call_fast` gains `tool_choice: dict[str, Any] | None = None`, passed through only when not None)
- Modify: `backend/app/agent/nodes.py` (`agent_debate`, `autonomous_actions`, `narrate_turn`, `_DEBATE_RETRY_NOTE`)
- Test: `backend/tests/test_nodes.py`

**Interfaces:**
- Consumes: Task 1 `usage_record`; Task 2 `build_debate_tool`, `parse_submit_debate`; Task 4 `build_offturn_tool`, `parse_submit_offturn`.
- Produces: `async def _call_structured(system_blocks: list, user_message: str, tool: dict[str, Any], max_tokens: int) -> tuple[Any, str]` in `nodes.py`.
  - It calls `call_fast` with `tools=[tool]` and `tool_choice={"type": "tool", "name": tool["name"]}`.
  - On `anthropic.BadRequestError` or `anthropic.NotFoundError` whose message, lowercased, contains `tool_choice`, `tool choice`, `tool compat` or `no endpoints`, it calls once more without `tool_choice`.
  - It returns `(response, "forced" | "auto")`. Any other error propagates.
- New `turn_meta` keys: `"debate_tool_choice"` (`"forced"` or `"auto"`) and `"llm_calls"` (a list of `usage_record` dicts, appended by every LLM call in the turn, in order).

**`agent_debate`:**
- Uses `_call_structured` with `build_debate_tool(alive_agents, list(colony_state.resources))` and `max_tokens=2000`, and parses with `parse_submit_debate`.
- The retry rule is unchanged (fewer than 3 valid, under the cost cap).
- New `_DEBATE_RETRY_NOTE`: `"\n\n<retry_note>Your previous reply had {valid} valid option(s). Call submit_debate once with exactly 3 options (one DOMINANT, one MORAL, one WILDCARD), each with a non-empty title, description and at least one effect.</retry_note>"`

**`autonomous_actions`:**
- Uses `_call_structured` with `build_offturn_tool(alive_agents)` and `max_tokens=1500`.
- Parses with `parse_submit_offturn(response, alive_agents, decision_id)`, where `decision_id` is `colony_state.event_log[-1].chosen_proposal_id` (or None when the log is empty).
- Keeps the `AUTONOMOUS_TOOL_NAMES` filter as the safety net.
- Update the comment about passing the full tool list.

**`narrate_turn`:** appends its `usage_record` to `turn_meta["llm_calls"]`.

**Cleanup:** remove the now-unused `parse_debate_response` import from `nodes.py`. Leave the function in `prompts.py`; `service.py` references it in a comment.

- [ ] **Step 1: Failing tests** (extend `_FakeCall` to record kwargs and optionally raise a queued exception):

```python
async def test_debate_forces_submit_debate(): ...
    # first call kwargs: tools[0]["name"] == "submit_debate", tool_choice == {"type": "tool", "name": "submit_debate"}

async def test_debate_falls_back_to_auto_when_host_rejects_forced_tool(): ...
    # first call raises anthropic.BadRequestError with message "tool_choice is not supported";
    # second call has no tool_choice; turn_meta["debate_tool_choice"] == "auto"

async def test_debate_other_errors_propagate(): ...
    # BadRequestError("context length exceeded") is raised, not retried

async def test_debate_retry_still_fires_on_fewer_than_three_valid(): ...
    # first response has 2 options, second has 3 -> two calls, kept the retry, turn_meta["debate_retried"] is True

async def test_llm_calls_recorded_for_debate_autonomous_and_narration(): ...
    # turn_meta["llm_calls"] purposes == ["debate", "autonomous", "narration"] for a full mocked turn

async def test_autonomous_applies_parsed_offturn_actions(): ...
    # a remember action for a living advisor lands in that advisor's memory via the engine (phase "autonomy")
```

- [ ] **Step 2: Run to fail** — `uv run pytest tests/test_nodes.py -v`.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Full suite and lint** — `uv run pytest tests/ -q` (all pass; the integration and eval markers stay deselected) and both ruff commands.
- [ ] **Step 5: Commit** `feat(agent): forced structured calls for debate and off-turn, with per-call usage`

---

### Task 6: Docs and config

**Files:**
- Modify: `backend/app/agent/CLAUDE.md`. Replace the parts that describe XML reactions, nested `propose_action`, and the "full tool list for the 4096-token cache minimum" rule. Add a short "Structured calls" section covering:
  - the forced single tool per call, with the fallback to `auto`;
  - per-game enums, so the tool schema is stable within a game only;
  - billed cost preferred, and `turn_meta["llm_calls"]`;
  - the non-Latin filter.
- Modify: root `CLAUDE.md`. Fix the LLM line: debate and autonomous run on `LLM_MODEL_FAST` (V4 Pro today) and narration on V4.1 Flash; the flat format makes cheaper models viable (bake-off 2026-10-22).
- Modify: `backend/.env.example`. Add a comment listing the models tested on 2026-10-22: `xiaomi/mimo-v2.6-flash`, `deepseek/deepseek-v4.1-flash-1017`, `openai/gpt-6-luna`.
- Modify: `docs/PLAN.md` Errors & Learnings. One entry:
  - **Symptom:** cheap models failed the debate.
  - **Cause:** the format needed free text plus three parallel tool calls; many models make one call and stop, or skip the text.
  - **Fix:** one forced structured tool.
  - **Files:** the ones changed above.

- [ ] **Step 1:** make the edits. **Step 2:** run `uv run ruff check app/ tests/` and `uv run ruff format --check app/ tests/` (no code change expected). **Step 3: Commit** `docs: structured-call format, billed cost and model notes`

---

## Self-review notes

- **Spec coverage:** W1b step 2 is covered by Tasks 2–5. The flat schema, debate-only tool, adapter-supplied reasons, no UUIDs in model output, and the shortfall retry wording all have a task. W1b step 1, "measure exactly", is covered by Task 1. The non-Latin guard for reactions (§6 W4 bullet) is pulled forward into Tasks 2 and 4.
- **Not in this plan, and why:**
  - Removing the autonomous call: that is W4.
  - Jev: W4.
  - Trimming the preamble: tested worse on 2026-10-22.
  - Model choice: it stays configuration and is decided by the full-game runs that follow this plan.
