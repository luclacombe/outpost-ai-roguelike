# Research: LLM Eval & Observability

> Research agent output from 2026-04-23. Source for observability and eval pipeline decisions. Consulted when making decisions in `docs/ARCHITECTURE.md` sections 11-12, `docs/PLAN.md` phase 5.

## Decision Matrix: LangSmith vs Langfuse

| Criterion | LangSmith | Langfuse | Winner |
|---|---|---|---|
| Free tier | 5,000 traces/mo, 14-day retention, 1 seat | 50,000 observations/mo, no seat limit | **Langfuse** |
| Paid pricing | $39/seat/month (Plus) | $29/month flat (Core), no per-seat | **Langfuse** |
| LangGraph tracing | Native — 2 env vars, zero code | CallbackHandler — 3-4 lines | LangSmith (marginally) |
| Setup complexity | `LANGSMITH_TRACING=true` + key | `pip install langfuse` + 3-line CallbackHandler | LangSmith |
| Self-hosted | Enterprise license only | MIT open source, Docker Compose, ~4 vCPU / 16 GB RAM | **Langfuse** |
| Cost tracking per run | Yes, native | Yes, same via callbacks | Tie |
| Multi-agent dashboard UX | Thread-level grouping, timeline, tool call drill-down | Agent Graph view, visual node graph per run | Tie |
| CI/CD eval integration | First-class: pytest + GitHub Actions, `agentevals` package, dataset mgmt | Possible via SDK, less opinionated about CI | **LangSmith** |
| LLM-as-judge built-in | Evaluators UI + custom Python evaluators | LLM-as-judge panel, scores API | Tie |
| Portfolio/CV value | Proprietary SaaS | Open source, self-hostable = more impressive infra story | **Langfuse** |

### Recommendation: **Langfuse (cloud Hobby tier) primary, LangSmith agentevals for CI**

50K observations/month is plenty for a portfolio project (~200 obs/run × ~250 runs free). Self-hosting is an option to mention in README without actually doing it.

Use LangSmith specifically for `agentevals` trajectory evaluation package + CI pipeline — integrates with pytest/GitHub Actions better than Langfuse does out of the box.

## Alternatives (considered, not chosen)
- **Arize Phoenix** (open source, OpenTelemetry-native, free) — excellent embedding drift analysis but weaker dashboard polish
- **Braintrust** — 1M trace spans + 10K eval runs free; eval-first design
- **Helicone** — proxy-style, adds network hop; skip
- **DeepEval** — eval test runner, not observability; **use alongside** Langfuse

## Concrete Eval Metric Definitions

### 1. Personality Consistency Score
- **Measures:** Does agent maintain character voice, priorities, decision style across a run?
- **Compute:**
  - Embed each agent response (text-embedding-3-small) + character profile (50-100 words).
  - `personality_score = mean(cosine_sim(response_i, profile_embedding))`
  - Layer Haiku-as-judge: prompt with character sheet + response → 0-1 score + rationale. ~$0.002/turn.
- **Threshold:** Flag runs <0.72. Drift between turns 1-4 vs 8-12 is the interesting signal.
- **Signal quality:** High, novel metric — stands out on CV.

### 2. Tool-Call Validity Rate
- **Measures:** % of tool calls passing engine validation (skill, target, resources).
- **Compute:** Instrument engine validation. Emit `tool_valid: bool` + `rejection_reason: str` per call.
  ```python
  langfuse.score(trace_id=trace_id, name="tool_validity",
                 value=1.0 if valid else 0.0, comment=rejection_reason)
  ```
- **Threshold:** Alert <85% run-level. Dataset <90% suggests prompt/schema bug.
- **Signal quality:** Directly measurable, zero LLM cost.

### 3. Narrative Repetition Index
- **Measures:** Boilerplate phrases across turns — signals prompt fatigue.
- **Compute:** Pairwise BLEU-2 or Jaccard similarity between agent utterances within a run.
- **Threshold:** >0.35 intra-run = "repetitive", tag for manual review.
- **Signal quality:** Medium. Cheap, catches obvious failures.

### 4. Decision Coherence (LLM-as-Judge)
- **Measures:** Does action align with stated reasoning + personality?
- **Compute:** `{character_sheet, agent_reasoning, action_taken}` → Claude Haiku judge → 0-1.
- **Cost:** ~$0.003/turn × 4 agents × 10 turns = ~$0.12/run.
- **Threshold:** Flag turns <0.6. Flag runs >2 incoherent turns.

### 5. Game Balance Proxies
- **Run survival rate:** % runs with any colonist surviving to turn 12 (target 40-60%)
- **Resource volatility:** std dev of key resource across turns (high = dramatic)
- **Forced choice rate:** % turns with only one valid action (<20% target)

Zero LLM cost — direct from engine logs.

## LLM-as-Judge Prompt Structure

