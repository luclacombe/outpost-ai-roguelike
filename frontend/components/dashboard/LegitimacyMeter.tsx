import { cn } from "@/lib/utils";

type Props = {
  value: number;
};

const SEGMENTS = 10;

/**
 * Pixel-chunked legitimacy meter, 10 segments. Color derived from value.
 * - >=60 ok green
 * - 30-59 warning ochre
 * - <30 danger red (also adds the .status-critical pulse)
 */
export function LegitimacyMeter({ value }: Props) {
  const v = Math.max(0, Math.min(100, value));
  const filled = Math.round((v / 100) * SEGMENTS);

  const tone =
    v >= 60 ? "ok" : v >= 30 ? "warning" : "danger";

  return (
    <div className="flex items-center gap-2">
      <span className="font-heading text-[10px] uppercase text-px-text-dim">
        LEGIT
      </span>
      <div
        role="progressbar"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={v}
        aria-label="Legitimacy"
        className="flex h-3 items-stretch gap-[2px] border border-px-border bg-px-bg p-[2px]"
      >
        {Array.from({ length: SEGMENTS }, (_, i) => (
          <span
            key={i}
            className={cn(
              "block w-[8px]",
              i < filled
                ? tone === "ok"
                  ? "bg-px-ok"
                  : tone === "warning"
                    ? "bg-px-warning"
                    : "bg-px-danger"
                : "bg-px-surface",
            )}
          />
        ))}
      </div>
      <span
        className={cn(
          "font-display text-xs tabular-nums",
          tone === "danger" && "status-critical",
        )}
      >
        {String(v).padStart(2, "0")}
      </span>
    </div>
  );
}
