"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { API_BASE_URL, type GameSessionResponse } from "@/lib/api/client";
import { createClient } from "@/lib/supabase/client";

export function BeginRunButton() {
  const router = useRouter();
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function start() {
    setPending(true);
    setError(null);
    try {
      const supabase = createClient();
      const {
        data: { session },
      } = await supabase.auth.getSession();
      if (!session) {
        setError("Session expired. Sign in again.");
        setPending(false);
        return;
      }

      const res = await fetch(`${API_BASE_URL}/game/session`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${session.access_token}`,
        },
      });

      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        const code: string =
          body?.error?.code ?? `http_${res.status}`;
        const message: string =
          body?.error?.message ?? `Could not start run (${res.status}).`;
        setError(`${code}: ${message}`);
        setPending(false);
        return;
      }

      const data: GameSessionResponse = await res.json();
      router.push(`/run/${data.game_id}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Network error.");
      setPending(false);
    }
  }

  return (
    <div className="flex flex-col gap-2">
      <Button onClick={start} disabled={pending} size="lg">
        {pending ? "Starting…" : "Begin run"}
      </Button>
      {error ? (
        <p className="font-terminal text-lg text-px-danger">{error}</p>
      ) : null}
    </div>
  );
}
