import { LegitimacyMeter } from "@/components/dashboard/LegitimacyMeter";

type Props = {
  settingName: string;
  turnNumber: number;
  maxTurns: number;
  legitimacy: number;
  costCents?: number | null;
};

const DEV_COST = process.env.NEXT_PUBLIC_SHOW_COST === "1";

export function DashboardHeader({
  settingName,
  turnNumber,
  maxTurns,
  legitimacy,
  costCents,
}: Props) {
  return (
    <header className="border-b border-px-border bg-px-surface px-5 py-3">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div className="flex items-baseline gap-3">
          <span className="font-display text-[10px] uppercase tracking-widest text-px-accent">
            OUTPOST
          </span>
          <span className="font-heading text-sm uppercase text-px-text-dim">
            ·
          </span>
          <h1 className="font-heading text-base uppercase tracking-wider text-px-text">
            {settingName}
          </h1>
        </div>

        <div className="flex items-center gap-6">
          <TurnBadge current={turnNumber} max={maxTurns} />
          <LegitimacyMeter value={legitimacy} />
          {DEV_COST && typeof costCents === "number" ? (
            <span className="font-terminal text-base text-px-text-dim">
              ${(costCents / 100).toFixed(2)}
            </span>
          ) : null}
        </div>
      </div>
    </header>
  );
}

function TurnBadge({ current, max }: { current: number; max: number }) {
  const safeCurrent = Math.max(0, current);
  return (
    <div className="flex items-baseline gap-1.5">
      <span className="font-heading text-[10px] uppercase text-px-text-dim">
        DAY
      </span>
      <span className="font-display text-sm tabular-nums text-px-text">
        {String(safeCurrent).padStart(2, "0")}
      </span>
      <span className="font-heading text-[10px] uppercase text-px-text-dim">
        / {max}
      </span>
    </div>
  );
}
