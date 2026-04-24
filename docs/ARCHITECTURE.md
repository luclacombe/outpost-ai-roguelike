# Outpost — Technical Architecture

**Version:** 1.0  
**Last updated:** 2026-04-23  
**Status:** Approved for implementation

> This document is the source of truth for HOW Outpost is built. For what it is, see `SPEC.md`. For phased build order, see `PLAN.md`.

---

## 1. System Overview

```
┌───────────────────────────────────────────────────────────────────────┐
│                               BROWSER                                  │
│  ┌─────────────────────────────────────────────────────────────────┐  │
│  │ Next.js 16 (App Router) · Tailwind v4 · shadcn/ui · DiceBear    │  │
│  │                                                                  │  │
│  │  Landing     Play (gated)    Run (dashboard)    Post-mortem     │  │
│  └──────────────────────────┬──────────────────────────────────────┘  │
└─────────────────────────────┼──────────────────────────────────────────┘
                              │ HTTPS + SSE
                              │ Authorization: Bearer <Supabase JWT>
                              ▼
┌───────────────────────────────────────────────────────────────────────┐
│                     FastAPI Backend (Railway)                          │
│                                                                        │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐              │
│  │   auth   │  │   game   │  │  agent   │  │middleware│              │
│  │ (JWT     │  │ (turn    │  │(LangGraph│  │(CORS,    │              │
│  │ validate)│  │  engine) │  │ + raw SDK│  │ auth,    │              │
│  │          │  │          │  │ nodes)   │  │ logging) │              │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘  └────┬─────┘              │
│       │             │             │             │                     │
│       └─────────────┴──────┬──────┴─────────────┘                     │
│                            │                                           │
│                     SQLAlchemy async                                   │
│                            │                                           │
└────────────────────────────┼───────────────────────────────────────────┘
            │                │                     │
            ▼                ▼                     ▼
    ┌───────────────┐ ┌──────────────┐ ┌─────────────────────┐
    │  Supabase     │ │  Anthropic   │ │   Langfuse (cloud)  │
    │  Postgres     │ │  API         │ │   + LangSmith CI    │
    │  + Auth       │ │  (Haiku/     │ │   (traces + evals)  │
    │  (Google      │ │   Sonnet)    │ │                     │
    │   OAuth)      │ │              │ │                     │
    └───────────────┘ └──────────────┘ └─────────────────────┘
```

### Request paths

1. **Unauthenticated landing** — Next.js SSR, public, no backend calls.
2. **Sign in** — Supabase Auth Google OAuth flow; token stored in HTTP-only cookie client-side.
3. **Start a run** — `POST /game/session` from Next.js → FastAPI validates JWT, checks daily gate, generates setting + agents, returns `game_id`.
4. **Play a turn** — `POST /agent/turn` returns a Server-Sent Events stream of LangGraph state transitions and final choices.
5. **Submit choice** — `POST /game/{id}/decide` returns the resolved state.
6. **Finish** — `GET /game/{id}/post-mortem` returns the timeline + counterfactuals.

---

## 2. Stack Summary

| Layer | Choice | Notes |
|-------|--------|-------|
| Frontend framework | Next.js 16 (App Router) | RSC where useful, client components for dashboard interactivity |
| Language (FE) | TypeScript strict | Matches user's existing preferences |
| Styling | Tailwind CSS v4 + shadcn/ui (re-themed) | Heavy re-theme to pixel-art aesthetic |
| Portraits | @dicebear/pixel-art + @dicebear/thumbs | Seeded, SVG data URIs |
| Backend framework | FastAPI 0.115+ | Python 3.12+, async-native |
| Agent orchestration | LangGraph (state graph) | Nodes call raw Anthropic SDK, not ChatAnthropic |
| LLM SDK | `anthropic` Python SDK | Direct calls for cache/batch control |
| Database | Supabase Postgres | Accessed via SQLAlchemy async + psycopg3, direct connection port 5432 |
| Auth | Supabase Auth (Google OAuth) | JWTs validated locally in FastAPI via PyJWT (HS256) |
| Migrations | Alembic (game tables) + Supabase CLI (auth/RLS) | Parallel migration trees |
| Package management | uv (Python) + pnpm (Node) | Fast installs, lock files |
| Linting | ruff (Python) + eslint (TS) | ruff replaces black/flake8/isort |
| Containerization | Docker + Docker Compose | FastAPI container; Supabase runs via CLI |
| Deploy (BE) | Railway | Free $5 credit/mo, Dockerfile auto-detect, no cold starts |
| Deploy (FE) | Vercel | Existing preference, Next.js native |
| CI/CD | GitHub Actions | Build + test + deploy pipeline |
| Observability | Langfuse (cloud Hobby) | 50K obs/mo free, LangGraph callback support |
| Eval CI | LangSmith `agentevals` + pytest | Trajectory evals, GitHub Actions integration |
| Error tracking | structlog (JSON logs via Railway/Vercel) | No Sentry in MVP |
| Feature flags | Env vars + hard-coded constants | No LaunchDarkly in MVP |

---

## 3. Agent Architecture

### 3.1 LangGraph as orchestrator, raw SDK for LLM calls

The decision from research (see `research/01-agent-architecture.md`): LangGraph provides the state graph structure (nodes, edges, checkpointing, visualization in Langfuse); each node implements its behavior by calling the Anthropic SDK directly rather than through LangChain's `ChatAnthropic` wrapper.

**Why this split:**
- LangGraph gives state management, parallelism primitives, and built-in observability integration.
- Raw SDK gives precise control over prompt caching (`cache_control` blocks), debate batching (XML-structured multi-persona prompts), and token budgeting.
- LangChain's `ChatAnthropic` wrapper obscures both of these.

### 3.2 The StateGraph

```python
# app/agent/graph.py (sketch)
from langgraph.graph import StateGraph, END
from app.agent.state import GameTurnState
from app.agent.nodes import (
    generate_event,
    agent_debate,
    apply_tool_calls,
    resolve_player_choice,
    autonomous_actions,
    check_deaths,
    narrate_turn,
    advance_turn,
)

builder = StateGraph(GameTurnState)

builder.add_node("generate_event", generate_event)
builder.add_node("agent_debate", agent_debate)
builder.add_node("apply_tool_calls", apply_tool_calls)
builder.add_node("await_player", lambda s: s)  # pause point for SSE pagination
builder.add_node("resolve", resolve_player_choice)
builder.add_node("autonomous", autonomous_actions)
builder.add_node("check_deaths", check_deaths)
builder.add_node("narrate", narrate_turn)
builder.add_node("advance", advance_turn)

builder.set_entry_point("generate_event")
builder.add_edge("generate_event", "agent_debate")
builder.add_edge("agent_debate", "apply_tool_calls")
builder.add_edge("apply_tool_calls", "await_player")
# await_player is terminal for this invocation; client posts choice to resume
builder.add_edge("resolve", "autonomous")
builder.add_edge("autonomous", "check_deaths")
builder.add_edge("check_deaths", "narrate")
builder.add_edge("narrate", "advance")
builder.add_conditional_edges(
    "advance",
    lambda s: "end_of_run" if s.is_run_over else "generate_event",
    {"end_of_run": END, "generate_event": "generate_event"},
)

graph = builder.compile(checkpointer=postgres_saver)
```

