# Research: Game Design & Procedural Narrative

> Research agent output from 2026-04-23. Source for game mechanics, narrative systems, and leader-dynamics design. Consulted when making decisions in `docs/SPEC.md` sections 6, 7, 8.

## Emergent Narrative Systems

### Rimworld's Storyteller AI
Cassandra Classic uses a "wealth-based threat budget" — total asset value converts to Raid Points which scale threat difficulty linearly. As wealth doubles, raid intensity nearly doubles. She enforces a cooldown of at least two in-game days between major events and targets 1-2 major threats per quadrum (~7-10 days).

**Key insight:** player success directly generates adversity. Hoarding resources is the trigger, not a timer.

Randy Random bypasses this (pure chaos). Phoebe Chillax softens the curve. The "storyteller" is a filter on procedural events, not a reasoner.

**Translates to Outpost:** wealth-curve logic (more stable colony → more pressure agents can bring), cooldown windows as breathing room after major decisions.

### Dwarf Fortress Personality/Grudge System
Dwarves have 50+ personality facets scored 0-100. Two dwarves accumulate a compatibility score through idle conversation. If their values diverge by more than 60 points on an axis, conversations generate stress rather than friendship, eventually formalizing as a grudge that inflicts 700+ stress per day just from proximity.

**Critical design insight:** relationships are computed consequences of trait differences, not scripted.

### Wildermyth's Character Arc System
Characters gain transformations (body-part replacement by mythic elements — stone arm, owl head) through story event outcomes that permanently alter stats and appearance. Transformation happens mid-campaign and carries forward.

**Key insight:** "authored feeling" comes through narrative consequence persistence, not branching dialogue.

### What Translates to LLM Agents
LLM agents can carry memory of events and adjust reasoning. The gap: simulation systems are deterministic engines with stochastic inputs. LLMs must be explicitly prompted with "what you know, what you want, what you fear" state vectors each turn or drift across a 10-turn run.

## Meaningful Decision Design

### Papers Please — Bureaucratic Moral Dilemmas
Core mechanic: time-money-ethics trilemma. Player earns money per approved traveler, loses money for errors, needs money for family. Moral dilemmas impose direct financial cost on compassion. No safe third option.

Dominant strategy (approve all quickly) is sabotaged by escalating rule complexity. **The dilemma is structural, not rhetorical.**

### Don't Starve — Scarcity Trade-offs
Seasonal irreversibility: food preserved in summer cannot be retroactively gathered in winter. Decisions feel meaningful because they are asymmetric in time.

### Detroit: Become Human — Branching Consequences
Flowchart shown after each chapter. Many branches converge to same outcomes (false breadth). **Lesson for Outpost (inverse):** appearance of convergence makes divergence hit harder. Hide branching until post-mortem.

### Avoiding Dominant Strategies (common pattern)
The dominant strategy becomes the threat generator:
- Papers Please: escalate rule complexity
- Don't Starve: time-limit the safe strategy with seasons
- Rimworld: wealth-scaling means hoarding generates worse raids

### Decision Count
Academic and empirical convergence on **3 choices per decision point**:
- A dominant-seeming option
- A moral option
- An unexpected option (source of emergent narrative)

4 options → analysis paralysis. 2 options → false choice.

## Leader-with-Agents Mechanics

### Frostpunk — Dual-Meter Authority
Hope and Discontent are opposing meters. High Hope buffs resistance to Discontent; high Discontent accelerates Hope decay. Game ends at either extreme. Laws let you cheat the system (public executions eliminate Discontent triggers) but end-game recap narrates these choices back harshly.

**Insight:** authority is not a resource to accumulate but a relationship to maintain under pressure.

### Crusader Kings II — Opinion + Faction Threshold
Vassal rebellion requires two thresholds:
1. Individual opinion below trait-determined floor (ambitious + low opinion = plotting)
2. Faction strength (sum of vassal military power aligned against you) exceeds your own

Rebellion requires coordination among the discontented. Character traits are explicit modifiers.

### Rimworld — Mood Stack and Mental Breaks
Mood has three threshold lines (minor at 35%, major at 20%, extreme at 5%). Mood Target (instant) vs displayed mood (lagged average) — the lag means the player can see a colonist tipping before the break fires.

### Suzerain — Cabinet Divergence
Ministers have internally-consistent but conflicting ideological priorities. Tension is architectural (baked into minister design) rather than dynamically generated. Player cannot satisfy all ministers simultaneously.

### Balancing Player-Can-Be-Killed (CK model)
A single agent cannot kill the player. It requires faction formation, meaning multiple agents who coordinate. Requires:
- Time for grievances to accumulate
- A triggering event that makes coordination rational
- At least one agent willing to initiate

**Tuning "rare but real":** minimum 2 agents conspiring, each needing specific grievance level (not just low mood), give player one warning signal before the act is possible.

## Delayed Consequences

### Concrete Examples
- **Rimworld:** "colonist lost a loved one" thought persists for 30 in-game days, compounding mood drain. A social fight in turn 2 can trigger a mental break during a raid in turn 8.
- **Don't Starve:** berry bushes overharvested in summer are depleted for winter. Choice at peak abundance; consequence at peak need.
- **Frostpunk:** signing "Child Labor" law in early crisis removes it as a future option, affects end-game recap even if colony thrives.
- **Detroit:** killing a character in chapter 3 removes their narrative thread for chapters 5-9 (only noticed on second playthrough).

