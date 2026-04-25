import { Portrait } from "@/components/agent/Portrait";
import {
  type PostMortemAgent,
  type PostMortemTurn,
  type ProposalFlavor,
  type Setting,
} from "@/lib/api/client";
import { cn } from "@/lib/utils";

const FLAVOR_TONE: Record<ProposalFlavor, string> = {
  DOMINANT: "text-px-text",
  MORAL: "text-px-ok",
  WILDCARD: "text-px-warning",
};

const ORIGIN_LABEL: Record<string, string> = {
  NATURAL: "EVENT",
  SEED_FIRED: "CONSEQUENCE",
  FORCED_ESCALATION: "ESCALATION",
};

const ORIGIN_TONE: Record<string, string> = {
  NATURAL: "text-px-accent",
  SEED_FIRED: "text-px-warning",
  FORCED_ESCALATION: "text-px-danger",
};

type Props = {
  turn: PostMortemTurn;
  agents: PostMortemAgent[];
  setting: Setting;
};

/**
 * One vertical timeline entry per turn.
 *
 * Visual hierarchy per SPEC §11.1:
 *   - turn marker (left rail)
 *   - event title + origin badge
 *   - chosen proposal highlighted, alternatives dimmed but shown
 *   - advisor reactions one-line per agent
 *   - narration block
 *   - state delta footer (legitimacy, grievance shifts)
 *   - consequence seeds (fired this turn / written this turn)
 */
export function TurnCard({ turn, agents, setting }: Props) {
  const agentsById = new Map(agents.map((a) => [a.id, a]));
  const origin = turn.event.origin;

  return (
    <article
      className="grid grid-cols-[60px_1fr] gap-x-4 border-l-2 border-px-border pl-0"
      data-testid="post-mortem-turn"
    >
      <div className="flex flex-col items-center gap-2 pt-2">
        <span className="pixel-border bg-px-surface px-2 py-1 font-display text-[10px] uppercase tracking-widest text-px-accent">
          d{String(turn.turn_number).padStart(2, "0")}
        </span>
      </div>

      <div className="flex flex-col gap-4 pb-8">
        <header className="flex flex-col gap-1">
          <div className="flex flex-wrap items-baseline gap-3">
            <span
              className={cn(
                "font-display text-[10px] uppercase tracking-widest",
                ORIGIN_TONE[origin] ?? "text-px-text-dim",
              )}
            >
              {ORIGIN_LABEL[origin] ?? origin}
            </span>
            <span className="font-heading text-[10px] uppercase tracking-wider text-px-text-dim">
              {turn.event.category}
            </span>
            <span className="font-heading text-[10px] uppercase tracking-wider text-px-text-dim">
              · sev {turn.event.severity}
            </span>
          </div>
          <h2 className="font-heading text-lg uppercase tracking-wide text-px-text">
            {turn.event.title}
          </h2>
          {turn.event.body ? (
            <p className="max-w-prose font-body text-base leading-snug text-px-text">
              {turn.event.body}
            </p>
          ) : null}
        </header>

        <ChoicesBlock
          turn={turn}
          agentsById={agentsById}
          setting={setting}
        />

        {turn.advisor_lines.length > 0 ? (
          <AdvisorLines turn={turn} agentsById={agentsById} setting={setting} />
        ) : null}

        {turn.narration ? (
          <p
            className="max-w-prose border-l-2 border-px-accent bg-px-surface px-4 py-3 font-body text-base italic leading-relaxed text-px-text-dim"
            data-testid="turn-narration"
          >
            {turn.narration}
          </p>
        ) : null}

        <StateDelta turn={turn} agents={agents} />

        {turn.seeds_fired.length > 0 ? (
          <SeedList
            label="Consequences fired"
            tone="text-px-warning"
            seeds={turn.seeds_fired}
            verb="fired"
          />
        ) : null}
        {turn.seeds_written.length > 0 ? (
          <SeedList
            label="Seeds written"
            tone="text-px-text-dim"
            seeds={turn.seeds_written}
            verb="written"
          />
        ) : null}
      </div>
    </article>
  );
}