The graph has a pause point after `apply_tool_calls` where control returns to the client with the proposed choices. The client POSTs the player's decision, which resumes the graph from `resolve`. This split is what lets the SSE stream be finite per request.

### 3.3 Turn flow (expanded)

```
┌───────────────────┐
│  Request arrives  │  POST /agent/turn
│  load GameTurnState from DB (by game_id)
└─────────┬─────────┘
          ▼
┌───────────────────────┐     ┌─────────────────────────────┐
│ 1. generate_event()   │◄────│ setting.event_pool          │
│    pick event, write  │     │ respect consequence seeds   │
│    into state         │     │ respect cooldown window     │
└─────────┬─────────────┘     └─────────────────────────────┘
          ▼
┌───────────────────────┐
│ 2. agent_debate()     │  ONE Anthropic call
│    batched XML prompt │  ~3k input tok, ~1.2k output tok
│    all 4 advisors     │  prompt-cached personality layers
│    return proposals   │  + cached colony state
└─────────┬─────────────┘
          ▼
┌───────────────────────┐
│ 3. apply_tool_calls() │  engine validates each tool call
│    reject invalid     │  emit `tool_valid` observability
│    mutate state       │  event per call
└─────────┬─────────────┘
          ▼
┌───────────────────────┐
│ 4. await_player       │  return SSE final event with
│  (graph pauses here)  │  3 proposed choices; client
│                       │  shows them, player picks
└─────────┬─────────────┘
          ▼
┌───────────────────────┐  POST /game/{id}/decide
│ 5. resolve()          │  apply choice, schedule
│                       │  consequence seeds, adjust
│                       │  resources/grievance
└─────────┬─────────────┘
          ▼
┌───────────────────────┐
│ 6. autonomous()       │  ONE Anthropic call (batched)
│    off-turn actions   │  agents may adjust own grievance,
│                       │  write to memory, speak privately
└─────────┬─────────────┘
          ▼
┌───────────────────────┐
│ 7. check_deaths()     │  pure Python; remove dead
│                       │  agents, pin memories
└─────────┬─────────────┘
          ▼
┌───────────────────────┐
│ 8. narrate()          │  ONE Sonnet call (optional
│    (Sonnet, optional) │  per cost budget); writes
│                       │  turn summary paragraph
└─────────┬─────────────┘
          ▼
┌───────────────────────┐
│ 9. advance()          │  turn_number++, check win/fail
│                       │  if run over → END node
└───────────────────────┘
```

**Total LLM calls per turn:** 2 (Haiku, batched) + 1 (Sonnet, optional) = **2–3 calls**

Compare to naive per-agent: 4 (debate) + 2–3 (tool refinement) + 2 (autonomous) = 8–9 calls.

### 3.4 Node implementation pattern

Each node is an async function that accepts and returns `GameTurnState`:

```python
# app/agent/nodes.py (sketch)
async def agent_debate(state: GameTurnState) -> GameTurnState:
    event = state.current_event
    agents = [a for a in state.agents if a.is_alive]
    
    # Build the batched XML prompt
    system_blocks = [
        {"type": "text", "text": STATIC_DEBATE_PREAMBLE,
         "cache_control": {"type": "ephemeral"}},
        *[build_agent_persona_block(agent) for agent in agents],  # cached
        {"type": "text", "text": build_colony_state_block(state),
         "cache_control": {"type": "ephemeral"}},  # cached per turn
    ]
    
    user_message = build_debate_prompt(event, agents)
    
    response = await anthropic_client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=2000,
        system=system_blocks,
        messages=[{"role": "user", "content": user_message}],
        tools=AGENT_TOOL_SCHEMAS,  # cached via schema stability
    )
    
    parsed = parse_debate_response(response)  # split XML by agent tag
    state.turn_proposals = parsed.proposals
    state.turn_tool_calls = parsed.tool_calls
    state.turn_cost_cents += cost_of(response)
    
    return state
```

### 3.5 Tool system

Tool schemas are defined once, checked into the repo, and cached in prompts. Validation lives in the game engine, not the LLM.

```python
# app/agent/tools.py
AGENT_TOOL_SCHEMAS = [
    {
        "name": "propose_action",
        "description": "Add an action to the player's choice pool for this turn.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "maxLength": 40},
                "description": {"type": "string", "maxLength": 200},
                "effects_preview": {
                    "type": "array",
                    "items": {"type": "string"},
                },
            },
            "required": ["name", "description"],
        },
    },
    {
        "name": "modify_resource",
        "description": "Shift a colony resource by a signed integer delta.",
        "input_schema": {
            "type": "object",
            "properties": {
                "resource_id": {"type": "string"},
                "delta": {"type": "integer"},
                "reason": {"type": "string"},
            },
            "required": ["resource_id", "delta", "reason"],
        },
    },
    {
        "name": "modify_health",
        "description": "Change health of an agent or the player. target_id is an agent UUID or 'player'.",
        "input_schema": {
            "type": "object",
            "properties": {
                "target_id": {"type": "string"},
                "delta": {"type": "integer"},
                "reason": {"type": "string"},
            },
            "required": ["target_id", "delta", "reason"],
        },
    },
    {
        "name": "adjust_grievance",
        "description": "Change another agent's grievance score (0-10). Validated against axis-alignment rules.",
        "input_schema": {
            "type": "object",
            "properties": {
                "target_id": {"type": "string"},
                "delta": {"type": "integer"},
                "reason": {"type": "string"},
            },
            "required": ["target_id", "delta", "reason"],
        },
    },
    # ... speak_to_agent, adjust_relationship, adjust_own_grievance,
    #     remember, initiate_coup, back_leader
]
```

**Full tool catalog (10 tools):** `propose_action`, `modify_resource`, `modify_health`, `adjust_grievance`, `speak_to_agent`, `adjust_relationship`, `adjust_own_grievance`, `remember`, `initiate_coup`, `back_leader`.

The engine validator:

```python
# app/game/validator.py
def validate_tool_call(tc: ToolCall, state: ColonyState) -> ValidationResult:
    match tc.name:
        case "modify_resource":
            resource = state.resources.get(tc.args.resource_id)
            if not resource:
                return ValidationResult(
                    valid=False,
                    reason=f"Unknown resource: {tc.args.resource_id}",
                )
            if resource.value + tc.args.delta < 0:
                return ValidationResult(valid=False, reason="Would go negative")
            return ValidationResult(valid=True)
        case "modify_health":
            target = state.target_by_id(tc.args.target_id)  # player or agent
            if not target:
                return ValidationResult(valid=False, reason=f"Unknown target: {tc.args.target_id}")
            if not target.is_alive and tc.args.delta < 0:
                return ValidationResult(valid=False, reason="Target already dead")
            return ValidationResult(valid=True)
        case "adjust_grievance":
            target = state.agents_by_id.get(tc.args.target_id)
            if not target:
                return ValidationResult(valid=False, reason=f"Unknown agent: {tc.args.target_id}")
            # Axis validation: a positive grievance delta must be consistent with
            # the caller's axis stake in the decision. Enforced softly — the engine
            # caps the delta rather than rejecting outright.
            if abs(tc.args.delta) > 3:
                return ValidationResult(valid=False, reason="Grievance delta exceeds per-call cap of ±3")
            return ValidationResult(valid=True)
        case "initiate_coup":
            caller = state.agents_by_id[tc.caller_id]
            if caller.grievance < 9:
                return ValidationResult(valid=False, reason="Grievance too low")
            co_conspirators = [a for a in state.agents if a.grievance >= 7 and a.id != caller.id]
            if len(co_conspirators) == 0:
                return ValidationResult(valid=False, reason="No faction support")
            return ValidationResult(valid=True)
        # ...
```