```
You are evaluating whether an AI agent stayed in character.

CHARACTER SHEET:
{character_name}: {character_description}
Core values: {values_list}
Typical reasoning style: {style_description}

GAME CONTEXT (turn {n}):
{situation_summary}

AGENT'S RESPONSE:
{agent_utterance_and_action}

Score from 0.0 to 1.0:
- 1.0 = fully in character, reasoning matches values, voice consistent
- 0.5 = somewhat in character but generic or slightly off
- 0.0 = clearly out of character or contradicts stated values

Return JSON: {"score": float, "reasoning": "one sentence"}
```

**Design notes:** Claude Haiku as judge (cheap, fast). Structured JSON output. Binary-anchored rubric (0/0.5/1.0 more reliable than 0-10 scales per research).

## Recommended Eval Pipeline Architecture

```
Nightly (or per-PR):
  1. Run N=20 simulated games (seeded scenarios)
  2. Per run: engine emits tool validity events → Langfuse scores API
  3. Per turn: Haiku judge scores personality consistency + decision coherence → Langfuse scores
  4. Post-run: compute repetition index → Langfuse scores
  5. Post-batch: aggregate metrics → compare against baseline thresholds

GitHub Actions step:
  - `pytest tests/eval_pipeline.py` using LangSmith `agentevals` + dataset
  - Fail PR if: tool_validity < 90%, avg personality < 0.72,
    or survival_rate outside [30%, 70%]

Manual review (weekly):
  - Pull Langfuse traces flagged as low-coherence turns
  - Read the actual conversation to diagnose prompt issues
```

## LangGraph Integration Code Pattern (Langfuse)
```python
import os
from langfuse.callback import CallbackHandler

langfuse_handler = CallbackHandler(
    public_key=os.environ["LANGFUSE_PUBLIC_KEY"],
    secret_key=os.environ["LANGFUSE_SECRET_KEY"],
    host="https://cloud.langfuse.com",
    session_id=f"game-run-{run_id}",
    user_id="outpost-game",
    metadata={"turn": turn_number, "scenario": scenario_name}
)

result = graph.invoke(
    state,
    config={"callbacks": [langfuse_handler],
            "configurable": {"thread_id": run_id}}
)
```

For LangSmith (parallel CI):
```python
os.environ["LANGSMITH_TRACING"] = "true"
os.environ["LANGSMITH_API_KEY"] = "..."
# LangGraph picks it up automatically
```

## Trace Hierarchy
```
Trace: game_run_{run_id}
  metadata: scenario_id, seed, start_timestamp, agents[]
  └─ Span: turn_{n}
       metadata: turn_number, active_threats[], resource_snapshot_before
       ├─ Span: agent_{name}_action
       │    metadata: agent_state, personality_summary
       │    ├─ LLM: reasoning_call (Haiku — context + tool schema)
       │    └─ Tool: {tool_name} (result + valid:bool)
       └─ Span: game_engine_tick
            metadata: resource_snapshot_after, events_triggered[]
```

Answers "why did Kira do something weird in turn 7": open trace → turn_7 → kira_action → see exact prompt, reasoning, tool call, validity, state before/after. One click deep.

## Recommended Stack
| Layer | Tool | Rationale |
|---|---|---|
| Tracing / observability | **Langfuse (cloud Hobby)** | 50K free obs/mo, open source credibility, LangGraph callback support |
| CI eval runner | **LangSmith agentevals + pytest** | Best-in-class trajectory eval, pytest integration, GitHub Actions |
| Eval framework | **DeepEval (G-Eval metric)** | Custom LLM-as-judge via G-Eval, agent evaluation mode |
| Personality scoring | Custom: sentence-transformers cosine + Haiku judge | Cheaper, fully explainable |
| Game balance metrics | Direct from engine logs → Langfuse scores API | Zero LLM cost, always accurate |

## Portfolio Pitch (writes itself)
"Custom eval pipeline running 20 simulated games per PR. Automated personality consistency scoring (cosine + LLM-judge), tool-call validity tracking, and narrative repetition detection. Build fails if quality drops below threshold. All traces in Langfuse — open source, self-hostable."

## Sources
- [LangSmith Plans and Pricing](https://www.langchain.com/pricing)
- [Langfuse vs LangSmith: An Independent Verdict | TECHSY](https://techsy.io/en/blog/langfuse-vs-langsmith)
- [Open Source Observability for LangGraph - Langfuse](https://langfuse.com/guides/cookbook/integration_langgraph)
- [GitHub - langchain-ai/agentevals](https://github.com/langchain-ai/agentevals)
- [G-Eval | DeepEval](https://deepeval.com/docs/metrics-llm-evals)
- [LLM-as-a-judge: a complete guide - Evidently AI](https://www.evidentlyai.com/llm-guide/llm-as-a-judge)
