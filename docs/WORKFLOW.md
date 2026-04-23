# Outpost — Development Workflow

> How to drive this project with Claude Code. Read this at the start of each session. Covers mental model, session structure, skill triggers, recovery patterns, and commit cadence.

---

## 1. The mental model

Four documents drive everything:

```
SPEC.md          → what to build (game design, mechanics, tone)
ARCHITECTURE.md  → how to build it (stack, state model, API, schemas)
PLAN.md          → the ordered task list (phased, TDD, with exit gates)
CLAUDE.md        → invariants, commands, anti-patterns (always loaded)
```

The spec is the source of truth. When unsure, read the spec. When the spec is wrong, **update the spec first, then execute** — never code around a stale spec.

`PLAN.md` has 9 phases. Each phase has entry criteria, bite-sized TDD tasks, and exit criteria with machine-verifiable commands. Phases are the unit of planning. Tasks are the unit of execution.

---

## 2. Session structure

Sessions align with phases. One phase per session is the target; a long phase may span 2–3 sessions.

### Start of session

```
1. Open Claude Code in the Outpost repo root.
   → CLAUDE.md auto-loads. Invariants and MCP rules are in context.

2. State the phase:
   "Read docs/PLAN.md. We're starting Phase 3.
    Confirm Phase 2 exit criteria are met, then begin Task 3.1."

3. Claude reads the plan, confirms entry criteria, announces Task 3.1.
```

### During the session

```
For each task:
  a. Claude reads task, announces what it will do.
  b. Writes failing test (TDD).
  c. Runs test, confirms failure.
  d. Writes minimal implementation.
  e. Runs test, confirms pass.
  f. Runs lint + any related tests.
  g. Commits via /commit skill.
  h. Marks task complete in PLAN.md.
  i. Moves to next task.

You intervene when:
  - Claude's approach is wrong — interrupt early, don't let it build.
  - The test design is weak — ask for harder cases.
  - Claude claims "done" without running verification — insist on evidence.
```

### End of session

```
"Before we stop:
 1. Run the phase exit verification commands.
 2. Fix anything that fails.
 3. Update PLAN.md — mark completed tasks, update phase status.
 4. Commit pending work.
 5. Push."
```

### Between sessions

```
/clear    # wipe context
```

Then next session: "Read docs/PLAN.md. Continue from the first unchecked task."

Context degrades over long sessions. `/clear` between phases is the single highest-leverage hygiene move. Use `/compact` mid-session if you hit token limits but want to keep going.

---

## 3. How to drive a task

Your prompt shape matters. From best to worst:

### Best — the template

```
Implement Task 3.4 from docs/PLAN.md.
When done, mark it complete in PLAN.md.
```

Claude reads the task, follows the steps (test → fail → implement → pass → commit), marks it done.

### Good — with extra context

```
Implement Task 4.1. Use context7 to verify the latest Anthropic SDK
prompt caching syntax before writing any code. Commit after tests pass.
```

### Avoid

