import { cn } from "@/lib/utils";

const SEGMENTS = 16;

type Props = {
  name: string;
  icon?: string;
  value: number;
  max: number;
  criticalThreshold: number;
};

/**
 * Pixel-chunked resource bar — 16 segments wide.
 *
 * Two-layer paint: a track div carries the empty pattern (vertical dividers
 * via `repeating-linear-gradient`), and a fill div on top is sized by
 * percentage and animates its width via `.resource-bar-fill` (a stepped
 * 400ms transition). The result: when the value changes between turns, the
 * fill grows or shrinks one segment at a time — the canonical pixel-game
 * feel from SPEC §3.
 *
 * Tone:
 *   value > critical:               accent (per setting)
 *   value <= critical:              warning ochre
 *   value <= half of critical:      pulsing critical
 */
export function ResourceBar({
  name,
  icon,
  value,
  max,
  criticalThreshold,
}: Props) {
  const safeMax = Math.max(1, max);
  const safeValue = Math.max(0, Math.min(safeMax, value));
  const ratio = safeValue / safeMax;
  const pct = ratio * 100;

  const isCritical = value <= criticalThreshold;
  const isCatastrophic = value <= Math.max(1, Math.floor(criticalThreshold / 2));
  const fillColor = isCritical ? "var(--px-warning)" : "var(--px-accent)";

  // Vertical-bar segment dividers, painted on top of the fill so the chunked
  // look survives whether the segment is filled or not. Stops are computed in
  // percent so the pattern stays sharp at any pixel width.
  const stop = 100 / SEGMENTS;

  return (
    <div className="grid grid-cols-[60px_1fr_60px] items-center gap-2">
      <span className="flex items-center gap-1.5">
        {icon ? (
          <span aria-hidden className="font-terminal text-base text-px-text-dim">
            {icon}
          </span>
        ) : null}
        <span className="font-heading text-[10px] uppercase tracking-wider text-px-text-dim">
          {name}
        </span>
      </span>

      <span
        role="progressbar"
        aria-label={name}
        aria-valuemin={0}
        aria-valuemax={max}
        aria-valuenow={value}
        className="relative h-3 border border-px-border bg-px-bg p-[2px]"
      >
        <span aria-hidden className="relative block h-full w-full bg-px-surface">
          <span
            aria-hidden
            className="resource-bar-fill absolute inset-y-0 left-0"
            style={{ width: `${pct}%`, background: fillColor }}
          />
          {/* dividers paint on top to keep chunks visible whether filled or not */}
          <span
            aria-hidden
            className="pointer-events-none absolute inset-0"
            style={{
              backgroundImage: `repeating-linear-gradient(90deg, transparent 0 calc(${stop}% - 1px), var(--px-bg) calc(${stop}% - 1px) ${stop}%)`,
            }}
          />
        </span>
      </span>

      <span
        className={cn(
          "text-right font-display text-[10px] tabular-nums",
          isCatastrophic && "status-critical",
          isCritical && !isCatastrophic && "text-px-warning",
        )}
      >
        {String(value).padStart(2, "0")}
        <span className="text-px-text-dim">/{max}</span>
      </span>
    </div>
  );
}