### Surfacing to the Player
Frostpunk + Rimworld use foreshadowing (meter warnings, thought logs) before consequences fire. Detroit uses post-hoc flowchart reveal.

**Optimal ratio for Outpost:** 70% foreshadowed (agent explicitly comments on grievance), 30% surprise (consequence fires through a chain the player didn't model).

## Run Structure for 8-12 Turns

### Pacing Arc
- **Turns 1-3:** resource scarcity and relationship establishment. Agents reveal personalities; initial trade-offs introduced.
- **Turns 4-7:** compounding pressure. Prior decision's delayed consequence fires. Resource trade-off tightens. Agents begin to disagree visibly.
- **Turns 8-10:** climax. Crisis forces highest-stakes decision. Prior relationship capital determines which agents hold vs. defect.
- **Turns 11-12:** resolution, post-mortem.

### Preventing Snowballing
Wealth-scaling (Rimworld model): successful resource management escalates difficulty of the next event. Each "win" in turns 1-5 should introduce a new cost or constraint that activates in turns 6-10.

### Preventing Coasting
Mandatory structural escalation independent of player performance — a "winter arrives" event that forces a hard decision regardless of colony state. Don't Starve's seasonal clock as template.

## Advisor Personality Archetypes

### Productive Axes for 3-4 Agents
Three axes generate sufficient cross-conflict:
1. **Pragmatist vs. Idealist** — disagree on whether outcomes justify means
2. **Risk-tolerant vs. Risk-averse** — disagree on whether to act or wait
3. **Individual-focused vs. Collective-focused** — disagree on who bears the cost

A 4-agent party covering: (Pragmatic/Bold/Collective), (Idealist/Cautious/Individual), (Pragmatic/Cautious/Individual), (Idealist/Bold/Collective) produces natural 2v2 splits, preventing default consensus.

### Preventing Repetition Across Runs
Wildermyth's solution: give each agent a transformation event — a mid-run moment that shifts their expressed values. An agent who started Pragmatic/Collective but watched a collective sacrifice fail may shift toward Idealist/Individual.

## Three Concrete Mechanical Proposals for Outpost

### 1. The Grievance Stack with Faction Threshold
Each agent has a hidden Grievance score (0-10). Specific decisions increment it based on axis positions (e.g., Collective agent gains +2 grievance when player sacrifices a group member). When two agents both reach grievance 7+, they can form a faction — visible only as behavioral signal (they start agreeing with each other suspiciously). At grievance 9+ for both, they can initiate a kill/oust action.

Player can drain grievances through targeted dialogue or resource allocation.

Implements the Crusader Kings coalition model: one discontented agent is manageable; two coordinated ones are existential.

### 2. The Delayed Consequence Log (visible post-mortem only)
Every decision secretly writes a "consequence seed" with a turn-delay timer. Player sees no consequence counter during play. After the run, post-mortem timeline reveals which seeds fired and which dormant seeds never triggered — showing near-misses as vividly as catastrophes.

Mechanically: seeds fire via event injection. At the specified turn, the scenario's event pool gains a new high-weight event drawn from the seed's consequence type.

70/30 foreshadowing ratio: 70% of seeds emit a single NPC comment 2 turns before firing; 30% are silent.

Creates Detroit's "I didn't see that coming" effect without false choice — the consequence was real, it just wasn't telegraphed.

### 3. The Legitimacy Meter (Frostpunk Inverted)
Single Legitimacy score (0-100) representing the colony's collective belief that the leader's authority is warranted. Decays passively each turn. Specific actions raise or lower it: decisive crisis response raises; vacillating or ignoring agent input drops.

Each law/order costs Legitimacy proportional to severity — "emergency rationing" costs 5, "exile a colonist" costs 20. Running Legitimacy to zero triggers an agent-faction oust event.

Forces player to spend authority deliberately rather than accumulate it. An agent with high Collective values and low grievance can donate Legitimacy back through "I back this decision" action — making inter-agent politics directly relevant to survival.

## Sources
- [AI Storytellers - RimWorld Wiki](https://rimworldwiki.com/wiki/AI_Storytellers)
- [Papers, Please - Wikipedia](https://en.wikipedia.org/wiki/Papers,_Please)
- [Papers, Please systemic approach to ethical expertise - Springer](https://link.springer.com/article/10.1007/s10676-016-9407-z)
- [Discontent - Frostpunk Wiki](https://frostpunk.fandom.com/wiki/Discontent)
- [Vassals - Crusader Kings II Wiki](https://ck2.paradoxwikis.com/Vassals)
- [DF2014:Grudge - Dwarf Fortress Wiki](https://dwarffortresswiki.org/index.php/DF2014:Grudge)
- [Characterization and Emergent Narrative in Dwarf Fortress](https://www.researchgate.net/publication/356686095_Characterization_and_Emergent_Narrative_in_Dwarf_Fortress)
- [Mood - RimWorld Wiki](https://rimworldwiki.com/wiki/Mood)
- [Design Journal: Villains - Wildermyth DevLog](https://wildermyth.itch.io/wildermyth/devlog/81164/design-journal-villains)
- [Designing for Mastery in Roguelikes - Grid Sage Games](https://www.gridsagegames.com/blog/2025/08/designing-for-mastery-in-roguelikes-w-roguelike-radio/)
