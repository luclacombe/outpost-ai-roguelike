# Research: Agent Architecture & Cost Engineering

> Research agent output from 2026-04-23. Source for architectural decisions around agent framework choice, prompt caching, and cost modeling. Consulted when making decisions in `docs/ARCHITECTURE.md` sections 3, 5, 14.

## Framework Decision Matrix

| Criterion | Raw Anthropic SDK | Pydantic AI | LangGraph |
|---|---|---|---|
| Turn-based game loop control | Manual agentic loop — you own every step | Agent-as-object with run_sync/run; orchestration is your code | Explicit StateGraph with nodes/edges; turn = graph traversal |
| Per-agent state (health, memory, relationships) | Dict you manage yourself | TypedDict/BaseModel per agent; strongly typed | TypedDict state with annotated reducers; merges updates |
| Shared colony state | Shared dict passed to tool calls | Dependency injection via RunContext | Centralized graph state, all nodes read/write it |
| Agent death / removal mid-run | Remove from your loop's agent list | Remove from orchestration loop; no framework support | Delete agent key from state in a reducer; re-route edges |
| Tool definition & validation | Raw JSON schema + your executor | `@agent.tool` decorator with Pydantic input validation | `@tool` decorator, or ToolNode wrapper |
| Inter-agent communication | Pass transcripts manually or in shared context | Agent delegation (one agent calls another as a tool) | Message-passing nodes; supervisor pattern routes to subgraphs |
| Learning curve (TS/Next.js dev) | Lowest — Python functions + dicts | Medium — Pydantic types feel like TypeScript interfaces | Steepest — graph theory + LangChain mental model |
| LangChain dependency weight | None | None | Full LangChain ecosystem |
| Portfolio signal | "I built from scratch" | "Typed production patterns" | "I understand orchestration graphs" |

**Verdict:** For Outpost specifically, raw Anthropic SDK wins on pure merit. However, we adopt a hybrid: LangGraph as the orchestration state graph (CV keyword + built-in checkpointing), raw SDK for actual LLM calls (cache/batch control).

## Cost Modeling (Haiku 4.5: $1/MTok input, $5/MTok output)

### Call Inventory per Turn (10-turn median run, 4 agents)

| Call type | Calls/turn | Input tokens | Output tokens |
|---|---|---|---|
| Agent debate response | 4 | ~2,000 | ~300 |
| Tool call (modify colony state) | 2–3 | ~1,500 | ~150 |
| Autonomous action (between turns) | 2 | ~1,800 | ~250 |
| Narration (Sonnet, 1/turn) | 1 | ~2,500 | ~400 |

### Per-turn Cost (Haiku only, no caching)
- Debate: 4 × (2,000 + 300) = 9,200 tokens → **$0.015**
- Tools: 2.5 × (1,500 + 150) = 4,125 tokens → **$0.0056**
- Autonomous: 2 × (1,800 + 250) = 4,100 tokens → **$0.0055**
- Subtotal Haiku/turn: **~$0.026**

### Per-run (10 turns): ~$0.26 (over budget at raw pricing)

### With Prompt Caching (5-min TTL, 75% cache hit)
- System prompt cached: 4 agents × 800 tokens × 9 turns = 28,800 tokens → saves $0.026
- Colony state cached: 600 tokens × 6 reads/turn × 10 turns = 36,000 tokens → saves $0.032
- Total cache savings: **~$0.058/run**

### Adjusted per-run (Haiku only): **~$0.20**
### Add 1 Sonnet narration/turn: +$0.085 → **~$0.28–0.30 total**

### Summary Numbers
| Scenario | Cost/run |
|---|---|
| No optimization | ~$0.30 |
| With prompt caching only | ~$0.22 |
| With caching + debate batching | ~$0.16 |
| Caching + batching + Haiku-only (no Sonnet) | ~$0.08 |
| Caching + batching + 1 Sonnet narration/turn | ~$0.22 |

Under $0.10: achievable Haiku-only + batching.
Under $0.25: achievable with Sonnet narration + caching + batching.

