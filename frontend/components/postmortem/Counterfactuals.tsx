import {
  type PostMortem,
  type PostMortemAgent,
} from "@/lib/api/client";

type Props = {
  data: PostMortem;
};

/**
 * Counterfactuals panel — what didn't happen.
 *
 * Surfaces three signals from the backend aggregator (SPEC §11.2):
 *   - Dormant seeds: scheduled consequences that never fired. The "if you'd
 *     done X here, Y would have happened on turn Z" hooks.
 *   - Near-miss coups: the highest-tension agent pair that hit faction
 *     formation but never crossed the coup threshold.
 *   - Didn't-happen transformations: structural slot, MVP-empty.
 *
 * Empty state matters here. Many ENDURED runs (and our Phase 7 exit run)
 * have all three sections empty. We show one explicit empty hint per
 * section instead of hiding the panel — the player should see that the
 * game DID track these, not silently elide them.
 */
export function Counterfactuals({ data }: Props) {
  const { dormant_seeds, near_miss_coups, didnt_happen_transformations } =
    data.counterfactuals;
  const allEmpty =
    dormant_seeds.length === 0 &&
    near_miss_coups.length === 0 &&
    didnt_happen_transformations.length === 0;

  const agentsById = new Map(data.agents.map((a) => [a.id, a]));

  return (
    <section className="flex flex-col gap-6 border-t border-px-border bg-px-bg px-6 py-8 md:px-10 md:py-10">
      <header className="flex flex-col gap-1">
        <h2 className="font-display text-[10px] uppercase tracking-widest text-px-accent">
          WHAT DIDN&apos;T HAPPEN
        </h2>
        <p className="font-body text-sm leading-snug text-px-text-dim">
          What the colony almost lost. What never came due.
        </p>
      </header>

      {allEmpty ? (
        <p className="font-body text-base italic leading-relaxed text-px-text-dim">
          No dormant consequences. No near-miss factions. The colony&apos;s
          history closed cleanly — for better or worse.
        </p>
      ) : null}

      {dormant_seeds.length > 0 ? (
        <CounterfactualSection title="Dormant consequences">
          <ul className="flex flex-col gap-2">
            {dormant_seeds.map((s) => (
              <li
                key={s.id}
                className="border-l-2 border-px-warning bg-px-surface px-4 py-3"
              >
                <p className="font-body text-base leading-snug text-px-text">
                  {s.origin_event_title ? (
                    <>
                      <span className="text-px-text-dim">From d</span>
                      {String(s.written_at_turn).padStart(2, "0")}
                      <span className="text-px-text-dim">
                        {" "}
                        ({s.origin_event_title}):{" "}
                      </span>
                    </>
                  ) : (
                    <>
                      <span className="text-px-text-dim">From d</span>
                      {String(s.written_at_turn).padStart(2, "0")}
                      <span className="text-px-text-dim">: </span>
                    </>
                  )}
                  {s.payload_summary}
                </p>
                <p className="mt-1 font-heading text-[10px] uppercase tracking-wider text-px-text-dim">
                  Would have fired d
                  {String(s.fires_at_turn).padStart(2, "0")}
                  {s.foreshadowed ? " · was foreshadowed" : " · was silent"}
                </p>
              </li>
            ))}
          </ul>
        </CounterfactualSection>
      ) : null}

      {near_miss_coups.length > 0 ? (
        <CounterfactualSection title="Near-miss factions">
          <ul className="flex flex-col gap-2">
            {near_miss_coups.map((coup, i) => {
              const a = agentsById.get(coup.agent_a_id);
              const b = agentsById.get(coup.agent_b_id);
              return (
                <li
                  key={`${coup.agent_a_id}-${coup.agent_b_id}-${i}`}
                  className="border-l-2 border-px-warning bg-px-surface px-4 py-3"
                >
                  <p className="font-body text-base leading-snug text-px-text">
                    {nameFor(a)} and {nameFor(b)} held grievance{" "}
                    <span className="text-px-warning">{coup.grievance_a}</span>{" "}
                    and{" "}
                    <span className="text-px-warning">{coup.grievance_b}</span>{" "}
                    on d{String(coup.turn_number).padStart(2, "0")}.
                  </p>
                  <p className="mt-1 font-heading text-[10px] uppercase tracking-wider text-px-text-dim">
                    Two more grievance points and they could have moved against
                    you.
                  </p>
                </li>
              );
            })}
          </ul>
        </CounterfactualSection>
      ) : null}
    </section>
  );
}

function CounterfactualSection({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex flex-col gap-2">
      <h3 className="font-heading text-sm uppercase tracking-wider text-px-text-dim">
        {title}
      </h3>
      {children}
    </div>
  );
}

function nameFor(a: PostMortemAgent | undefined): string {
  return a?.name ?? "unknown";
}
