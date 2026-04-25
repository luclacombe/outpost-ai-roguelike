import { Portrait } from "@/components/agent/Portrait";
import { type AgentPublic, type Setting } from "@/lib/api/client";
import { cn } from "@/lib/utils";

type Props = {
  agents: AgentPublic[];
  setting: Setting;
};

/**
 * Roster of advisors. Status pill is derived from health/morale/grievance
 * (purely cosmetic — the engine is the source of truth):
 *
 *   dead              → KIA, dim row
 *   health < 30       → wounded
 *   morale < 30       → tired
 *   grievance >= 7    → stewing
 *   else              → steady
 */
export function AgentRoster({ agents, setting }: Props) {
  return (
    <ul className="flex flex-col gap-1">
      {agents.map((a) => (
        <li
          key={a.id}
          className={cn(
            "flex items-center gap-2 border border-transparent bg-px-bg/40 px-2 py-1.5",
            !a.is_alive && "opacity-50",
          )}
          aria-label={`${a.name}, ${a.role}`}
        >
          <Portrait
            seed={a.portrait_seed}
            setting={setting}
            size={32}
            variant="thumb"
            alt=""
          />
          <span className="flex min-w-0 flex-1 flex-col leading-tight">
            <span className="truncate font-heading text-[11px] uppercase tracking-wider text-px-text">
              {a.name}
            </span>
            <span className="truncate font-terminal text-sm text-px-text-dim">
              {a.role}
            </span>
          </span>
          <StatusPill agent={a} />
        </li>
      ))}
    </ul>
  );
}

function StatusPill({ agent }: { agent: AgentPublic }) {
  const status = derive(agent);
  return (
    <span
      className={cn(
        "shrink-0 border border-px-border px-1.5 py-[2px] font-heading text-[9px] uppercase tracking-wide",
        status.tone === "ok" && "text-px-ok",
        status.tone === "warning" && "text-px-warning",
        status.tone === "danger" && "text-px-danger",
        status.tone === "muted" && "text-px-text-dim",
      )}
    >
      {status.label}
    </span>
  );
}

function derive(a: AgentPublic): { label: string; tone: "ok" | "warning" | "danger" | "muted" } {
  if (!a.is_alive) return { label: "KIA", tone: "muted" };
  if (a.health < 30) return { label: "WOUNDED", tone: "danger" };
  if (a.grievance >= 7) return { label: "STEWING", tone: "warning" };
  if (a.morale < 30) return { label: "TIRED", tone: "warning" };
  return { label: "STEADY", tone: "ok" };
}
