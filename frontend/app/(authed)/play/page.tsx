import { BeginRunButton } from "@/components/play/BeginRunButton";
import { Countdown } from "@/components/play/Countdown";
import { API_BASE_URL, type MeResponse } from "@/lib/api/client";
import { createClient } from "@/lib/supabase/server";

async function fetchMe(): Promise<
  | { ok: true; me: MeResponse }
  | { ok: false; status: number; reason: string }
> {
  const supabase = await createClient();
  const {
    data: { session },
  } = await supabase.auth.getSession();
  if (!session) return { ok: false, status: 401, reason: "no_session" };

  try {
    const res = await fetch(`${API_BASE_URL}/me`, {
      headers: { Authorization: `Bearer ${session.access_token}` },
      cache: "no-store",
    });
    if (!res.ok) {
      return { ok: false, status: res.status, reason: `http_${res.status}` };
    }
    const me = (await res.json()) as MeResponse;
    return { ok: true, me };
  } catch (e) {
    return {
      ok: false,
      status: 0,
      reason: e instanceof Error ? e.message : "network_error",
    };
  }
}

export default async function PlayPage() {
  const result = await fetchMe();

  return (
    <main className="mx-auto flex min-h-screen w-full max-w-2xl flex-col gap-10 px-6 py-12">
      <header className="space-y-2">
        <p className="font-display text-[10px] uppercase tracking-widest text-px-text-dim">
          OUTPOST · PRE-RUN
        </p>
        <h1 className="font-display text-2xl leading-tight md:text-3xl">
          BRIEFING
        </h1>
      </header>

      {result.ok ? (
        <ReadyOrLocked me={result.me} />
      ) : (
        <BackendUnreachable
          status={result.status}
          reason={result.reason}
        />
      )}

      <footer className="font-terminal mt-auto text-base text-px-text-dim">
        the door doesn&apos;t open twice today.
      </footer>
    </main>
  );
}

function ReadyOrLocked({ me }: { me: MeResponse }) {
  if (me.daily_gate.can_start_run) {
    return (
      <section className="space-y-6">
        <p className="font-terminal text-2xl leading-snug">
          Today&apos;s post is open. Eight to twelve turns. No restart.
        </p>
        <p className="text-base text-px-text">
          The cast and setting are generated when you begin. Choose carefully.
        </p>
        <BeginRunButton />
        <p className="font-heading text-xs uppercase text-px-text-dim">
          Total runs: {me.daily_gate.total_runs}
        </p>
      </section>
    );
  }

  const next = me.daily_gate.next_run_at;
  return (
    <section className="space-y-6">
      <p className="font-terminal text-2xl leading-snug">
        You ran today. Come back tomorrow.
      </p>
      <div className="pixel-border space-y-2 p-4">
        <p className="font-heading text-xs uppercase text-px-text-dim">
          NEXT RUN OPENS IN
        </p>
        {next ? (
          <Countdown nextRunAt={next} />
        ) : (
          <p className="font-display text-2xl">--:--:--</p>
        )}
        {next ? (
          <p className="text-xs text-px-text-dim">
            {new Date(next).toUTCString()}
          </p>
        ) : null}
      </div>
      <p className="font-heading text-xs uppercase text-px-text-dim">
        Total runs: {me.daily_gate.total_runs}
      </p>
    </section>
  );
}

function BackendUnreachable({
  status,
  reason,
}: {
  status: number;
  reason: string;
}) {
  return (
    <section className="space-y-4">
      <div className="pixel-border-strong space-y-2 p-4">
        <p className="font-heading text-sm uppercase">
          BACKEND UNREACHABLE
        </p>
        <p className="font-terminal text-xl">
          {status ? `HTTP ${status}` : "no response"} · {reason}
        </p>
        <p className="text-sm text-px-text-dim">
          The colony radio isn&apos;t answering. Bring up the FastAPI service
          (<code>cd backend && docker compose up</code>) or set{" "}
          <code>NEXT_PUBLIC_API_BASE_URL</code>.
        </p>
      </div>
    </section>
  );
}
