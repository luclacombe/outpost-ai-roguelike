# Outpost — Game Design Specification

**Version:** 1.0  
**Last updated:** 2026-04-23  
**Status:** Approved for implementation

> This document is the source of truth for what Outpost IS. It describes mechanics, tone, and player experience. For technical implementation, see `ARCHITECTURE.md`. For phased build plan, see `PLAN.md`.

---

## 1. Objective

Build a browser-based narrative roguelike where four AI-driven colonists and one human leader (the player) navigate a hostile setting over 8–12 turns. Decisions have moral weight, delayed consequences, and the potential to kill. Each run produces a unique story, ending in a post-mortem timeline the player can share.

The project has two audiences held in tension:

- **Players** — people who enjoy weighty, character-driven games (Papers Please, Don't Starve, Detroit: Become Human) and who will play for 15–25 minutes at a time.
- **Recruiters / hiring managers** — people evaluating the builder's portfolio, looking specifically for evidence of production-grade multi-agent AI orchestration, Python backend work, container-based deployment, LLM evaluation pipelines, and observability.

Every design decision must serve both. When they conflict, the game comes first — a shipped, complete, playable game is the portfolio artifact.

---

## 2. Core Concept

A daily-gated narrative roguelike where four AI advisors run a fragile colony under your command. You face a cascading sequence of crises. Each decision costs something — sometimes resources, sometimes trust, sometimes a life. The advisors argue, remember, form alliances, and can eventually move against you. You win if you survive; you lose if your colony collapses or your advisors oust you. Either way, a post-mortem timeline at the end shows not just what happened, but what would have happened if you'd chosen differently — including the consequences you narrowly avoided.

---

## 3. Player Experience Goals

### During a run
- **Dread that earns itself.** Each decision feels genuinely difficult. The player should spend real time on at least half of their choices.
- **Living characters.** Advisors feel like people with views, not options in a menu. The player starts referring to them by name after turn 3.
- **Bounded cognitive load.** The dashboard presents everything the player needs without drilling into nested menus. A single glance reveals colony status, advisor mood, and the current crisis.

### At run end
- **Emotional weight, not satisfaction.** The goal is not "I won" but "I did what I could."
- **Readable post-mortem.** The player can narrate their own run to a friend in under 90 seconds.
- **A story worth telling.** Post-mortem reveals a near-miss or a surprise that reframes the run — "if I'd sided with the Engineer in turn 4, the Doctor wouldn't have defected."

### Between runs
- **Curiosity, not optimization.** "What would a different setting feel like?" beats "How do I get a higher score?"
- **Daily anchoring.** One run per day per account. The single-run cap creates ritual, not FOMO.

---

## 4. Core Loop

### One run, top-down

```
Auth gate → daily gate check → setting & agents generated → 8–12 turns → post-mortem → share
```

### One turn, expanded

```
1. Event presented       — crisis, opportunity, dilemma, or discovery
2. Agent debate          — 4 advisors react, using tool calls to propose
                           actions (modify resource, confront agent, hold vote)
3. Player decides        — pick one of 3 proposed actions
4. Engine resolves       — validated tool calls apply, state updates
5. Agent autonomy        — advisors may take off-turn actions
                           (remember a slight, reconcile, scheme)
6. Narration (optional)  — short paragraph describing how the turn unfolded
7. State advance         — check deaths, consequence seeds, phase shift
```

### Concrete walkthrough — turn 5 of a run in the arctic setting

> **Event:** The heating core has sprung a leak. Temperature is dropping 1.5°C per hour. You have fuel to run the backup generator for 3 hours, or 6 hours at reduced power that leaves the lab unheated.
>
> **Debate (one batched LLM call, XML-structured):**
> - **Nadia (Climate Scientist, Idealist/Cautious/Collective):** "The lab samples represent eight months of work. I'd rather freeze three hours earlier than lose them."
> - **Marko (Commissar, Pragmatist/Bold/Collective):** "Samples won't keep us warm. Full power. Full three hours. We use that time to patch the core."
> - **Irena (Medic, Pragmatist/Cautious/Individual):** "Elena's lungs won't take a cold night. Reduced power, keep the infirmary at priority."
> - **Yuri (Radio Operator, Idealist/Bold/Individual):** "I can rig a partial bypass. Give me two hours of full power and I think I can get us through the night without burning the reserve."
>
> **Player picks:** Yuri's bypass gamble.
>
> **Resolution (engine-validated tool calls):**
> - Tool: `modify_resource(fuel, -2)` — validates, applies.
> - Tool: `schedule_event(bypass_check, turn=+1)` — writes a consequence seed.
> - Tool: `adjust_grievance(Nadia, +2)` — she didn't get her way and her samples are still at risk.
> - Tool: `adjust_grievance(Irena, +1)` — Elena sleeps cold.
>
> **Autonomous action:** Yuri and Marko have a brief, private conversation. Yuri's memory gains: "Marko backed me when Nadia objected."
>
> **Narration:** "Yuri worked through the night under Marko's flashlight. By dawn, the bypass held. Nadia did not speak at breakfast."

---

## 5. Game Mechanics

Three interlocking systems create the run's dramatic shape.

### 5.1 The Grievance Stack

Each advisor has a hidden Grievance score, integer 0–10.

**How it grows:**
- Decisions that violate an advisor's axis values increment their grievance. A Collective advisor gains +2 when the player sacrifices the group for an individual. A Cautious advisor gains +1 for a bold play that fails.
- The increment depends on how closely the decision touches that advisor's stake. Losing a direct friend: +3. Losing someone they were neutral toward: +1.

**How it drains:**
- Targeted dialogue scenes ("Listen to Nadia about the samples.") cost a turn's attention but drop grievance by 2–3.
- Resource allocation that benefits that advisor's priority drops grievance by 1–2.
- A decisive crisis success where they backed the plan drops grievance by 2 across all backers.

**What it triggers:**
- **Grievance ≥ 7, two or more advisors:** faction formation. Visible only as behavioral signals — two advisors start agreeing with each other suspiciously, appear together in private scenes, or vote as a bloc.
- **Grievance ≥ 9, two or more advisors:** coup eligibility. The faction can move against the player.

**Coup resolution (OUSTED vs KILLED):** when a validated `initiate_coup()` call fires, the engine computes:
- Sum of co-conspirator grievance (including the initiator): `total_grievance`
- Sum of co-conspirator temperament (bold bias pushes toward violence): `total_boldness`
- Current player Legitimacy

The resolution is rule-based, not LLM-decided:
- `KILLED` requires: initiator grievance ≥ 9 AND at least one co-conspirator grievance ≥ 9 AND `total_boldness > 0.5` AND Legitimacy < 15
- Otherwise the coup resolves as `OUSTED` — player loses command for the remainder of the run; the highest-legitimacy-among-survivors advisor narrates the final turns

**Rule of rarity:** a coup should occur in at most 15% of runs. `KILLED` resolutions in under 5%. These targets are verified by the end-state distribution eval (see ARCHITECTURE §12).

### 5.2 The Delayed Consequence Log

Every decision can silently write a "consequence seed" — a scheduled future event with a turn-delay timer and a payload.

**Seed lifecycle:**
1. Written at decision time, invisible to the player.
2. Two turns before firing, the seed may emit a foreshadow — a brief in-character line from a relevant advisor ("The fuel runs thin, Nadia notices") that hints at what's coming.
3. At the scheduled turn, the seed fires by injecting a high-weight event into the event pool.
4. The event resolves as a normal event, but its narrative references the originating decision.

**Foreshadowing ratio:** 70% of seeds emit a foreshadow; 30% are silent. Silent seeds are the "I didn't see that coming" moments — reserved for seeds created by decisions the player couldn't reasonably predict would matter.

**Post-mortem disclosure:** all seeds — fired and dormant — are revealed in the post-mortem. Dormant seeds show the player what they narrowly avoided. "If you'd picked full power here, the heating core would have failed on turn 9."

### 5.3 The Legitimacy Meter

A single integer 0–100 representing the colony's collective belief that your authority is warranted. Highly visible — displayed in the dashboard header.

**How it behaves:**
- **Passive decay:** −1 per turn. Governing is hard. Staying above 50 requires action.
- **Order cost:** every direct order the player issues costs Legitimacy proportional to severity. Small orders (approve a ration change) cost 1–2. Medium (overrule an advisor) cost 5–8. Extreme (exile or imprison) cost 15–25.
- **Earning it back:** decisive crisis success (+5–10), advisor "backing" (an advisor whose grievance is under 3 and whose axis aligns with the decision can donate +3 after a hard call), surviving a foreshadowed event you prepared for (+3).

**What it gates:**
- Below 30: orders start being questioned. Advisors may disobey minor commands.
- Below 10: advisors can refuse commands outright.
- At 0: automatic coup event triggered. Outcome depends on advisor grievance state — may be an ouster or a killing.

The Legitimacy meter creates the core push-pull: to act decisively, you must spend something you can't easily earn back.

### Interactions between the three systems

These three systems are intentionally linked:
- Grievance rises when you overrule advisors with orders — which also costs Legitimacy.
- Legitimacy can be donated by aligned, low-grievance advisors — so keeping advisors happy is a direct resource.
- Delayed Consequences can be triggered *by* grievance events — a high-grievance advisor may secretly sabotage, which is stored as a seed that fires later.

---

## 6. Agent System

### 6.1 Three personality axes

Each advisor is defined by three binary-ish axes (real values −1.0 to +1.0, biased toward the ends):

| Axis | Pole A | Pole B |
|------|--------|--------|
| Ethics | Pragmatist (outcomes) | Idealist (principles) |
| Temperament | Cautious (wait/minimize) | Bold (act/maximize) |
| Loyalty | Individual (person-first) | Collective (group-first) |

These axes determine:
- How they react to events (what they propose)
- Which decisions increment their grievance, and by how much
- Which other advisors they naturally ally with or oppose
- How they vote in faction formations

### 6.2 The archetype grid

A run has four advisors. The procedural generator picks one from each quadrant of a 2×2 grid formed by two of the three axes (the third axis is rolled independently per advisor), ensuring natural tension.

**Standard quadrant layout:**

| | Cautious | Bold |
|-|-|-|
| Pragmatist | The Accountant — manages resources, fears waste, warns | The Operator — gets things done, willing to bend rules |
| Idealist | The Conscience — argues for doing right even when costly | The Crusader — pushes for righteous action, willing to sacrifice |

These four archetype roles are then costumed by the setting:

- Arctic setting: The Accountant becomes the Quartermaster. The Operator becomes the Commissar. The Conscience becomes the Medic. The Crusader becomes the Radio Operator.
- Orbital setting: Accountant → Flight Engineer. Operator → Ex-Astronaut Commander. Conscience → Botanist. Crusader → AI Specialist.
- Desert setting: Accountant → Trader. Operator → Ex-Soldier Sheriff. Conscience → Ex-Teacher Doctor. Crusader → Mechanic.

### 6.3 Agent state

```
Agent {
  id: uuid
  name: localized name appropriate to setting
  role: setting-specific archetype name
  axes: { ethics, temperament, loyalty }  # -1.0 to +1.0
  portrait_seed: string (stable, drives DiceBear output)

  # Live state (mutated during run)
  health: int (0-100, 0 = dead)
  morale: int (0-100)
  grievance: int (0-10)
  relationships: { other_agent_id: int (-10 to +10) }
  memory: list<string>  # last 10 facts, injected into system prompt

  # Transformations (set once per run, resets axis pulls)
  transformation: null | { trigger_turn, description, axis_shift }
}
```

### 6.4 Transformations (Wildermyth-inspired)

At most once per run, at a high-emotion moment, an advisor can undergo a transformation — a permanent shift in how they're prompted for the rest of the run.

Examples:
- An Idealist watches a collective sacrifice fail. Transforms toward Individual.
- A Cautious advisor is the one who finally acts. Transforms toward Bold.
- A Pragmatist has to make a moral choice and takes the principled option at cost. Transforms toward Idealist.

Transformations are rare (expect one in ~50% of runs), narratively highlighted, and shown in the post-mortem. They exist to make the cast feel different across runs even when the starting archetypes repeat.

### 6.5 Agent memory

Each agent has a memory list capped at 10 entries. Entries are injected into that agent's system prompt each turn.

**What gets written:**
- Major events they witnessed or were subject to
- Decisions the player made that affected them
- Relationships changes (especially deaths)
- Their own successes and failures

**What gets evicted:** FIFO when the cap is exceeded. Memories of deaths never evict (they stay pinned).

### 6.6 Tools agents can use

Agents do not write state directly — they call tools. Every tool call is validated by the engine before effects apply.

Core tool set:
- `propose_action(name, description, effects[])` — puts an action in the player's choice pool. `effects` is a list of scheduled tool calls that fire if this proposal is chosen.
- `modify_resource(resource_id, delta, reason)` — shifts colony resources (validated against inventory)
- `modify_health(target_id, delta, reason)` — change health of an agent or the player (environmental damage, medical care, violence). `target_id` may be an agent UUID or the sentinel `"player"`.
- `adjust_grievance(target_id, delta, reason)` — change another agent's grievance. Engine-validated against axis rules — e.g., a Collective agent gaining grievance only makes sense when the decision harms the group.
- `speak_to_agent(target_id, message)` — private conversation; writes to target's memory
- `adjust_relationship(target_id, delta)` — shifts relationship score with another agent
- `adjust_own_grievance(delta, reason)` — agents can self-regulate grievance (for narrative coherence)
- `remember(fact)` — write to own memory
- `initiate_coup()` — only available when grievance ≥ 9 and at least one other agent has grievance ≥ 7. Triggers the coup resolution logic (see §5.1).
- `back_leader(decision_id, legitimacy_bonus)` — when their axis aligns and grievance < 3

### 6.7 Engine vs tool mutations (discipline)

All state mutations pass through a tool call and engine validation. There are no "engine-only" side-channel mutations. This exists so that:
- Every state change is traceable in Langfuse (tool call → result)
- Every state change is evaluable via the Tool-Call Validity metric
- The engine is a single predictable surface

Axis-derived heuristics (e.g., "Collective agent gains +2 grievance when the player sacrifices the group") are codified as **engine helpers** that *emit* tool calls (e.g., `adjust_grievance`) rather than mutate state directly. These helpers run in the resolve phase of the turn and their emitted tool calls go through the same validator as any LLM-proposed tool call.

---

## 7. Run Structure

### 7.1 Pacing arc (8–12 turns)

Runs follow a three-act structure biased by procedural noise:

| Act | Turns | Function |
|-----|-------|----------|
| Setup | 1–3 | Colony established, advisors introduced via context, resource trade-offs seeded, first moral pressure |
| Pressure | 4–7 | Delayed consequences start firing, factions start forming, stakes visibly escalate |
| Climax | 8–10 | A crisis that forces the defining decision, relationship capital pays or fails |
| Resolution | 11–12 | Aftermath, surviving characters' final actions, post-mortem |

The generator picks total turn count (8–12) based on setting and difficulty at run creation. The player does not see this number until post-mortem.

### 7.2 Difficulty tuning

Two anti-patterns to actively prevent:

**Snowballing** (one early mistake guarantees death):
- No single decision reduces any resource by more than 30%.
- Advisor deaths in turns 1–3 are prohibited by the event pool.
- Legitimacy cannot drop below 20 before turn 4 from any single action.

**Coasting** (safe strategy always works):
- Wealth-scaled threat (Rimworld model): the event pool weights scale with colony stability. A well-resourced colony gets more aggressive events. Hoarding creates pressure.
- A forced escalation event fires once per run, guaranteed, at a turn in [5–9]. This event presents a hard decision regardless of colony state.

### 7.3 Three choices per decision

Every decision point offers exactly three actions, reliably structured as:
- **The dominant-seeming option** (obvious, usually pragmatist-aligned)
- **The moral option** (ethically clean but costly)
- **The wildcard** (an agent's own creative proposal, often high-variance)

The fourth option is never present — it would produce analysis paralysis. The second option is never absent — false choice breaks moral weight.

---

## 8. Setting System

### 8.1 Setting schema (procedural from day one)

```
Setting {
  id: string                           # "arctic_station_v1"
  name: string                         # "Vorgrad Station"
  lore: string                         # 2-3 paragraphs of setup
  palette_accent: hex                  # single CSS accent color
  resources: [                         # 5 resource types
    { id, name, icon, starting, max, critical_threshold }
  ]
  advisor_archetypes: [                # 4 archetypes mapped to quadrants
    { quadrant, role_name, name_pool, description_template, portrait_options }
  ]
  event_categories: [                  # pool of events the generator pulls from
    { id, weight, tags, prompt_template, choice_templates }
  ]
  narrative_voice: string              # 1-paragraph style guide for the LLM
}
```

### 8.2 Three launch settings

Three hand-crafted settings ship at launch. The procedural system handles variation within each. A fourth setting ships as "practice/custom" — user types a theme, the system generates a setting schema via LLM (deferred feature).

---

#### Setting 1 — Vorgrad Station (Arctic Research)

**Theme:** The bones of a Soviet-era polar research station, reactivated after an Arctic geopolitical crisis scattered the Russian science corps. Your colony is an international skeleton crew: a commissar who stayed, a German glaciologist who couldn't leave, a field medic from Oslo, a radio operator who knows more than he should. The ice was never supposed to be this political.

**Palette accent:** cold steel blue (`#4a6c8c`)

**Resources:** Fuel · Food · Warmth · Medicine · Morale

**Advisor archetypes:**
- Accountant → Quartermaster (tracks every ration)
- Operator → Commissar (holds authority from the old order)
- Conscience → Medic (the body is not political)
- Crusader → Radio Operator (the world outside is listening)

**Event categories:** weather crises, equipment failures, external claims (raiders, foreign military), hidden archives from the station's Soviet era, civilians arriving on the ice

**Narrative voice:** cold, sparing prose. Use of Cyrillic in labels where appropriate. Second-person narration to the player. Never sentimental; occasionally dryly funny.

---

#### Setting 2 — Helios-7 (Orbital Colony)

**Theme:** Fourteen months since Earth went dark. Your station is one of three in orbit still broadcasting. The garden bay is producing less each week. Someone down below was working on the transmitter before the silence. No one knows if they were working on it to talk or to stop talking.

**Palette accent:** void purple (`#6c4a8c`)

**Resources:** Oxygen · Water · Power · Food · Morale

**Advisor archetypes:**
- Accountant → Flight Engineer (what the systems can take)
- Operator → Commander (what we do next)
- Conscience → Botanist (what's alive is what matters)
- Crusader → AI Specialist (someone down there is still reachable, maybe)

**Event categories:** system failures, radiation events, salvage opportunities, resource depletion, signals from Earth (true, false, ambiguous), internal dissent about whether to burn fuel to reach another station

**Narrative voice:** clinical, systems-oriented. Heavy use of station time (MET). Emotional weight hidden in mundane logs. Gallows humor in the engineering crew.

---

#### Setting 3 — Carmine Junction (Desert Outpost)

**Theme:** The old highway runs past your station. Before the collapse, it was a truck stop; now it's a waypoint where survivors stop, trade, and sometimes stay. You run it. The water well works. The road is busy enough to be dangerous and lonely enough to be haunted.

**Palette accent:** oxide red (`#8c4a4a`)

**Resources:** Water · Food · Ammunition · Shelter · Morale

**Advisor archetypes:**
- Accountant → Trader (what can be bought, what can be spared)
- Operator → Sheriff (ex-soldier, keeps order)
- Conscience → Doctor (ex-teacher, keeps faith)
- Crusader → Mechanic (ex-engineer, keeps things running)

**Event categories:** raider probes, strangers arriving (friendly, desperate, dangerous), well integrity, medical emergencies from the road, old-world salvage, moral tests involving travelers

**Narrative voice:** American Southwest gothic. Terse. Weather as character. Violence rare but decisive.

---

## 9. Player Role — Leader Mechanics

The player is inside the colony, not outside it. They have a character — unnamed, gendered by default female for the MVP narrative (overridable), with the role of "Director," "Commander," or "Warden" depending on setting.

### 9.1 Player stats

| Stat | Range | Effect |
|------|-------|--------|
| Legitimacy | 0–100 | See section 5.3 |
| Health | 0–100 | 0 = player dies = run ends |

No influence, morale, or separate stats for the player — the three-system model already gives the leader enough mechanical texture.

### 9.2 How the player acts

The player picks one of three proposed actions per turn. They can also, rarely, issue a direct **Order** (outside the proposed choices), which:
- Costs 10+ Legitimacy
- Can target a specific advisor ("Marko, handle this.")
- Always succeeds mechanically but may spike grievance

Orders are the escape valve when the three proposed actions are all bad. They are expensive on purpose.

### 9.3 How the player can die

- Grievance-triggered coup killing (rare, see 5.1)
- Environmental events that the player is caught in (rare, flagged in event design)
- Suicide by neglect — if legitimacy hits zero and the coup resolves as a killing

Death of the player ends the run. The post-mortem is written from the advisors' perspective.

---

## 10. Win / Fail States

There is no clean "win." Runs end in four possible states:

| End state | Definition | Frequency target |
|-----------|------------|-----------------|
| **Endured** | Reached turn 12 with colony functioning (≥ 2 advisors alive, critical resources above threshold, legitimacy above 20) | 35–45% |
| **Collapsed** | Colony failed — all advisors dead, critical resource at zero for 2+ turns, or structural collapse event | 30–40% |
| **Ousted** | Player removed from command via coup (run continues narratively but player no longer has agency) | 10–15% |
| **Killed** | Player dies — by coup killing, environmental event, or legitimacy zero resolution | 5–10% |

All four resolutions lead to the same post-mortem timeline view; the framing paragraph differs.

---

## 11. The Post-Mortem Timeline

The signature shareable artifact of Outpost. Replaces the score/leaderboard.

### 11.1 Structure

A vertical timeline. One entry per turn plus a header (run summary) and footer (closing paragraph).

Each turn entry shows:
- Turn number and event title
- The three choices that were available
- The choice the player made (highlighted)
- The two paths not taken (dimmed, but shown)
- Consequence seeds that fired this turn (with their origin turn)
- Consequence seeds written this turn (shown post-hoc)
- Advisor reactions in one-line summaries
- State change summary (resources, grievance shifts)

### 11.2 The "counterfactuals" section

Below the timeline, a dedicated panel reveals:
- Dormant seeds that never fired ("if you'd done X, Y would have happened on turn Z")
- Near-miss coup events ("Nadia and Irena were one grievance point from faction formation")
- Transformations that didn't happen and why ("Yuri was on the edge of transforming toward Cautious but the crisis never came")

### 11.3 Share format

The share button generates a compact square image (for social sharing) that shows:
- Setting name and end-state icon
- 12 small colored tiles (one per turn) indicating the flavor of each decision (pragmatic / moral / wildcard)
- The opening and closing lines of the run's narrative
- Outpost branding

This is Outpost's Wordle grid equivalent — pattern-readable at a glance, not a literal score.

---

## 12. Daily Gate & Authentication

### 12.1 The gate

One run per authenticated account per day, UTC. No exceptions in MVP.

Purpose:
- Cost control (runs cost $0.10–0.30 each in LLM calls)
- Bot resistance
- Creates ritual — players check in once a day rather than grinding

Attempts after the gate show: "Your next run begins at 00:00 UTC" plus a countdown.

### 12.2 Authentication

Google OAuth via Supabase Auth. Skip email/password.

The auth flow:
1. Unauthenticated users see landing with game description, screenshots, and a "Play" CTA
2. Play click → Google OAuth popup
3. On first sign-in: Supabase creates a `profiles` row linked to the auth user
4. On subsequent sign-ins: daily gate is checked server-side via `last_run_at` on profile

Auth gates *playing*. The landing page, marketing content, and post-mortem views of shared runs remain public.

---

## 13. MVP Scope

### In MVP
- Three launch settings (Vorgrad, Helios-7, Carmine Junction)
- Full 3-system mechanics (Grievance, Delayed Consequences, Legitimacy)
- 4 AI advisors per run with personality, memory, transformations
- Pixel-art dashboard UI (desktop-first)
- Google OAuth via Supabase
- Daily gate
- Post-mortem timeline + share card
- Observability (Langfuse) + eval pipeline (5 metrics, CI-integrated)
- Split FE/BE deploy (Vercel + Railway)

### Explicitly deferred
- **Custom/practice mode** — schema ready, UI not exposed. Unlocks when a player has completed 10+ runs, or for power users, via a feature flag.
- **Daily theme bias** — setting rotation by day. Current: random per run.
- **More settings beyond the 3 launch** — schema is procedural; adding a setting is a content PR.
- **Leaderboard / competitive ranking**
- **Multi-player friends** ("friends replace AI advisors" — long-term v2 dream)

### Dropped
- AI-generated setting backgrounds or event illustrations (cost, scope)
- Per-event illustrations (typography + portrait + narrative is enough)

---

## 14. Tone & Writing Voice

### 14.1 Overall tone
Grim-but-not-nihilistic. Moral weight lives here. Dark humor is permitted when it serves character. Sentimentality is banned.

References by medium:
- **Games:** Papers Please (bureaucratic moral trilemmas), Don't Starve (cold scarcity), Detroit: Become Human (stakes + hidden branching), Suzerain (cabinet dynamics), Disco Elysium (prose style, occasional)
- **Prose:** Ursula K. Le Guin's short fiction, Cormac McCarthy for the desert setting, Arkady Strugatsky for the arctic
- **Film:** The Wages of Fear, The Thing, Moon (2009)

### 14.2 Writing rules for advisors and events

**Do:**
- Write lines short. 20 words is usually enough.
- Use concrete nouns. "The fuel runs thin" beats "Resources are depleting."
- Let advisors contradict themselves over time — they're people, not positions.
- Give each advisor one verbal tic per setting (Marko: ends sentences with "yes?"; Nadia: references scientific journals; Irena: uses medical metaphors for everything; Yuri: quotes things he "heard on the radio"). Verbal tics live in the advisor's persona block and carry through to every LLM call for that agent.
- Let dialogue imply stakes; let narration state them.

**Don't:**
- No "unfortunately," "regrettably," "it seems that," or other softeners.
- No "Captain" or "sir/ma'am" — advisors address the player by role ("Director," "Commander") or by name if relationship high.
- No lines that explicitly telegraph mechanics ("My grievance is now at 7.")
- No AI-tells: avoid em dash overuse, rule-of-three lists, negative parallelism, "a tapestry of," "delve into," etc. See `humanizer` skill rules.

### 14.3 Worldbuilding discipline

Every detail should be usable in at least one gameplay moment. If a setting mentions a thing, that thing should appear in an event or an advisor line within the first three turns.

---

## 15. Out-of-Scope Design (documented for discipline)

The following are known desirable features explicitly not being built in MVP. Their architectural hooks exist so they can be added without refactor, but their implementation is not scheduled.

- **Save and resume a run.** All runs complete in-session. A browser refresh forfeits the current run (but honors the daily gate, so no second attempt today).
- **Replay viewer.** Seeing someone else's run is post-mortem-only; a full replay of the advisor debates is not a feature.
- **Multiple languages.** English only at MVP. Setting-specific proper nouns use their original script where natural (Cyrillic for Vorgrad lore flavor).
- **Accessibility features beyond WCAG AA.** Keyboard navigation, color-contrast-passing dashboard, and readable body fonts are in. Screen-reader deep support, dyslexia-friendly font option, and colorblind palettes are post-MVP.

---

## 16. Success Criteria (MVP → Ship)

The MVP ships when all of the following are true:

- [ ] A player can start a run on all three settings and complete it without any crashes, errors, or placeholder text
- [ ] Runs consistently produce 8–12 turns with coherent advisor behavior
- [ ] End-state distribution after 50 test runs is within 10% of the targets in section 10
- [ ] Eval pipeline (personality consistency, tool validity, narrative repetition, decision coherence, game balance) passes thresholds on 20 simulated games
- [ ] Per-run LLM cost stays under $0.25 at 95th percentile
- [ ] Google OAuth + daily gate function end-to-end on production
- [ ] Post-mortem timeline and share card render correctly and are shareable on Twitter/X, Discord, Bluesky
- [ ] Dashboard passes WCAG AA on all views
- [ ] Site loads in under 2 seconds on a 3G-equivalent connection
- [ ] All tests pass; CI is green; Lighthouse mobile > 85 on landing

When these are met, Outpost is MVP-complete and public-ready.