**Coup resolution logic** (runs within `apply_tool_calls` node when `initiate_coup` validates):

```python
def resolve_coup(state: ColonyState, initiator_id: UUID) -> ColonyState:
    initiator = state.agents_by_id[initiator_id]
    co_conspirators = [a for a in state.active_agents if a.grievance >= 7 and a.id != initiator_id]
    total_boldness = initiator.temperament + sum(a.temperament for a in co_conspirators)
    max_accomplice_grievance = max(a.grievance for a in co_conspirators)

    kills = (
        initiator.grievance >= 9
        and max_accomplice_grievance >= 9
        and total_boldness > 0.5
        and state.legitimacy < 15
    )
    state = state.model_copy(update={"end_state": EndState.KILLED if kills else EndState.OUSTED})
    return state
```

Validation results are scored to Langfuse for the tool-call-validity eval metric.

### 3.6 XML debate format (detailed)

This is the crucial cost optimization. Rather than four parallel LLM calls, one call structured as:

```xml
<debate>
<agent name="Nadia" axes="idealist cautious collective" grievance="3">
You respond to the current event. Reference your values. If you'd take an action,
use the propose_action or modify_resource tools. If you'd speak privately to
another agent, use speak_to_agent.
</agent>
<agent name="Marko" axes="pragmatist bold collective" grievance="1">
[Same instructions]
</agent>
<agent name="Irena" axes="pragmatist cautious individual" grievance="2">
[Same instructions]
</agent>
<agent name="Yuri" axes="idealist bold individual" grievance="4">
[Same instructions]
</agent>
</debate>

CURRENT EVENT:
[Event text]

INSTRUCTIONS:
Each agent responds in turn. Use XML tags <agent name="X">...</agent> to
delimit. Inside each block: one short paragraph of reaction (<25 words),
followed by tool calls (JSON).
```

Anthropic's tool-use machinery handles the tool calls across agent personas in one completion. The per-agent text is split client-side on the XML tags.

### 3.7 Agent memory injection

Per-agent memories live in the **colony-state block**, not the persona block, so
per-run persona caching stays intact across turns (see §5.1). The persona block
is stable for the whole run; the colony-state block is rebuilt every turn and
carries the volatile per-agent data — grievance, morale, health, relationships,
and the memory list — inline for each alive agent.

Persona block (cached per run, one breakpoint shared across all 4 advisors):

```
<persona id="..." name="Nadia" role="Climate Scientist" archetype_quadrant="CONSCIENCE"
         ethics="0.60" temperament="-0.30" loyalty="0.80"
         verbal_tic="references scientific journals when making technical arguments">
Voice: idealist / cautious / collectivist. Keep utterances under 25 words.
</persona>
```

Colony-state block (cached per turn; same cache TTL but invalidates when the
turn flips):

```
<agent_state id="<nadia-uuid>" name="Nadia" grievance="3" morale="60" health="100" is_alive="true">
MEMORIES:
- Player overruled me on the samples (turn 3)
- Irena supported me on the heating decision (turn 4)
- Marko's proposal cost us fuel reserves (turn 4)
RELATIONSHIPS: →<marko-uuid>: -2, →<irena-uuid>: +3
</agent_state>
```

The `verbal_tic` field is populated from the setting's archetype template at run
generation and stays constant for the run. Memory entries turn over FIFO after
10, except death memories which are pinned (see
`backend/app/game/engine._pin_memory`).

---

## 4. State Model

All types use Pydantic v2 models; DB layer uses SQLAlchemy ORM with dataclass-style declarations.

### 4.1 `ColonyState`

```python
class ColonyState(BaseModel):
    game_id: UUID
    user_id: UUID
    setting_id: str
    seed: int
    turn_number: int
    max_turns: int  # 8-12, decided at run creation
    
    resources: dict[str, Resource]           # {id: Resource}
    agents: list[Agent]                       # includes dead with is_alive=False
    legitimacy: int                           # 0-100
    player_health: int                        # 0-100
    
    consequence_seeds: list[ConsequenceSeed]  # includes fired (with .fired=True)
    event_log: list[ResolvedEvent]            # one per turn
    
    current_event: Optional[Event]
    turn_proposals: list[Proposal]
    turn_tool_calls: list[ToolCall]
    turn_cost_cents: int                      # running cost for the run
    
    end_state: Optional[EndState]             # ENDURED/COLLAPSED/OUSTED/KILLED
```

### 4.2 `Agent`

```python
class Agent(BaseModel):
    id: UUID
    game_id: UUID
    name: str
    role: str                                 # setting-specific
    archetype_quadrant: Quadrant              # ACCOUNTANT/OPERATOR/CONSCIENCE/CRUSADER
    
    # Axes (-1.0 to +1.0)
    ethics: float                             # - pragmatist, + idealist
    temperament: float                        # - cautious, + bold
    loyalty: float                            # - individual, + collective
    
    portrait_seed: str                        # stable, drives DiceBear output
    
    # Live state
    health: int                               # 0-100
    morale: int                               # 0-100
    grievance: int                            # 0-10
    is_alive: bool
    
    # Multi-turn state
    memory: list[str]                         # last 10 facts, FIFO except deaths
    relationships: dict[UUID, int]            # other_agent_id → -10..+10
    transformation: Optional[Transformation]  # once per run max
```

### 4.3 `Event`

```python
class Event(BaseModel):
    id: str
    category: str                             # weather, raid, discovery, ...
    title: str
    body: str                                 # LLM-generated or template-filled
    severity: int                             # 1-5
    origin: EventOrigin                       # NATURAL / SEED_FIRED / FORCED_ESCALATION
    origin_seed_id: Optional[UUID]            # if SEED_FIRED
```

### 4.4 `ConsequenceSeed`

```python
class ConsequenceSeed(BaseModel):
    id: UUID
    game_id: UUID
    written_at_turn: int
    fires_at_turn: int
    foreshadowed: bool                        # 70% true
    foreshadow_turn: Optional[int]            # usually fires_at_turn - 2
    payload: SeedPayload                      # describes the event to inject
    origin_decision: DecisionRef              # links back to the originating choice
    fired: bool                               # True after firing
    dormant: bool                             # True if run ended before firing
```

