import { AgentRoster } from "@/components/dashboard/AgentRoster";
import { EventLog, type EventLogEntry } from "@/components/dashboard/EventLog";
import { ResourceBar } from "@/components/dashboard/ResourceBar";
import {
  type AgentPublic,
  type ResourcePublic,
  type Setting,
} from "@/lib/api/client";

type Props = {
  resources: ResourcePublic[];
  agents: AgentPublic[];
  eventLog: EventLogEntry[];
  setting: Setting;
};

export function ColonyStatus({ resources, agents, eventLog, setting }: Props) {
  return (
    <aside className="flex flex-col gap-5 border-r border-px-border bg-px-surface px-4 py-5 lg:h-full">
      {/* On mobile this lives at the bottom and grows to content. On
          desktop (≥lg) it's a fixed-height column with internal log scroll. */}
      <Section label="COLONY">
        <div className="flex flex-col gap-2">
          {resources.map((r) => (
            <ResourceBar
              key={r.id}
              name={r.name}
              icon={r.icon}
              value={r.value}
              max={r.max}
              criticalThreshold={r.critical_threshold}
            />
          ))}
        </div>
      </Section>

      <Section label="ADVISORS">
        <AgentRoster agents={agents} setting={setting} />
      </Section>

      <Section label="LOG" growing>
        <div className="min-h-0 flex-1 overflow-y-auto pr-1">
          <EventLog entries={eventLog} />
        </div>
      </Section>
    </aside>
  );
}

function Section({
  label,
  children,
  growing = false,
}: {
  label: string;
  children: React.ReactNode;
  growing?: boolean;
}) {
  return (
    <section className={growing ? "flex min-h-0 flex-1 flex-col gap-2" : "flex flex-col gap-2"}>
      <h2 className="font-display text-[10px] uppercase tracking-widest text-px-text-dim">
        {label}
      </h2>
      {children}
    </section>
  );
}
