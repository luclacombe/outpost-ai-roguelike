"use client";

import { Button } from "@/components/ui/button";
import {
  type AgentPublic,
  type ProposalFlavor,
  type ProposalPublic,
  type Setting,
} from "@/lib/api/client";
import { cn } from "@/lib/utils";

const ORDER_COST_LEGITIMACY = 10;

const FLAVOR_LABEL: Record<ProposalFlavor, string> = {
  DOMINANT: "DOMINANT",
  MORAL: "MORAL",
  WILDCARD: "WILDCARD",
};

const FLAVOR_TONE: Record<ProposalFlavor, string> = {
  DOMINANT: "text-px-text",
  MORAL: "text-px-ok",
  WILDCARD: "text-px-warning",
};

export type ChoosePhase =
  | "idle"
  | "running"
  | "awaiting_choice"
  | "awaiting_continue"
  | "submitting"
  | "advancing"
  | "ended";

type Props = {
  proposals: ProposalPublic[];
  agents: AgentPublic[];
  setting: Setting;
  phase: ChoosePhase;
  onChoose: (proposalId: string) => void;
  onBeginTurn: () => void;
  onContinue?: () => void;
  onIssueOrder?: () => void;
  legitimacy?: number;
  closePreview?: string[];
};

export function ChoosePanel({
  proposals,
  agents,
  phase,
  onChoose,
  onBeginTurn,
  onContinue,
  onIssueOrder,
  legitimacy,
  closePreview,
}: Props) {
  const agentsById = new Map(agents.map((a) => [a.id, a]));

  if (phase === "idle") {
    return (
      <Footer>
        <div className="flex items-center justify-between gap-4">
          <p className="font-terminal text-base text-px-text-dim">
            Ready when you are.
          </p>
          <Button onClick={onBeginTurn} size="lg">
            Begin turn
          </Button>
        </div>
      </Footer>
    );
  }

  if (phase === "ended") {
    return (
      <Footer>
        <p className="font-terminal text-base text-px-text-dim">
          The run is closed. The post-mortem is being assembled.
        </p>
      </Footer>
    );
  }

  if (phase === "awaiting_continue") {
    return (
      <Footer>
        <div className="flex items-center justify-between gap-4">
          <p className="font-terminal text-base text-px-text-dim">
            Read the aftermath. The next event waits.
          </p>
          <Button onClick={onContinue} size="lg" data-testid="continue-button">
            Continue
          </Button>
        </div>
      </Footer>
    );
  }

  if (phase === "running" || phase === "advancing") {
    return (
      <Footer>
        <p className="font-terminal text-base text-px-text-dim">
          The advisors are speaking.
          <span className="cursor" />
        </p>
      </Footer>
    );
  }

  // awaiting_choice or submitting
  const submitting = phase === "submitting";
  const orderTooExpensive =
    typeof legitimacy === "number" && legitimacy < ORDER_COST_LEGITIMACY;

  return (
    <Footer>
      <div className="mb-3 flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <h2 className="font-display text-[10px] uppercase tracking-widest text-px-accent">
          CHOOSE
        </h2>
        {closePreview && closePreview.length > 0 ? (
          <p
            data-testid="close-preview"
            className="font-terminal text-sm text-px-text-dim"
          >
            {closePreview.join(" · ")}
          </p>
        ) : null}
      </div>
      <ol className="flex flex-col gap-2">
        {proposals.map((p, i) => {
          const proposer = p.proposed_by_agent_id
            ? (agentsById.get(p.proposed_by_agent_id) ?? null)
            : null;
          return (
            <li key={p.id}>
              <button
                type="button"
                onClick={() => onChoose(p.id)}
                disabled={submitting}
                className={cn(
                  "group/choice flex w-full items-start gap-3 border border-px-border bg-px-surface px-3 py-2.5 text-left transition-colors",
                  "hover:border-px-accent hover:bg-px-bg/40 focus-visible:border-px-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-px-accent",
                  "disabled:cursor-not-allowed disabled:opacity-50",
                )}
              >
                <span className="font-display text-sm tabular-nums text-px-accent">
                  {String.fromCharCode(65 + i)}
                </span>
                <span className="flex flex-1 flex-col gap-1">
                  <span className="flex flex-wrap items-baseline gap-2">
                    <span className="font-heading text-sm uppercase tracking-wider text-px-text">
                      {p.name}
                    </span>
                    <span
                      className={cn(
                        "font-heading text-[9px] uppercase tracking-wider",
                        FLAVOR_TONE[p.flavor],
                      )}
                    >
                      {FLAVOR_LABEL[p.flavor]}
                    </span>
                    {proposer ? (
                      <span className="font-heading text-[10px] uppercase text-px-text-dim">
                        · {proposer.name}
                      </span>
                    ) : null}
                  </span>
                  <span className="font-body text-sm leading-snug text-px-text">
                    {p.description}
                  </span>
                  {p.effects_preview.length > 0 ? (
                    <ul className="mt-1 flex flex-wrap gap-x-3 gap-y-0.5">
                      {p.effects_preview.map((eff, idx) => (
                        <li
                          key={idx}
                          className="font-terminal text-sm text-px-text-dim"
                        >
                          {eff}
                        </li>
                      ))}
                    </ul>
                  ) : null}
                  {p.risk_note ? (
                    <span
                      data-testid="risk-note"
                      className="font-body text-sm italic leading-snug text-px-text-dim"
                    >
                      {p.risk_note}
                    </span>
                  ) : null}
                </span>
              </button>
            </li>
          );
        })}
      </ol>
      {onIssueOrder ? (
        <div className="mt-3 flex items-center justify-end">
          <Button
            variant="ghost"
            size="sm"
            disabled={submitting || orderTooExpensive}
            onClick={onIssueOrder}
          >
            Issue direct order · −{ORDER_COST_LEGITIMACY} legit
          </Button>
        </div>
      ) : null}
    </Footer>
  );
}

function Footer({ children }: { children: React.ReactNode }) {
  return (
    <footer className="border-t border-px-border bg-px-surface px-6 py-4">
      {children}
    </footer>
  );
}