### 4.5 `Decision` (post-mortem record)

```python
class Decision(BaseModel):
    turn_number: int
    event_id: str
    choices_offered: list[Proposal]           # 3 options
    chosen: ProposalRef
    effects_applied: list[Effect]
    seeds_written: list[SeedRef]
    grievance_shifts: dict[UUID, int]
    legitimacy_cost: int


class DecisionRef(BaseModel):
    """Compact reference to a past Decision, embedded in ConsequenceSeed."""
    turn_number: int
    event_id: str
    chosen_proposal_id: str
```

### 4.6 `Proposal`

```python
class Proposal(BaseModel):
    id: str
    proposed_by_agent_id: Optional[UUID]      # None if "wildcard" generated
    flavor: ProposalFlavor                    # DOMINANT / MORAL / WILDCARD
    name: str
    description: str
    effects_preview: list[str]                # shown to player
    tool_calls_on_pick: list[ToolCall]        # actual effects if chosen
```

### 4.7 Database tables (see section 7 for full schema)

Each of the above has a corresponding Postgres table:
- `game_sessions` (one row per run)
- `agents` (4 per run)
- `consequence_seeds`
- `turn_logs` (one row per turn)
- `event_log_entries` (details of each event resolution)
- `profiles` (per user, for daily gate)

---

## 5. Prompt Engineering

### 5.1 Caching layout

Every Anthropic call structures its system prompt as **three** cache-control
breakpoints (stable-to-volatile). Anthropic caps the cache_control breakpoint
count at 4 per request; we use 3 with one spare.

```
System:
  [cached — static, never invalidates within TTL] Preamble + tool rules  (~900 tok)
  [cached — per-run stable] 4 personas concatenated (id, role, axes, tic) (~1500 tok)
  [cached — per-turn] Colony state: resources, legitimacy, player_health,
                       per-agent memories + grievance + relationships     (~800 tok)

User (not cached):
  Current event + response instructions                                  (~400 tok)
```

The preamble and persona blocks are stable for the entire run (9 reads per
10-turn run, 1 write). The colony-state block invalidates every turn but is
still cache-hit within the turn by the second and third LLM calls
(debate → autonomous → narrate).

**Per-agent memories live in the colony-state block, not the persona block.**
Inlining memories into personas would invalidate the per-run cache every turn
when a new memory is appended — dropping expected cache hit rate from 85–90%
to roughly 40–50% on input tokens. See §3.7 for the block layout and
`backend/app/agent/prompts.build_colony_state_block`.

Expected cache hit rate after turn 2: **85–90%** of input tokens. Verified in
`tests/integration/test_full_run.py` via a wrapped `cost_cents_of` that records
`cache_read_input_tokens` per call.

### 5.2 Cost-control constants

```python
# app/agent/constants.py
MODEL_HAIKU = "claude-haiku-4-5-20251001"
MODEL_SONNET = "claude-sonnet-4-6"
MAX_COST_CENTS_PER_TURN = 15    # ~$0.15
MAX_COST_CENTS_PER_RUN = 40     # ~$0.40 hard cap; if hit, narration skipped
SONNET_NARRATION_ENABLED = True  # can be disabled for cost
CACHE_TTL = "ephemeral"          # 5 minutes
```

If `turn_cost_cents` exceeds `MAX_COST_CENTS_PER_TURN`, Sonnet narration is skipped and a templated fallback paragraph is used.

### 5.3 Narrative templates

Every event, proposal flavor, and end-state has a set of template fragments. Templates are used as:
- Fallbacks when Sonnet narration is disabled
- Seeds for the Sonnet call (giving it "here's the beats, write the prose")

Templates live in `app/narrative/templates/<setting>/<category>.yml`.

---

## 6. API Design

### 6.1 Routes

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| POST | `/auth/session` | Supabase JWT | Exchange Supabase token for server session; rate-limit first-signin |
| GET | `/me` | JWT | Current user info + daily gate status |
| POST | `/game/session` | JWT | Create a new run. Checks daily gate. Returns game_id, setting, agents. |
| GET | `/game/{id}` | JWT (own) | Full state read (for reconnection) |
| POST | `/game/{id}/turn` | JWT (own) | Start a turn's debate. Streams SSE events from LangGraph. |
| POST | `/game/{id}/decide` | JWT (own) | Submit the player's choice; resumes graph; returns resolved state + SSE for autonomous phase |
| GET | `/game/{id}/post-mortem` | JWT (own) OR public share token | Full timeline, counterfactuals, share card URL |
| GET | `/share/{share_token}` | public | Public read-only post-mortem view |
| GET | `/health` | public | Health check |

### 6.2 SSE event shape

```json
// Turn stream events
{"type": "turn_started", "turn": 5}
{"type": "event_generated", "event": {...}}
{"type": "agent_response", "agent_id": "...", "text": "...", "partial": true}
{"type": "agent_response_final", "agent_id": "...", "text": "...", "tool_calls": [...]}
{"type": "tool_validated", "tool_call_id": "...", "valid": true, "result": {...}}
{"type": "tool_rejected", "tool_call_id": "...", "reason": "..."}
{"type": "proposals_ready", "proposals": [...]}
{"type": "done"}
```

All SSE streams end with `event: done` / `data: [DONE]`.

### 6.3 Error shapes

```json
{
  "error": {
    "code": "daily_gate_exceeded",
    "message": "Your next run begins at 2026-04-24T00:00:00Z",
    "detail": {"next_run_at": "2026-04-24T00:00:00Z"}
  }
}
```

Standard codes: `daily_gate_exceeded`, `game_not_found`, `invalid_choice`, `rate_limited`, `cost_cap_exceeded`, `llm_unavailable`.

### 6.4 CORS and security headers

We use Bearer-token auth in the `Authorization` header, not cookies — so `allow_credentials=True` is unnecessary and would block wildcard origins without adding security.

```python
app.add_middleware(CORSMiddleware,
    allow_origins=[settings.FRONTEND_URL, "http://localhost:3000"],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "Content-Type"],
)
```

Set `Strict-Transport-Security`, `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer` in middleware.

---

## 7. Database Schema

Managed with Alembic under `backend/alembic/`. Supabase CLI manages auth + RLS policies under `backend/supabase/migrations/`. The two do not collide.

### 7.1 Tables