- "Build the agent system." (too vague)
- "Write a function that does X with Y and Z pattern." (too prescriptive — claude's judgment is usually better)
- "Do all of Phase 3." (batch too big — context will drift)

---

## 4. Skill triggers (when to invoke what)

You installed plugins with specialized skills. Use them at the right moments.

### Superpowers skills

| When | Invoke |
|------|--------|
| Before any new feature / significant new component | `superpowers:brainstorming` |
| Before writing implementation for a testable unit | `superpowers:test-driven-development` |
| Before claiming a task "done" | `superpowers:verification-before-completion` |
| Executing a plan with independent tasks across fresh contexts | `superpowers:subagent-driven-development` |
| Executing a plan inline in the current session | `superpowers:executing-plans` |
| 2+ independent tasks that could run in parallel | `superpowers:dispatching-parallel-agents` |
| Bug / test failure / unexpected behavior | `superpowers:systematic-debugging` |
| Before merging / ending a phase | `superpowers:requesting-code-review` |
| Receiving pushback on your plan or code | `superpowers:receiving-code-review` |

Some of these auto-trigger from keywords. Others you invoke explicitly (`/superpowers:brainstorming`).

### MCP servers

| MCP | When |
|-----|------|
| **context7** | Before writing code using any library you're not fluent in: FastAPI, LangGraph, Anthropic SDK, DiceBear, shadcn/ui, Tailwind v4, Next.js 16. **Always do this before guessing an API.** |
| **supabase** | All DB operations — migrations, RLS, schema inspection, direct SQL queries for debugging, auth config. |
| **playwright** | Visual testing after any UI change. Screenshot at multiple viewports, inspect rendered DOM. |
| **vercel** | Deployment status, build logs, env var management for the frontend. |

### Specialized skills

| When | Skill |
|------|-------|
| Committing changes | `/commit` |
| User-facing prose before commit | `/humanizer` |
| Before a PR | `/review` |
| Before going public | `/security-review` |
| Need research on an unfamiliar topic | `/research` |

---

## 5. Model + effort strategy

You can swap models per task. Rough guide:

| Task type | Model | Effort |
|-----------|-------|--------|
| Planning, architecture calls, debugging hard bugs | Opus 4.7 | max or high |
| Writing straightforward implementation code from a detailed task | Sonnet 4.6 | medium |
| Simple CRUD, boilerplate, renaming | Sonnet 4.6 | low |
| UI iteration with visual feedback loop | Opus 4.7 | medium |

Commands: `/model claude-sonnet-4-6`, `/effort max`. Change before the work, not after.

---

## 6. Inline vs subagent-driven execution

PLAN.md's handoff recommends a mix. Rule of thumb:

**Inline** (execute tasks in this session with you watching):
- Scaffolding (file creation, dependency installs, config)
- UI iteration (each screenshot informs the next edit)
- Deployment + polish (small wins, quick feedback)
- Phases 0, 1, 2, 6, 7, 8, 9

**Subagent-driven** (dispatch a fresh subagent per task, review between):
- Game engine logic (dense, each task benefits from fresh context)
- Agent system wiring (LangGraph + tool schemas + batched prompts)
- Eval pipeline (each metric is self-contained)
- Phases 3, 4, 5

To go subagent-driven: invoke `superpowers:subagent-driven-development` at the start of the phase. It dispatches one subagent per task with review checkpoints.

---

## 7. Commit cadence

**Commit after every passing test / task completion.** Frequent commits are cheap and create natural rollback points.

Use the `/commit` skill — it audits changes, runs safety checks, batches into logical commits, and writes Conventional Commit messages.

Example session might produce 8–15 commits. That's correct. Don't batch into "one giant commit at end of session."

---

## 8. When things go wrong

### A test is mysteriously failing

Don't propose a fix immediately. Invoke `superpowers:systematic-debugging`:
1. Reproduce the failure reliably
2. State the hypothesis
3. Collect evidence
4. Only then fix

Skipping this produces shotgun debugging — random changes that sometimes work.

### Claude keeps trying the wrong approach

Stop and rethink. Two attempts is the signal.

```
That's the second attempt. Let's step back.
What are the assumptions in this approach?
What would change if they were wrong?
```

If still stuck: `/clear` and restart with better framing. Cheaper than grinding.

### The spec says X but the code needs Y

Update the spec. Then update the code. Don't quietly diverge — you'll lose your reference point.

### Claude claims work is done but tests don't pass

Invoke `superpowers:verification-before-completion` and make it re-run verification. The rule is: evidence before assertions. "I ran the tests and they pass — here's the output" is the only acceptable claim.

---

## 9. Cross-session state

Three places state lives across sessions:

1. **`PLAN.md` checkboxes** — authoritative progress. Update them as work completes.
2. **`PLAN.md` "Errors & Learnings"** — log non-obvious failures (symptom, root cause, fix, file). Prevents re-learning next session.
3. **Project memory** (`~/.claude/projects/-Users-luclacombe-Documents-Outpost/memory/`) — auto-maintained by Claude. Read it at the start of a fresh session if continuity matters.

Between sessions: commit everything, push, `/clear`, next day open and resume.

---

## 10. Phase exit protocol

Every phase ends with the same ritual:

```
1. Run the phase's exit verification commands
   (each phase has machine-verifiable exit criteria)
2. Fix anything that fails
3. Update PLAN.md — mark completed tasks + phase status
4. Commit + push
5. /clear before starting the next phase
```

Do not skip step 5. Carrying phase-N context into phase-N+1 reliably produces conflation errors.

---

## 11. Special moments

| Moment | What to do |
|--------|-----------|
| About to go public on GitHub | Run `/security-review`, enable branch protection + secret scanning (commands in `README.md`) |
| First deploy to production | Run full `docs/SPEC.md` §16 checklist — don't call MVP complete until every box ticks |
| Adding a new setting post-launch | YAML file only, no Python changes (see `ARCHITECTURE.md` §8) |
| LLM cost seems high | Measure with Langfuse, disable Sonnet narration, check cache hit rate |
| Something is "just not working" | `superpowers:systematic-debugging` — do not skip this |

---

## 12. Quick reference — commands you'll use often

| Command | What it does |
|---------|--------------|
| `/clear` | Reset context (use between phases) |
| `/compact` | Compact conversation, keep going |
| `/model claude-opus-4-7` | Switch to Opus (planning/hard) |
| `/model claude-sonnet-4-6` | Switch to Sonnet (execution) |
| `/effort max` | Maximum reasoning depth |
| `/commit` | Smart commit with safety checks |
| `/humanizer` | Strip AI-writing tells from prose |
| `/review` | Review current branch before PR |
| `/security-review` | Security audit of pending changes |
| `/research <topic>` | Load curated research from KB |
| `/plugin` | Install or manage plugins |
| `/mcp` | Manage MCP servers |
| `!<cmd>` | Run a shell command, pipe output to conversation |

---

## 13. The first session (Phase 0)

Concretely, your next session:

```
Open Claude Code at /Users/luclacombe/Documents/Outpost

"Read docs/PLAN.md. We're starting Phase 0.
 Walk me through Task 0.1, 0.2, 0.3, 0.4, 0.5 — they're account setup
 I need to do myself. For each, list what I need to do and note when
 I've got the key, so we can add it to .env together."
```

Then Phase 0 ends when all accounts exist, CLIs installed, keys captured in `.env` (never committed). Phase 1 starts with `uv init` in a fresh session.

---

## 14. Anti-orchestration patterns

Things that feel efficient but aren't:

- **Running multiple phases in one session.** Context bloat produces silent errors.
- **Skipping exit verification.** "Tests pass on my machine" is not the same as "CI passes."
- **Giant prompts.** "Build the entire auth system" → fragmented output. Use task-by-task from PLAN.md.
- **Not using skills because they feel like bureaucracy.** Each one exists because shotgun approaches failed somewhere. Trust the pattern.
- **Committing once per session.** You lose rollback granularity. Commit per task.
- **Ignoring Errors & Learnings.** The same bug will bite you again if you don't log it.

---

If you're about to start a session and unsure where to pick up, ask: "Read docs/PLAN.md and CLAUDE.md. What's the current state? What should we do next?"
