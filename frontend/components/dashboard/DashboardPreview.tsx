"use client";

import { useEffect, useId, useRef } from "react";

import { CrtOverlay } from "@/components/CrtOverlay";
import { ChoosePanel } from "@/components/dashboard/ChoosePanel";
import { ColonyStatus } from "@/components/dashboard/ColonyStatus";
import { DashboardHeader } from "@/components/dashboard/DashboardHeader";
import { EventDebatePanel } from "@/components/dashboard/EventDebatePanel";
import { useTurnStream } from "@/hooks/useTurnStream";
import { settingFromId } from "@/lib/api/client";
import { DEV_GAME_STATE } from "@/lib/dev/fixtures";

/**
 * Standalone dashboard preview wired to the real `useTurnStream` hook
 * pointed at the in-process mock endpoints under /api/dev/.
 *
 * Two flags drive token-refresh testing:
 *   ?fail_first=1   → first /turn POST returns 401; the hook should refresh
 *                     and retry, completing the stream cleanly.
 *   ?fail_always=1  → every /turn POST returns 401; verifies error UX.
 *
 * No backend required. Exists for visual iteration only — gated by
 * `process.env.NODE_ENV === "production"` upstream in `app/dev/dashboard/page.tsx`.
 */
export function DashboardPreview() {
  const setting = settingFromId(DEV_GAME_STATE.setting_id);

  // Stable per-mount id so the mock route's "fail first" toggle distinguishes
  // sessions across page refreshes during a Playwright test run.
  const sessionId = useId();
  const sessionIdRef = useRef<string>(sessionId);
  const search = typeof window !== "undefined" ? window.location.search : "";

  const stream = useTurnStream(
    DEV_GAME_STATE.game_id,
    {
      turnNumber: DEV_GAME_STATE.current_turn,
      legitimacy: DEV_GAME_STATE.legitimacy,
      playerHealth: DEV_GAME_STATE.player_health,
      currentEvent: null,
      proposals: [],
    },
    {
      beginTurnUrl: `/api/dev/turn${search}`,
      decideUrl: `/api/dev/decide${search}`,
      // The mock endpoint ignores the value — but supplying a non-null
      // token here is what tells the hook to skip the supabase fallback.
      getAuthToken: async () => "dev-mock",
      // No-op refresher in dev. The mock endpoint's `?fail_first=1` switch
      // tracks attempt count via the `x-dev-session` header (see below).
      refreshSession: async () => {},
    },
  );

  // Fire the mock with a session-stable header by patching window.fetch.
  // Avoids leaking the dev session id into the production hook signature.
  // Runs once per mount; the cleanup restores the original to keep this
  // contained to the dev preview surface only.
  useEffect(() => {
    if (typeof window === "undefined") return;
    const originalFetch = window.fetch.bind(window);
    window.fetch = (input, init) => {
      const url =
        typeof input === "string"
          ? input
          : input instanceof URL
            ? input.toString()
            : input.url;
      if (url.includes("/api/dev/")) {
        const headers = new Headers(init?.headers);
        headers.set("x-dev-session", sessionIdRef.current);
        return originalFetch(input, { ...init, headers });
      }
      return originalFetch(input, init);
    };
    return () => {
      window.fetch = originalFetch;
    };
  }, []);

  return (
    <div data-setting={setting} className="flex min-h-svh w-full flex-col lg:h-svh">
      <CrtOverlay />
      <div className="dashboard-grid flex-1 bg-px-bg text-px-text">
        <div className="dashboard-area-header">
          <DashboardHeader
            settingName={DEV_GAME_STATE.setting_name}
            turnNumber={stream.turnNumber}
            maxTurns={DEV_GAME_STATE.max_turns}
            legitimacy={stream.legitimacy ?? DEV_GAME_STATE.legitimacy}
            costCents={stream.costCents}
          />
        </div>
        <aside className="dashboard-area-status">
          <ColonyStatus
            resources={DEV_GAME_STATE.resources}
            agents={DEV_GAME_STATE.agents}
            eventLog={stream.eventLog}
            setting={setting}
          />
        </aside>
        <section className="dashboard-area-event lg:overflow-y-auto">
          <EventDebatePanel
            currentEvent={stream.currentEvent}
            agents={DEV_GAME_STATE.agents}
            setting={setting}
            debateLines={stream.debateLines}
            narration={stream.narration}
          />
          {stream.error ? (
            <div
              role="alert"
              className="m-6 border border-px-danger bg-px-bg p-3 font-terminal text-base text-px-danger"
              data-testid="stream-error"
            >
              {stream.error.code} · {stream.error.message}
            </div>
          ) : null}
        </section>
        <section className="dashboard-area-choose">
          <ChoosePanel
            proposals={stream.proposals}
            agents={DEV_GAME_STATE.agents}
            setting={setting}
            phase={stream.phase === "error" ? "idle" : stream.phase}
            legitimacy={stream.legitimacy ?? DEV_GAME_STATE.legitimacy}
            onChoose={stream.submitChoice}
            onBeginTurn={stream.beginTurn}
          />
        </section>
      </div>
    </div>
  );
}