```sql
-- profiles: one row per authenticated user; links to auth.users
CREATE TABLE profiles (
    id UUID PRIMARY KEY REFERENCES auth.users ON DELETE CASCADE,
    display_name TEXT,
    last_run_started_at TIMESTAMPTZ,
    total_runs INT NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- game_sessions: one row per run
CREATE TABLE game_sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    setting_id TEXT NOT NULL,
    seed BIGINT NOT NULL,
    max_turns INT NOT NULL,
    current_turn INT NOT NULL DEFAULT 0,
    
    state JSONB NOT NULL,          -- the full ColonyState snapshot
    legitimacy INT NOT NULL DEFAULT 100,
    player_health INT NOT NULL DEFAULT 100,
    
    end_state TEXT,                -- NULL while running, ENDURED/COLLAPSED/...
    cost_cents INT NOT NULL DEFAULT 0,
    
    share_token TEXT UNIQUE,       -- generated on completion
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    ended_at TIMESTAMPTZ
);
CREATE INDEX idx_game_sessions_user ON game_sessions(user_id);
CREATE INDEX idx_game_sessions_end_state ON game_sessions(end_state) WHERE end_state IS NOT NULL;

-- agents: 4 per game session
CREATE TABLE agents (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    game_id UUID NOT NULL REFERENCES game_sessions(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    role TEXT NOT NULL,
    archetype_quadrant TEXT NOT NULL,
    ethics REAL NOT NULL,
    temperament REAL NOT NULL,
    loyalty REAL NOT NULL,
    portrait_seed TEXT NOT NULL,
    
    -- Live state; updated every turn
    state JSONB NOT NULL            -- { health, morale, grievance, is_alive,
                                    --   memory[], relationships{}, transformation? }
);
CREATE INDEX idx_agents_game ON agents(game_id);

-- turn_logs: one row per turn, used for post-mortem
CREATE TABLE turn_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    game_id UUID NOT NULL REFERENCES game_sessions(id) ON DELETE CASCADE,
    turn_number INT NOT NULL,
    event JSONB NOT NULL,           -- the event presented this turn
    proposals JSONB NOT NULL,       -- the 3 options
    chosen JSONB NOT NULL,          -- the one picked
    agent_responses JSONB NOT NULL, -- per-agent text + tool calls
    autonomous_actions JSONB,        -- off-turn actions
    narration TEXT,                 -- if Sonnet was used
    state_snapshot JSONB NOT NULL,  -- ColonyState after this turn
    turn_cost_cents INT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (game_id, turn_number)
);

-- consequence_seeds: track all seeds, fired and dormant
CREATE TABLE consequence_seeds (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    game_id UUID NOT NULL REFERENCES game_sessions(id) ON DELETE CASCADE,
    written_at_turn INT NOT NULL,
    fires_at_turn INT NOT NULL,
    foreshadowed BOOLEAN NOT NULL,
    foreshadow_turn INT,
    payload JSONB NOT NULL,
    origin_decision JSONB NOT NULL,
    fired BOOLEAN NOT NULL DEFAULT FALSE,
    dormant BOOLEAN NOT NULL DEFAULT FALSE
);
CREATE INDEX idx_seeds_game_fires ON consequence_seeds(game_id, fires_at_turn);
```

### 7.2 RLS policies (via Supabase CLI migrations)

```sql
-- profiles: user can read/write their own profile
ALTER TABLE profiles ENABLE ROW LEVEL SECURITY;
CREATE POLICY "users_read_own_profile" ON profiles
    FOR SELECT USING (auth.uid() = id);
CREATE POLICY "users_update_own_profile" ON profiles
    FOR UPDATE USING (auth.uid() = id);

-- game_sessions: user can read their own runs (post-mortem)
ALTER TABLE game_sessions ENABLE ROW LEVEL SECURITY;
CREATE POLICY "users_read_own_runs" ON game_sessions
    FOR SELECT USING (auth.uid() = user_id);
CREATE POLICY "public_read_shared_runs" ON game_sessions
    FOR SELECT USING (share_token IS NOT NULL);

-- Writes go through the service role (FastAPI backend), not client-side.
-- No INSERT/UPDATE policies for authenticated role needed.
```

### 7.3 Indexes & performance

At MVP scale (1 run/day/user, ~hundreds of users), query performance is not a concern. The indexes above are defensive. Monitor `pg_stat_statements` after launch.

The `state JSONB` column on `game_sessions` is queried by path for specific reads (`state->'resources'->>'fuel'`) but most reads deserialize the whole object into a Pydantic model. There is no separate `event_log_entries` table — the canonical event record for post-mortem is `turn_logs.event` (JSONB) plus `turn_logs.state_snapshot.event_log` (a history list maintained on `ColonyState.event_log`).

### 7.4 Migrations

Alembic handles game tables; Supabase CLI handles RLS + the `auth` schema.

```bash
# Alembic workflow
alembic revision --autogenerate -m "add_consequence_seeds"
alembic upgrade head

# Supabase CLI workflow
supabase migration new enable_rls_profiles
# edit supabase/migrations/TIMESTAMP_enable_rls_profiles.sql
supabase db push
```

Both apply raw SQL to the same Postgres instance; they don't conflict as long as they touch disjoint tables. RLS migrations always come after Alembic creates the table.

---

## 8. Frontend Architecture

### 8.1 Route structure

```
app/
├── (public)/
│   ├── page.tsx                    # Landing
│   ├── about/page.tsx
│   └── share/[token]/page.tsx      # Public post-mortem view
├── (authed)/
│   ├── layout.tsx                  # Checks auth, redirects to /login if absent
│   ├── play/page.tsx               # Pre-run screen: "Begin run" button or daily gate message
│   ├── run/[id]/page.tsx           # The live dashboard
│   └── post-mortem/[id]/page.tsx   # The run's post-mortem
├── auth/
│   └── callback/route.ts           # Supabase OAuth callback
└── layout.tsx                      # Root: CRT overlay, font loading, theme provider
```

### 8.2 Client/server split

- **Landing, auth, pre-run, post-mortem:** Server Components. Data fetched server-side with the Supabase server client, rendered as HTML.
- **Run dashboard:** Client component. Uses SSE for turn streaming. State held in React state + synced to server via POST.

No global state library (Redux/Zustand) — React state is enough for the run dashboard. Supabase session is provided via context.

### 8.3 Dashboard layout (the 3-panel grid)

```
┌─────────────────────────────────────────────────────────────────────┐
│ OUTPOST · Vorgrad Station                   DAY 05 / 10 · LEGIT 62  │
├─────────────────────────┬───────────────────────────────────────────┤
│ COLONY STATUS           │ EVENT                                     │
│                         │                                           │
│  fuel    ████░░  58%    │  [portrait]  Heating core leak.           │
│  food    ██░░░░  34%    │                                           │
│  warmth  ███░░░  50%    │  [narrative text]                         │
│  medic.  ██░░░░  31%    │                                           │
│  morale  ███░░░  55%    │ DEBATE                                    │
│                         │  ◉ Nadia:   "Samples matter."            │
│ AGENTS                  │  ◉ Marko:   "Full power."                 │
│  ◉ Nadia    healthy     │  ◉ Irena:   "Infirmary first."           │
│  ◉ Marko    wounded     │  ◉ Yuri:    "Let me try a bypass."       │
│  ◉ Irena    tired       │                                           │
│  ◉ Yuri     healthy     │ CHOOSE                                    │
│                         │  [A] Marko: Full power, full three hours │
│ LOG                     │  [B] Irena: Reduced, infirmary priority  │
│  d03 · drought          │  [C] Yuri:  Bypass gamble (2h full)      │
│  d04 · theft            │                                           │
│  d04 · heating alert    │  (direct order: 10 legitimacy)            │
└─────────────────────────┴───────────────────────────────────────────┘
```