## Prompt Caching Strategy

### What to Cache
1. Each agent's full personality/backstory system prompt (~800 tokens each) — static per run, cached once, read 9+ times. Near-100% hit rate after turn 1.
2. Tool schema definitions (~346 tokens) — static per session.
3. Colony world-state prefix — cache at turn start, all agents within that turn read from cache.

### Cache Structure
```
[CACHED] System prompt (agent personality) — static per run
[CACHED] Colony world state snapshot — refreshed each turn, cached once, read by all 4 agents
[NOT CACHED] Turn event + conversation history — dynamic per call
```

### Realistic Hit Rate: 80-90% of input tokens after turn 2 are cache reads.

### TTL Warning (March 2026)
Anthropic dropped default TTL to 5 minutes. Must explicitly pass `"type": "ephemeral"` with `cache_control` blocks, or use 1-hour tier ($2/MTok write) if players pause between turns.

## Call Reduction Techniques

### Debate Batching (highest impact)
Instead of 4 individual calls per debate round, run all 4 agents in a single call using XML-delimited persona sections:

```
<agent name="Engineer" personality="...">Respond to the crisis.</agent>
<agent name="Doctor" personality="...">Respond to the crisis.</agent>
...respond for each in turn
```

One call with ~3,000 input + ~1,200 output tokens replaces four calls totaling ~8,000 input + ~1,200 output. **Cuts debate cost by ~60%.**

### Autonomous Actions Batching
One batched call per autonomous phase instead of one per agent. Reduces 2-3 calls to 1.

### Minimum Viable Call Count per Turn
- 1 batched debate call
- 1-2 tool execution calls
- 1 narration call (Sonnet, optional)
- **Total: 3-4 calls/turn** vs 9-11 without batching

## Concrete Architecture

Framework: LangGraph as orchestrator + raw Anthropic SDK for LLM calls.

### Conceptual Layout
```
ColonyGame
  state: ColonyState (resources, turn, events, consequence_seeds)
  agents: list[Agent]  # removed on death

Agent
  id, name, role, personality_prompt (cached)
  memory: list[str]  # last 10 facts injected as context
  health, morale, grievance: int
  relationships: dict[agent_id, int]
```

### Turn Flow (LangGraph nodes)
1. `generate_event()` → ColonyState.current_event
2. `agent_debate(event)` → ONE batched Anthropic call with XML persona sections
3. `execute_tools(tool_calls)` → engine validates + mutates ColonyState
4. `player_choice(proposals)` → PlayerAction
5. `resolve(player_action)` → update ColonyState
6. `autonomous_actions()` → 1 batched call for off-turn behavior
7. `check_deaths()` → `self.agents = [a for a in self.agents if a.health > 0]`
8. `narrate_turn()` → 1 Sonnet call (optional)
9. `advance_turn()`

### Agent Death
Permanent removal. Surviving agents get a memory entry noting the death.

### Inter-Agent Reasoning
Agent B's proposal references Agent A's because they appear in the same batched call context. For sequential reactions, run two batched calls (first A+C, then B+D with A+C's output in context). Still 2 calls vs 4.

### State Persistence
Python dataclass or Pydantic model serialized to JSON in Postgres (via SQLAlchemy). Per-run ID → load/save on each request from FastAPI.

### Portfolio Signal
Sophistication shows in: prompt engineering (cached personality layers, structured tool schemas, inter-agent XML debate format) and the ColonyState machine — not the framework import.

## Sources
- [Anthropic Pricing — Claude API Docs](https://platform.claude.com/docs/en/about-claude/pricing)
- [Pydantic AI vs LangGraph — ZenML Blog](https://www.zenml.io/blog/pydantic-ai-vs-langgraph)
- [Anthropic Prompt Caching — Official Docs](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)
- [LangGraph + Claude Agent SDK Guide 2026 — mager.co](https://www.mager.co/blog/2026-03-07-langgraph-claude-agent-sdk-ultimate-guide/)
- [Pydantic AI Multi-Agent Patterns](https://ai.pydantic.dev/multi-agent-applications/)
