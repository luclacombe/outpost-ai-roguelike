"use client";

import { ChoosePanel, type ChoosePhase } from "@/components/dashboard/ChoosePanel";
import { ColonyStatus } from "@/components/dashboard/ColonyStatus";
import { DashboardHeader } from "@/components/dashboard/DashboardHeader";
import { EventDebatePanel } from "@/components/dashboard/EventDebatePanel";
import { useTurnStream } from "@/hooks/useTurnStream";
import {
  type GameSessionResponse,
  settingFromId,
} from "@/lib/api/client";

type Props = {
  initialState: GameSessionResponse;
  /** Server-issued access token, for use only as a fallback if cookies are stale. */
  initialToken: string;
};

/**
 * Dashboard layout — the live run surface. Loads initial state from the
 * server, then streams subsequent turns via SSE.
 *
 * Grid (per ARCHITECTURE §8.3):
 *   header  header
 *   status  event
 *   status  choose
 *
 * Mobile collapse (≤768): stack as header → event → choose → status.
 * Status sits below choose on mobile so the action stays above the fold.
 */
export function DashboardShell({ initialState, initialToken }: Props) {
  // Reference initialToken so the prop survives strict prop-drilling lint
  // checks. The supabase client provides fresh tokens via cookies; we keep
  // the initial server-issued token only as a defensive backup.
  void initialToken;

  const setting = settingFromId(initialState.setting_id);

  const stream = useTurnStream(initialState.game_id, {
    turnNumber: initialState.current_turn,
    legitimacy: initialState.legitimacy,
    playerHealth: initialState.player_health,
    currentEvent: initialState.current_event,
    proposals: initialState.turn_proposals,
    endState: initialState.end_state,
  });

  // Resources rerender from the server's initial snapshot. SSE doesn't carry
  // resource deltas yet — they update on the next /game/{id} fetch (e.g. on
  // page refresh after a turn). Acceptable for MVP; revisit if needed.
  const resources = initialState.resources;
  const agents = initialState.agents;

  const choosePhase: ChoosePhase =
    stream.phase === "error" ? "idle" : stream.phase;

  return (
    <div
      data-setting={setting}
      className="dashboard-grid min-h-svh w-full bg-px-bg text-px-text lg:h-svh"
    >
      <div className="dashboard-area-header">
        <DashboardHeader
          settingName={initialState.setting_name}
          turnNumber={stream.turnNumber}
          maxTurns={initialState.max_turns}
          legitimacy={stream.legitimacy ?? initialState.legitimacy}
          costCents={stream.costCents}
        />
      </div>

      <aside className="dashboard-area-status">
        <ColonyStatus
          resources={resources}
          agents={agents}
          eventLog={stream.eventLog}
          setting={setting}
        />
      </aside>

      <section className="dashboard-area-event lg:overflow-y-auto">
        <EventDebatePanel
          currentEvent={stream.currentEvent}
          agents={agents}
          setting={setting}
          debateLines={stream.debateLines}
          narration={stream.narration}
        />
        {stream.error ? (
          <div
            role="alert"
            data-testid="stream-error"
            className="m-6 border border-px-danger bg-px-bg p-3 font-terminal text-base text-px-danger"
          >
            <span className="font-heading text-xs uppercase tracking-wider">
              {stream.error.code}
            </span>
            <span className="ml-2">{stream.error.message}</span>
          </div>
        ) : null}
      </section>

      <section className="dashboard-area-choose">
        <ChoosePanel
          proposals={stream.proposals}
          agents={agents}
          setting={setting}
          phase={choosePhase}
          legitimacy={stream.legitimacy ?? initialState.legitimacy}
          onChoose={stream.submitChoice}
          onBeginTurn={stream.beginTurn}
        />
      </section>
    </div>
  );
}