Implemented as CSS Grid with named areas:
```css
.dashboard {
  display: grid;
  grid-template-areas:
    "header  header"
    "status  event"
    "status  choose";
  grid-template-columns: 280px 1fr;
  grid-template-rows: auto 1fr auto;
  gap: 0;
  height: 100vh;
}
@media (max-width: 768px) {
  .dashboard {
    grid-template-areas:
      "header"
      "event"
      "choose"
      "status";
    grid-template-columns: 1fr;
  }
}
```

### 8.4 shadcn/ui re-theme

Install shadcn with the default theme, then overlay:

```css
/* globals.css */
@layer base {
  :root {
    --radius: 0;   /* kill all rounded corners */
    --background: 28 4% 10%;       /* HSL; our --color-bg */
    --foreground: 44 15% 74%;      /* --color-text */
    --card: 40 5% 13%;              /* --color-surface */
    --primary: var(--color-accent); /* setting-dependent */
    /* ... */
  }
}

.pixel-border {
  box-shadow:
    -4px 0 0 var(--px-border), 4px 0 0 var(--px-border),
    0 -4px 0 var(--px-border), 0 4px 0 var(--px-border);
}
```

shadcn components (Button, Card, Dialog, Tooltip) get themed via CSS variables + a `pixel-*` utility class overlay. Components with hardcoded `rounded-*` classes (rare) get copied locally into `components/ui/` and edited.

### 8.5 Fonts

```ts
// app/layout.tsx
import { Silkscreen, Press_Start_2P, VT323, IBM_Plex_Mono } from 'next/font/google'

const silkscreen = Silkscreen({ subsets: ['latin'], weight: ['400'], variable: '--font-silkscreen' })
const pressStart = Press_Start_2P({ subsets: ['latin'], weight: ['400'], variable: '--font-press-start' })
const vt323 = VT323({ subsets: ['latin'], weight: ['400'], variable: '--font-vt323' })
const ibmPlex = IBM_Plex_Mono({ subsets: ['latin'], weight: ['400', '500'], variable: '--font-body' })
```

Tailwind theme extends:
```js
fontFamily: {
  display: ['var(--font-press-start)', 'monospace'],
  heading: ['var(--font-silkscreen)', 'monospace'],
  terminal: ['var(--font-vt323)', 'monospace'],
  body: ['var(--font-body)', 'monospace'],
}
```

### 8.6 DiceBear integration

```tsx
// components/agent/Portrait.tsx
'use client'
import { useMemo } from 'react'
import { createAvatar } from '@dicebear/core'
import { pixelArt } from '@dicebear/pixel-art'

type Props = { seed: string; setting: 'arctic' | 'orbital' | 'desert'; size?: number }

const SETTING_OVERRIDES = {
  arctic: { clothingColor: ['3c4f5c', '2a3a4a'], backgroundColor: ['1a1a18'] },
  orbital: { clothingColor: ['4a4a5c', '3c3c4a'], backgroundColor: ['1a1a18'] },
  desert: { clothingColor: ['8c6a4a', '7a5a3a'], backgroundColor: ['1a1a18'] },
}

export function Portrait({ seed, setting, size = 64 }: Props) {
  const svg = useMemo(() => {
    return createAvatar(pixelArt, {
      seed,
      size,
      ...SETTING_OVERRIDES[setting],
    }).toString()
  }, [seed, setting, size])
  
  const dataUri = `data:image/svg+xml;utf8,${encodeURIComponent(svg)}`
  return <img src={dataUri} alt="" width={size} height={size} className="pixel" />
}
```

`.pixel { image-rendering: pixelated; }` in globals.

### 8.7 SSE consumption

```tsx
// hooks/useTurnStream.ts
export function useTurnStream(gameId: string) {
  const [events, setEvents] = useState<TurnEvent[]>([])
  
  async function startTurn() {
    const res = await fetch(`${API}/game/${gameId}/turn`, {
      method: 'POST',
      headers: { Authorization: `Bearer ${token}` },
    })
    const reader = res.body!.pipeThrough(new TextDecoderStream()).getReader()
    // parse SSE frames, append to events
  }
  
  return { events, startTurn }
}
```

---

## 9. Authentication Flow

### 9.1 Sign-in

```
1. User clicks "Play" on landing
2. Next.js redirects to Supabase Auth OAuth URL with Google provider
3. User authenticates with Google
4. Google redirects to Supabase callback
5. Supabase redirects to https://outpost.app/auth/callback?code=...
6. Next.js route handler exchanges code for session via Supabase client
7. Session cookie set (HTTP-only, secure)
8. Redirect to /play
```

### 9.2 JWT validation in FastAPI

```python
# app/auth/service.py
import jwt
from app.config import settings

def decode_supabase_jwt(token: str) -> dict:
    try:
        return jwt.decode(
            token,
            settings.SUPABASE_JWT_SECRET,
            algorithms=["HS256"],
            audience="authenticated",
        )
    except jwt.ExpiredSignatureError:
        raise HTTPException(401, "Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(401, "Invalid token")

# app/dependencies.py
async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
) -> dict:
    return decode_supabase_jwt(credentials.credentials)
```

`SUPABASE_JWT_SECRET` is fetched from Supabase dashboard → Settings → API → JWT Settings. It's an HS256 secret (symmetric), so verification is zero-network-cost.

### 9.3 Token refresh

Frontend uses Supabase client which auto-refreshes. Before every API call, the client fetches the current access token. Refreshing doesn't require changes to FastAPI.

### 9.4 Profile bootstrap

First successful sign-in creates a `profiles` row via a Postgres trigger:

```sql
-- in supabase/migrations/
CREATE OR REPLACE FUNCTION create_profile_for_new_user()
RETURNS TRIGGER AS $$
BEGIN
    INSERT INTO public.profiles (id, display_name)
    VALUES (NEW.id, NEW.raw_user_meta_data->>'name');
    RETURN NEW;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

CREATE TRIGGER on_auth_user_created
    AFTER INSERT ON auth.users
    FOR EACH ROW EXECUTE FUNCTION create_profile_for_new_user();
```

---

## 10. Daily Gate & Bot Protection

### 10.1 Daily gate (server-authoritative)

```python
# app/game/service.py
async def can_start_run(user_id: UUID, db: AsyncSession) -> bool:
    profile = await db.get(Profile, user_id)
    if profile.last_run_started_at is None:
        return True
    # Reset at 00:00 UTC
    reset_time = profile.last_run_started_at.replace(
        hour=0, minute=0, second=0, microsecond=0
    ) + timedelta(days=1)
    return datetime.now(timezone.utc) >= reset_time
```

`last_run_started_at` is set atomically on run creation.

### 10.2 Rate limiting

`slowapi` middleware per route:
- `/game/session`: 1 per day (enforced by daily gate; rate limit is 5/hour as safety)
- `/game/{id}/turn`: 30/hour per user
- `/game/{id}/decide`: 60/hour per user
- Unauth `/share/{token}`: 30/minute per IP

### 10.3 Bot resistance