function ChoicesBlock({
  turn,
  agentsById,
  setting,
}: {
  turn: PostMortemTurn;
  agentsById: Map<string, PostMortemAgent>;
  setting: Setting;
}) {
  return (
    <ol className="flex flex-col gap-2">
      {turn.proposals.map((p, idx) => {
        const chosen = p.id === turn.chosen_proposal_id;
        const proposer = p.proposed_by_agent_id
          ? (agentsById.get(p.proposed_by_agent_id) ?? null)
          : null;
        return (
          <li
            key={p.id}
            data-testid={chosen ? "chosen-proposal" : "alternative-proposal"}
            className={cn(
              "flex items-start gap-3 border px-3 py-2",
              chosen
                ? "border-px-accent bg-px-surface"
                : "border-px-border/40 bg-px-bg/40 opacity-60",
            )}
          >
            <span
              className={cn(
                "shrink-0 font-display text-sm tabular-nums",
                chosen ? "text-px-accent" : "text-px-text-dim",
              )}
            >
              {String.fromCharCode(65 + idx)}
            </span>
            <div className="flex flex-1 flex-col gap-1">
              <div className="flex flex-wrap items-baseline gap-2">
                <span
                  className={cn(
                    "font-heading text-sm uppercase tracking-wider",
                    chosen ? "text-px-text" : "text-px-text-dim",
                  )}
                >
                  {p.name}
                </span>
                <span
                  className={cn(
                    "font-heading text-[9px] uppercase tracking-wider",
                    FLAVOR_TONE[p.flavor],
                  )}
                >
                  {p.flavor}
                </span>
                {proposer ? (
                  <span className="font-heading text-[10px] uppercase text-px-text-dim">
                    · {proposer.name}
                  </span>
                ) : null}
                {chosen ? (
                  <span className="font-heading text-[9px] uppercase tracking-wider text-px-accent">
                    · CHOSEN
                  </span>
                ) : null}
              </div>
              <p
                className={cn(
                  "font-body text-sm leading-snug",
                  chosen ? "text-px-text" : "text-px-text-dim",
                )}
              >
                {p.description}
              </p>
            </div>
            {proposer ? (
              <Portrait
                seed={proposer.portrait_seed}
                setting={setting}
                size={28}
                variant="thumb"
                alt=""
                className={chosen ? "" : "opacity-50"}
              />
            ) : null}
          </li>
        );
      })}
    </ol>
  );
}

function AdvisorLines({
  turn,
  agentsById,
  setting,
}: {
  turn: PostMortemTurn;
  agentsById: Map<string, PostMortemAgent>;
  setting: Setting;
}) {
  return (
    <ul className="flex flex-col gap-2">
      {turn.advisor_lines.map((line, i) => {
        const agent = agentsById.get(line.agent_id);
        if (!agent) return null;
        return (
          <li
            key={`${line.agent_id}-${i}`}
            className="flex items-start gap-3"
          >
            <Portrait
              seed={agent.portrait_seed}
              setting={setting}
              size={28}
              variant="thumb"
              alt=""
            />
            <div className="flex-1">
              <span className="font-heading text-[10px] uppercase tracking-wider text-px-accent">
                {agent.name}
              </span>
              <p className="font-body text-base leading-snug text-px-text">
                {line.text}
              </p>
            </div>
          </li>
        );
      })}
    </ul>
  );
}

function StateDelta({
  turn,
  agents,
}: {
  turn: PostMortemTurn;
  agents: PostMortemAgent[];
}) {
  const grievanceItems = agents
    .map((a) => ({
      id: a.id,
      name: a.name,
      grievance: turn.agent_grievance[a.id] ?? 0,
    }))
    .filter((g) => g.grievance > 0);

  return (
    <dl className="flex flex-wrap gap-x-4 gap-y-1 font-heading text-[10px] uppercase tracking-wider text-px-text-dim">
      <div>
        <dt className="inline">legit </dt>
        <dd className="ml-1 inline font-display text-px-text">
          {turn.legitimacy}
        </dd>
      </div>
      <div>
        <dt className="inline">cost </dt>
        <dd className="ml-1 inline font-display text-px-text">
          {turn.cost_cents}¢
        </dd>
      </div>
      {grievanceItems.map((g) => (
        <div key={g.id}>
          <dt className="inline">{g.name} </dt>
          <dd className="ml-1 inline font-display text-px-warning">
            grievance {g.grievance}
          </dd>
        </div>
      ))}
    </dl>
  );
}

function SeedList({
  label,
  tone,
  seeds,
  verb,
}: {
  label: string;
  tone: string;
  seeds: PostMortemTurn["seeds_fired"];
  verb: "fired" | "written";
}) {
  return (
    <div className="flex flex-col gap-1 border-t border-px-border/40 pt-2">
      <span
        className={cn("font-display text-[10px] uppercase tracking-widest", tone)}
      >
        {label}
      </span>
      <ul className="flex flex-col gap-0.5">
        {seeds.map((s) => (
          <li key={s.id} className="font-terminal text-base text-px-text">
            <span className="text-px-text-dim">
              {verb === "fired"
                ? `from d${String(s.written_at_turn).padStart(2, "0")} → `
                : `→ d${String(s.fires_at_turn).padStart(2, "0")}: `}
            </span>
            {s.payload_summary}
          </li>
        ))}
      </ul>
    </div>
  );
}
