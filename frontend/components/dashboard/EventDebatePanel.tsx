import { Portrait } from "@/components/agent/Portrait";
import {
  type AgentPublic,
  type EventPublic,
  type Setting,
} from "@/lib/api/client";

export type DebateLine = {
  agent_id: string;
  text: string;
  /** True while the line is still streaming in. */
  partial?: boolean;
};

type Props = {
  currentEvent: EventPublic | null;
  agents: AgentPublic[];
  setting: Setting;
  debateLines: DebateLine[];
  narration: string | null;
};

const SEVERITY_LABEL: Record<number, string> = {
  1: "minor",
  2: "notable",
  3: "serious",
  4: "severe",
  5: "catastrophic",
};

export function EventDebatePanel({
  currentEvent,
  agents,
  setting,
  debateLines,
  narration,
}: Props) {
  const agentsById = new Map(agents.map((a) => [a.id, a]));

  return (
    <div className="flex flex-col gap-5 border-b border-px-border bg-px-bg px-6 py-5 lg:h-full">
      {/* `lg:h-full` (≥1024) so desktop's overflow-y scroll has a
          constrained parent to push against. Below lg, the page
          scrolls naturally. */}
      <EventHeader event={currentEvent} />

      {currentEvent ? <EventBody event={currentEvent} /> : <Idle />}

      {debateLines.length > 0 ? (
        <Debate lines={debateLines} agentsById={agentsById} setting={setting} />
      ) : null}

      {narration ? <Narration text={narration} /> : null}
    </div>
  );
}

function Idle() {
  return (
    <p className="font-terminal text-xl leading-snug text-px-text-dim">
      The post is quiet. Begin the day&apos;s turn to draw the next event.
    </p>
  );
}

function EventHeader({ event }: { event: EventPublic | null }) {
  if (!event) {
    return (
      <h2 className="font-display text-[10px] uppercase tracking-widest text-px-text-dim">
        AWAITING ORDERS
      </h2>
    );
  }
  return (
    <div className="flex flex-wrap items-baseline gap-3">
      <h2 className="font-display text-[10px] uppercase tracking-widest text-px-accent">
        EVENT
      </h2>
      <span className="font-heading text-[10px] uppercase text-px-text-dim">
        {event.category}
      </span>
      <span className="font-heading text-[10px] uppercase text-px-text-dim">
        · {SEVERITY_LABEL[event.severity] ?? `sev ${event.severity}`}
      </span>
      {event.origin === "SEED_FIRED" ? (
        <span className="font-heading text-[10px] uppercase text-px-warning">
          · CONSEQUENCE
        </span>
      ) : null}
    </div>
  );
}

function EventBody({ event }: { event: EventPublic }) {
  return (
    <article className="flex flex-col gap-3">
      <h3 className="font-heading text-lg uppercase tracking-wide text-px-text">
        {event.title}
      </h3>
      <p className="cursor max-w-prose font-body text-base leading-relaxed text-px-text">
        {event.body}
      </p>
    </article>
  );
}

function Debate({
  lines,
  agentsById,
  setting,
}: {
  lines: DebateLine[];
  agentsById: Map<string, AgentPublic>;
  setting: Setting;
}) {
  return (
    <section className="flex flex-col gap-3 border-t border-px-border pt-4">
      <h3 className="font-display text-[10px] uppercase tracking-widest text-px-text-dim">
        DEBATE
      </h3>
      <ul className="flex flex-col gap-3">
        {lines.map((line, i) => {
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
                size={36}
                variant="thumb"
                alt=""
              />
              <div className="flex flex-1 flex-col gap-1">
                <span className="font-heading text-[10px] uppercase tracking-wider text-px-accent">
                  {agent.name}
                  <span className="ml-1.5 text-px-text-dim">
                    · {agent.role}
                  </span>
                </span>
                <p
                  className={
                    line.partial
                      ? "cursor font-body text-base leading-snug text-px-text"
                      : "font-body text-base leading-snug text-px-text"
                  }
                >
                  {line.text}
                </p>
              </div>
            </li>
          );
        })}
      </ul>
    </section>
  );
}

function Narration({ text }: { text: string }) {
  return (
    <section className="border-t border-px-border pt-4">
      <h3 className="font-display text-[10px] uppercase tracking-widest text-px-text-dim">
        AFTER
      </h3>
      <p className="font-body text-base italic leading-relaxed text-px-text-dim">
        {text}
      </p>
    </section>
  );
}