The primary bot defenses:
- Google OAuth (bots can't easily automate Google sign-in)
- Server-authoritative daily gate tied to `auth.users.id`
- Rate limits above

No CAPTCHA in MVP. If abuse emerges post-launch, add Cloudflare Turnstile at the `/play` click.

### 10.4 Cost cap

Per-run LLM cost is tracked on `game_sessions.cost_cents`. If a run exceeds `MAX_COST_CENTS_PER_RUN`, Sonnet calls are disabled for the remainder of the run and a log warning is emitted.

---

## 11. Observability (Langfuse)

### 11.1 Setup

```python
# app/agent/observability.py
from langfuse.callback import CallbackHandler
from app.config import settings

def build_langfuse_handler(game_id: str, turn_number: int, setting_id: str):
    return CallbackHandler(
        public_key=settings.LANGFUSE_PUBLIC_KEY,
        secret_key=settings.LANGFUSE_SECRET_KEY,
        host="https://cloud.langfuse.com",
        session_id=f"outpost-run-{game_id}",
        metadata={
            "turn": turn_number,
            "setting": setting_id,
            "environment": settings.ENVIRONMENT,
        },
    )
```

Every LangGraph `graph.ainvoke()` passes this handler via `config={"callbacks": [handler]}`. LangGraph's built-in LangChain callback support propagates the handler through all nodes.

### 11.2 Trace hierarchy

```
Trace: outpost-run-<game_id>
  metadata: setting_id, seed, user_id, started_at
  ├─ Span: turn_1
  │    metadata: turn_number, event_id, resource_snapshot_before
  │    ├─ LLM: agent_debate  (model, tokens_in, tokens_out, cost, cache_hits)
  │    │    attributes: agents[], event_id
  │    ├─ Span: tool_validation (one per tool call)
  │    │    attributes: tool_name, caller_id, valid, reason
  │    └─ Event: proposals_ready
  ├─ Span: turn_2
  │    ...
  └─ Span: post_mortem
       attributes: end_state, total_cost_cents, duration_ms
```

Any support debugging ("why did the agent act weird in turn 7?") becomes: open trace → drill to turn 7 → see exact prompt + response + tool calls + state.

### 11.3 Custom scores

Eval metrics are pushed to Langfuse as scores per trace:

```python
langfuse_client.score(
    trace_id=trace_id,
    name="personality_consistency",
    value=0.84,
    comment="Average cosine sim across 10 turns",
)
```

---

## 12. Eval Pipeline

### 12.1 Five metrics (summarized from research)

| Metric | Measurement | Threshold | Cost |
|--------|-------------|-----------|------|
| Personality Consistency | Cosine sim(response, character profile) + Haiku judge | ≥ 0.72 avg | ~$0.008/turn |
| Tool-Call Validity | % of valid tool calls from engine logs | ≥ 85% run | $0 (logs) |
| Narrative Repetition | Pairwise BLEU-2 intra-run | ≤ 0.35 | $0 (local compute) |
| Decision Coherence | Haiku-as-judge: does action match personality? | ≥ 0.6 per turn | ~$0.003/turn |
| Game Balance | Survival rate in [30%, 70%]; forced-choice rate <20% | target ranges | $0 (simulated play) |

### 12.2 Pipeline structure

```
.github/workflows/eval.yml
  on: pull_request
  
  jobs:
    eval-pipeline:
      - checkout
      - install deps
      - run 20 simulated games (scripted player using fixed policy)
      - compute metrics → upload to LangSmith
      - agentevals trajectory check
      - fail if any threshold breached
```

The simulated player is a deterministic policy in `tests/fixtures/simulated_player.py` that picks actions by a heuristic (e.g., always pick moral option, or always pick dominant). Running 20 games with varying policies exposes different game behaviors.

### 12.3 Personality consistency implementation

```python
# app/eval/personality.py
from sentence_transformers import SentenceTransformer
import numpy as np

model = SentenceTransformer("all-MiniLM-L6-v2")  # 80MB, local

def score_personality_consistency(agent: Agent, responses: list[str]) -> float:
    profile_embedding = model.encode(agent.profile_text)
    response_embeddings = model.encode(responses)
    sims = [np.dot(profile_embedding, e) / (
        np.linalg.norm(profile_embedding) * np.linalg.norm(e)
    ) for e in response_embeddings]
    return float(np.mean(sims))
```

Optional Haiku-as-judge layer for narrative/semantic consistency (adds ~$0.008/turn).

### 12.4 CI integration

```toml
# pyproject.toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
markers = ["eval: tests that simulate full games"]
```

```python
# tests/eval/test_eval_pipeline.py
@pytest.mark.eval
@pytest.mark.parametrize("policy", ["moral", "pragmatic", "random"])
async def test_simulated_run(policy):
    run = await simulate_game(policy=policy, setting="arctic_station_v1")
    assert run.tool_validity >= 0.85
    assert run.personality_consistency >= 0.72
    assert 0.30 <= run.survival_rate <= 0.70
```

---

## 13. Deployment

### 13.1 Backend on Railway

- Connect GitHub repo
- Railway auto-detects `Dockerfile`
- Set env vars in Railway dashboard (see section 14)
- Set custom domain (e.g., `api.outpost.app`) with automatic TLS

### 13.2 Frontend on Vercel

- Connect GitHub repo (monorepo — root is Next.js)
- Framework preset: Next.js
- Root directory: `frontend/`
- Set env vars (public + server-side)
- Custom domain (`outpost.app` or similar)

### 13.3 CI/CD

```yaml
# .github/workflows/deploy.yml
name: Deploy
on:
  push:
    branches: [main]

jobs:
  test-backend:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.12" }
      - run: pip install uv
      - run: cd backend && uv sync
      - run: cd backend && uv run ruff check .
      - run: cd backend && uv run pytest -v --ignore=tests/eval

  eval-backend:
    runs-on: ubuntu-latest
    needs: test-backend
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.12" }
      - run: pip install uv
      - run: cd backend && uv sync
      - run: cd backend && uv run pytest -v -m eval tests/eval
    env:
      ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
      LANGSMITH_API_KEY: ${{ secrets.LANGSMITH_API_KEY }}

  test-frontend:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with: { node-version: "22" }
      - run: cd frontend && pnpm install
      - run: cd frontend && pnpm lint
      - run: cd frontend && pnpm test
      - run: cd frontend && pnpm build

  deploy-backend:
    needs: [test-backend, eval-backend]
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: bervProject/railway-deploy@main
        with:
          railway_token: ${{ secrets.RAILWAY_TOKEN }}
          service: outpost-backend

  # Frontend deploys via Vercel's GitHub integration (no workflow needed)
```

### 13.4 Monorepo layout (root)

```
Outpost/
├── CLAUDE.md
├── docs/                  # SPEC, ARCHITECTURE, PLAN, research/
├── backend/               # FastAPI app
│   ├── app/
│   ├── tests/
│   ├── alembic/
│   ├── supabase/migrations/
│   ├── Dockerfile
│   ├── Dockerfile.dev
│   ├── docker-compose.yml
│   └── pyproject.toml
├── frontend/              # Next.js app
│   ├── app/
│   ├── components/
│   ├── hooks/
│   ├── lib/
│   ├── public/
│   ├── package.json
│   └── next.config.ts
├── .github/workflows/
└── README.md
```

---

## 14. Environment Variables Catalog

### 14.1 Backend (`backend/.env` + Railway)

| Var | Purpose | Where from |
|-----|---------|-----------|
| `DATABASE_URL` | Postgres connection string | Supabase dashboard → Settings → Database |
| `SUPABASE_URL` | Supabase project URL | Supabase dashboard → Settings → API |
| `SUPABASE_JWT_SECRET` | HS256 secret for JWT validation | Supabase dashboard → Settings → API |
| `SUPABASE_SERVICE_ROLE_KEY` | Service role key for admin ops | Supabase dashboard → Settings → API |
| `ANTHROPIC_API_KEY` | Claude API | console.anthropic.com |
| `LANGFUSE_PUBLIC_KEY` | Langfuse tracing | langfuse.com dashboard |
| `LANGFUSE_SECRET_KEY` | Langfuse tracing | langfuse.com dashboard |
| `LANGSMITH_API_KEY` | (CI only) for eval agentevals | smith.langchain.com |
| `FRONTEND_URL` | For CORS allowlist | Your Vercel domain |
| `ENVIRONMENT` | dev/staging/prod | Set per deploy |
| `LOG_LEVEL` | debug/info/warn | Default info |

### 14.2 Frontend (`frontend/.env.local` + Vercel)

| Var | Purpose |
|-----|---------|
| `NEXT_PUBLIC_SUPABASE_URL` | Supabase URL (public-safe) |
| `NEXT_PUBLIC_SUPABASE_ANON_KEY` | Supabase anon key (public-safe) |
| `NEXT_PUBLIC_API_BASE_URL` | FastAPI backend URL |

### 14.3 Local dev (`.env` via Docker Compose)

The FastAPI container reads from a `.env` file mounted into the container. Example values point at local Supabase (`host.docker.internal:54322`) and `NEXT_PUBLIC_API_BASE_URL=http://localhost:8000` in the frontend.

---

## 15. Cost Model

### 15.1 Per-run math (from `research/01-agent-architecture.md`)

With caching + debate batching + Haiku primary + 1 Sonnet narration per turn:
- **~$0.22 per run** at the 50th percentile
- **~$0.35 per run** at the 95th percentile

### 15.2 Monthly projection

Assuming daily-gate: 1 run per user per day.

| Daily active users | Runs/month | LLM cost/month |
|-------------------|-----------|---------------|
| 10 | 300 | ~$66 |
| 100 | 3,000 | ~$660 |
| 1,000 | 30,000 | ~$6,600 |

Above 100 DAU, cost becomes a real concern. Controls ready:
- Disable Sonnet narration globally → drop to ~$0.10/run → $300/mo at 100 DAU
- Require Haiku-only in a "budget mode" setting

### 15.3 Other recurring costs

| Service | Tier | Monthly |
|---------|------|---------|
| Supabase | Free tier | $0 (up to 500MB DB, 5GB bandwidth) |
| Railway | Hobby | $5 (after $5 free credit) |
| Vercel | Hobby | $0 (hobby is free for non-commercial) |
| Langfuse | Hobby | $0 (50K observations/mo) |
| LangSmith | Developer | $0 (5K traces/mo, CI only) |
| Domain | — | ~$10/year |

**Floor: ~$5/month.** LLM cost dominates at scale.

---

## 16. Architectural Invariants

These are non-negotiable decisions. Violating any of them is a bug.

1. **The engine is the source of truth.** LLMs propose via tool calls; the engine validates and applies. LLMs never directly mutate state.
2. **No numbers from the LLM.** Resource deltas, health changes, grievance shifts — all come from validated tool calls, not from freeform text parsing.
3. **Dead means dead.** Agents removed on death are not re-prompted. Their name never appears in subsequent advisor contexts except as memory facts in surviving agents.
4. **One run per account per day.** Daily gate is server-authoritative. Cannot be bypassed by client-side state.
5. **Prompt caching is on by default.** `cache_control: ephemeral` blocks on system prompt. Skipping the cache is a conscious optimization reversal.
6. **Debate is batched.** One LLM call per debate round, XML-delimited, not N calls per agent.
7. **Setting content is procedural from day one.** Hand-authored settings in code are forbidden. All settings use the `Setting` schema.
8. **No LLM-generated images in MVP.** Portraits are DiceBear; backgrounds are CSS-only.
9. **RLS is defense-in-depth, not primary auth.** Queries filter by user_id in application code; RLS is the safety net.
10. **Eval thresholds gate merges.** PRs that drop personality consistency below 0.72 or tool validity below 85% do not merge.
11. **All state mutations flow through validated tool calls.** There is no engine-only side-channel. Engine axis-heuristics (e.g., "Collective agent gains grievance when player sacrifices group") are implemented as helpers that *emit* tool calls, which then pass through the same validator as LLM-proposed calls. This preserves the Tool-Call Validity metric as a complete view of state change correctness.
12. **Thread-id discipline for LangGraph resumption.** Every `graph.invoke()` and `graph.ainvoke()` passes `config={"configurable": {"thread_id": game_id}}` so the Postgres checkpointer can resume after the `await_player` pause point. Missing thread_id silently breaks pause/resume.

---

## 17. Known Risks & Mitigations

Catalog of known risks surfaced during spec review. Each has an in-plan mitigation (see `PLAN.md` for task references) or is acknowledged as a post-launch monitoring item.

| Risk | Mitigation | Plan reference |
|------|-----------|----------------|
| Supabase HS256 → JWKS migration might deprecate our JWT verification path | Keep project on HS256 signing setting; add JWKS fallback as P1 post-launch task | Phase 2 notes |
| 5-min ephemeral cache TTL may not survive real player pacing (slow turns) | Measure in Phase 4 integration test; switch to 1-hour cache tier if hit rate < 70% | Phase 4 task 4.1 comment |
| Turn latency (3 serialized LLM calls = 9-24s) may exceed player patience | Stream debate incrementally via SSE; measure p95 turn latency in Phase 5 | Phase 5 eval metric |
| Cost projection at real player pacing could exceed $0.25/run | Measure p95 cost in Phase 5 eval; have Sonnet-off fallback already wired | Phase 5 + existing cost cap |
| SSE stream holds stale JWT across token refresh mid-run → 401 partway | Frontend re-fetches token before each POST; backend tolerates reconnect with fresh token to resume graph by thread_id | Phase 7 task 7.7 |
| Inter-run narrative repetition (run 5 of Vorgrad feels same as run 1) | Add inter-run repetition metric to eval pipeline | Phase 5 task 5.3 |
| Railway cold starts on $5-credit tier could spike first-request latency | Accept on Hobby tier; document in README; watch in production | Phase 9 task 9.2 |
| Model IDs: Anthropic aliases (`claude-sonnet-4-6`) may shift out from under us | Use dated forms (`claude-haiku-4-5-20251001`, similar for Sonnet) in constants | Phase 4 task 4.1 |
