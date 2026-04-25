import { redirect } from "next/navigation";

import { DashboardShell } from "@/components/dashboard/DashboardShell";
import {
  API_BASE_URL,
  type GameSessionResponse,
  isApiErrorEnvelope,
} from "@/lib/api/client";
import { createClient } from "@/lib/supabase/server";

type Params = { id: string };

async function loadGame(
  gameId: string,
): Promise<
  | { ok: true; state: GameSessionResponse; accessToken: string }
  | { ok: false; status: number; reason: string }
> {
  const supabase = await createClient();
  const {
    data: { session },
  } = await supabase.auth.getSession();
  if (!session) return { ok: false, status: 401, reason: "no_session" };

  try {
    const res = await fetch(`${API_BASE_URL}/game/${gameId}`, {
      headers: { Authorization: `Bearer ${session.access_token}` },
      cache: "no-store",
    });
    if (!res.ok) {
      const body = (await res.json().catch(() => ({}))) as unknown;
      const reason = isApiErrorEnvelope(body)
        ? body.error.code
        : `http_${res.status}`;
      return { ok: false, status: res.status, reason };
    }
    const state = (await res.json()) as GameSessionResponse;
    return { ok: true, state, accessToken: session.access_token };
  } catch (e) {
    return {
      ok: false,
      status: 0,
      reason: e instanceof Error ? e.message : "network_error",
    };
  }
}

export default async function RunPage({ params }: { params: Promise<Params> }) {
  const { id } = await params;
  const result = await loadGame(id);

  // Closed runs route to the post-mortem instead of the dashboard. The
  // dashboard's "ended" footer still exists as a fallback for the brief
  // window between run termination and post-mortem aggregation completing,
  // but the canonical landing for a finished run is /post-mortem/{id}.
  if (result.ok && result.state.end_state !== null) {
    redirect(`/post-mortem/${id}`);
  }

  if (!result.ok) {
    if (result.status === 404) {
      redirect("/play");
    }
    return (
      <main className="mx-auto flex min-h-screen w-full max-w-2xl flex-col gap-6 px-6 py-12">
        <div className="pixel-border-strong space-y-2 p-4">
          <p className="font-heading text-sm uppercase">RUN UNREACHABLE</p>
          <p className="font-terminal text-xl">
            {result.status ? `HTTP ${result.status}` : "no response"} ·{" "}
            {result.reason}
          </p>
          <p className="text-sm text-px-text-dim">
            The colony is offline. Bring up the FastAPI service or return to{" "}
            <a className="underline" href="/play">
              the briefing room
            </a>
            .
          </p>
        </div>
      </main>
    );
  }

  return (
    <DashboardShell initialState={result.state} initialToken={result.accessToken} />
  );
}
