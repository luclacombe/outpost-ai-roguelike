"use client";

import { useEffect, useState } from "react";

type Props = { nextRunAt: string };

function formatRemaining(ms: number): string {
  if (ms <= 0) return "00:00:00";
  const total = Math.floor(ms / 1000);
  const h = String(Math.floor(total / 3600)).padStart(2, "0");
  const m = String(Math.floor((total % 3600) / 60)).padStart(2, "0");
  const s = String(total % 60).padStart(2, "0");
  return `${h}:${m}:${s}`;
}

export function Countdown({ nextRunAt }: Props) {
  const target = new Date(nextRunAt).getTime();
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, []);

  const remaining = target - now;
  return (
    <span className="font-display text-3xl">
      {formatRemaining(remaining)}
    </span>
  );
}
