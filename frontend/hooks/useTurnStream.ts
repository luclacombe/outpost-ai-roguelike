"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import {
  API_BASE_URL,
  type EndState,
  type EventPublic,
  type ProposalPublic,
  type TurnStreamEvent,
} from "@/lib/api/client";
import { iterateSse } from "@/lib/api/sse";
import { type DebateLine } from "@/components/dashboard/EventDebatePanel";
import { type EventLogEntry } from "@/components/dashboard/EventLog";
import { createClient } from "@/lib/supabase/client";

/**
 * High-level dashboard phase for the player. Maps to ChoosePanel's phases.
 */
export type TurnPhase =
  | "idle"
  | "running"
  | "awaiting_choice"
  | "awaiting_continue"
  | "submitting"
  | "advancing"
  | "ended"
  | "error";

export type TurnStreamState = {
  phase: TurnPhase;
  turnNumber: number;
  currentEvent: EventPublic | null;
  proposals: ProposalPublic[];
  debateLines: DebateLine[];
  narration: string | null;
  legitimacy: number | null;
  playerHealth: number | null;
  costCents: number | null;
  endState: EndState | null;
  /** Most recent error surfaced to the UI; cleared by a successful action. */
  error: { code: string; message: string } | null;
  eventLog: EventLogEntry[];
  // Narration "Continue" gate — when /decide's stream auto-advances past
  // narration into the next turn's `proposals_ready`, we hold the new turn
  // data in these slots until the player clicks Continue. Without the gate,
  // narration disappears the moment the next event renders.
  pendingEvent: EventPublic | null;
  pendingProposals: ProposalPublic[];
  pendingTurnNumber: number | null;
  pendingLogEntry: EventLogEntry | null;
};

const INITIAL_STATE: TurnStreamState = {
  phase: "idle",
  turnNumber: 0,
  currentEvent: null,
  proposals: [],
  debateLines: [],
  narration: null,
  legitimacy: null,
  playerHealth: null,
  costCents: null,
  endState: null,
  error: null,
  eventLog: [],
  pendingEvent: null,
  pendingProposals: [],
  pendingTurnNumber: null,
  pendingLogEntry: null,
};

type SeedOptions = {
  turnNumber: number;
  legitimacy: number | null;
  playerHealth: number | null;
  currentEvent: EventPublic | null;
  proposals: ProposalPublic[];
  /** Already-set end state when reloading a closed run; null otherwise. */
  endState?: EndState | null;
};

export type AuthGetter = () => Promise<string | null>;

export type UseTurnStreamOptions = {
  /** Override the URLs hit for /turn and /decide. Defaults to the live backend. */
  beginTurnUrl?: string;
  decideUrl?: string;
  /**
   * Async getter for a fresh access token. Called BEFORE every fetch.
   * Default: pulls `supabase.auth.getSession().access_token`.
   *
   * Override in tests or the dev preview. Returning null short-circuits to
   * the "no_session" error.
   */
  getAuthToken?: AuthGetter;
  /**
   * Async refresher used after a 401. Default: `supabase.auth.refreshSession()`.
   * The retry that follows then calls `getAuthToken()` again to pull the
   * freshly minted token.
   */
  refreshSession?: () => Promise<void>;
};

/**
 * Single-game SSE consumer hook.
 *
 * Lifecycle:
 *   1. mount with `seed` (loaded server-side via /game/{id}).
 *   2. caller invokes beginTurn() → POST /game/{id}/turn, parses SSE,
 *      transitions phase: idle → running → awaiting_choice (or ended).
 *   3. caller invokes submitChoice(id) → POST /game/{id}/decide, parses SSE,
 *      transitions phase: submitting → advancing → awaiting_choice (or ended).
 *
 * Token discipline (per ARCHITECTURE §8.7 & PLAN 7.6):
 *   - BEFORE every fetch we call `getAuthToken()` to pull the latest token.
 *     Never close-over a stale token from mount time.
 *   - On a 401 response, we abort, refresh the session, and re-POST /turn.
 *     /turn is idempotent while paused — the backend short-circuits to
 *     re-emit `proposals_ready`, so the player sees the same proposals
 *     without losing the run. /decide cannot be safely retried (graph
 *     already past pause), so on 401 mid-/decide we surface an error and
 *     leave reconciliation to the caller (refresh state via /game/{id}).
 */
export function useTurnStream(
  gameId: string,
  seed: SeedOptions,
  opts: UseTurnStreamOptions = {},
): TurnStreamState & {
  beginTurn: () => Promise<void>;
  submitChoice: (proposalId: string) => Promise<void>;
  continueAfterNarration: () => void;
} {
  const [state, setState] = useState<TurnStreamState>(() => {
    // Mounted phase precedence: ended > awaiting_choice > idle.
    // A closed run must NOT offer "Begin turn" — that's how navigating
    // back to /run/<id> after a run terminates was wrongly serving the
    // pre-run affordance during Phase 7's first live test.
    const initialPhase: TurnPhase = seed.endState
      ? "ended"
      : seed.proposals.length > 0
        ? "awaiting_choice"
        : "idle";
    return {
      ...INITIAL_STATE,
      turnNumber: seed.turnNumber,
      legitimacy: seed.legitimacy,
      playerHealth: seed.playerHealth,
      currentEvent: seed.currentEvent,
      proposals: seed.proposals,
      endState: seed.endState ?? null,
      phase: initialPhase,
    };
  });

  const abortRef = useRef<AbortController | null>(null);
  const supabaseRef = useRef<ReturnType<typeof createClient> | null>(null);
  // Lazily construct the Supabase client only when no override is provided —
  // the dev preview supplies its own getter and never needs a real client.
  if (
    supabaseRef.current === null &&
    !opts.getAuthToken &&
    !opts.refreshSession
  ) {
    supabaseRef.current = createClient();
  }

  const beginTurnUrl =
    opts.beginTurnUrl ?? `${API_BASE_URL}/game/${gameId}/turn`;
  const decideUrl = opts.decideUrl ?? `${API_BASE_URL}/game/${gameId}/decide`;

  // Abort any in-flight stream when the component unmounts.
  useEffect(() => {
    return () => {
      abortRef.current?.abort();
    };
  }, []);

  const fetchToken = useCallback(async (): Promise<string | null> => {
    if (opts.getAuthToken) return opts.getAuthToken();
    const supabase = supabaseRef.current!;
    const {
      data: { session },
    } = await supabase.auth.getSession();
    return session?.access_token ?? null;
  }, [opts]);

  const refreshSession = useCallback(async (): Promise<void> => {
    if (opts.refreshSession) {
      await opts.refreshSession();
      return;
    }
    if (supabaseRef.current) {
      await supabaseRef.current.auth.refreshSession();
    }
  }, [opts]);

  const consumeStream = useCallback(
    async (
      url: string,
      method: "POST",
      body: unknown,
      onEvent: (e: TurnStreamEvent) => void,
      attempt = 0,
    ): Promise<void> => {
      const token = await fetchToken();
      // A null token from the default supabase getter means there's no
      // session — bail out. Custom getters that return null are treated
      // as "skip the bearer header", since they may legitimately want
      // unauthenticated requests (the dev preview).
      if (!token && !opts.getAuthToken) {
        setState((s) => ({
          ...s,
          phase: "error",
          error: { code: "no_session", message: "Sign in again." },
        }));
        return;
      }

      const ctrl = new AbortController();
      abortRef.current?.abort();
      abortRef.current = ctrl;

      const headers: Record<string, string> = {
        "Content-Type": "application/json",
      };
      if (token) headers.Authorization = `Bearer ${token}`;

      let res: Response;
      try {
        res = await fetch(url, {
          method,
          headers,
          body: body ? JSON.stringify(body) : undefined,
          signal: ctrl.signal,
        });
      } catch (e) {
        if (ctrl.signal.aborted) return;
        setState((s) => ({
          ...s,
          phase: "error",
          error: {
            code: "network_error",
            message: e instanceof Error ? e.message : "Network error.",
          },
        }));
        return;
      }

      if (res.status === 401 && attempt === 0) {
        // Stale access token — refresh + retry once. /turn is idempotent
        // while paused, so the user sees the same paused proposals.
        await refreshSession();
        return consumeStream(url, method, body, onEvent, attempt + 1);
      }

      if (!res.ok) {
        const errBody = (await res.json().catch(() => ({}))) as {
          error?: { code?: string; message?: string };
        };
        const code: string =
          errBody?.error?.code ?? `http_${res.status}`;
        const message: string =
          errBody?.error?.message ?? `Request failed (${res.status}).`;
        setState((s) => ({ ...s, phase: "error", error: { code, message } }));
        return;
      }

      try {
        for await (const evt of iterateSse(res, ctrl.signal)) {
          onEvent(evt);
          if (evt.type === "done") break;
        }
      } catch (e) {
        if (ctrl.signal.aborted) return;
        if (attempt === 0) {
          await refreshSession();
          return consumeStream(url, method, body, onEvent, attempt + 1);
        }
        setState((s) => ({
          ...s,
          phase: "error",
          error: {
            code: "stream_error",
            message: e instanceof Error ? e.message : "Stream interrupted.",
          },
        }));
      }
    },
    [fetchToken, opts.getAuthToken, refreshSession],
  );

  const handleEvent = useCallback((evt: TurnStreamEvent) => {
    setState((s) => applyEvent(s, evt));
  }, []);

  const beginTurn = useCallback(async () => {
    setState((s) => ({
      ...s,
      phase: "running",
      debateLines: [],
      narration: null,
      error: null,
    }));
    await consumeStream(beginTurnUrl, "POST", null, handleEvent);
  }, [beginTurnUrl, consumeStream, handleEvent]);

  const submitChoice = useCallback(
    async (proposalId: string) => {
      setState((s) => ({ ...s, phase: "submitting", error: null }));
      await consumeStream(
        decideUrl,
        "POST",
        { chosen_proposal_id: proposalId },
        handleEvent,
      );
    },
    [decideUrl, consumeStream, handleEvent],
  );

  const continueAfterNarration = useCallback(() => {
    setState((s) => promotePendingTurn(s));
  }, []);

  return { ...state, beginTurn, submitChoice, continueAfterNarration };
}

function promotePendingTurn(s: TurnStreamState): TurnStreamState {
  if (s.phase !== "awaiting_continue" || s.pendingEvent === null) return s;
  return {
    ...s,
    phase: "awaiting_choice",
    currentEvent: s.pendingEvent,
    proposals: s.pendingProposals,
    turnNumber: s.pendingTurnNumber ?? s.turnNumber,
    narration: null,
    debateLines: [],
    eventLog: s.pendingLogEntry
      ? [...s.eventLog, s.pendingLogEntry]
      : s.eventLog,
    pendingEvent: null,
    pendingProposals: [],
    pendingTurnNumber: null,
    pendingLogEntry: null,
  };
}

// ---------------------------------------------------------------------------
// Pure reducer — exported for testability
// ---------------------------------------------------------------------------

export function applyEvent(
  s: TurnStreamState,
  evt: TurnStreamEvent,
): TurnStreamState {
  switch (evt.type) {
    case "agent_spoke":
      return {
        ...s,
        phase: s.phase === "submitting" ? "advancing" : "running",
        debateLines: appendDebateLine(s.debateLines, evt.data),
      };
    case "decision_applied":
      return { ...s, phase: "advancing" };
    case "autonomous_action":
      return s;
    case "death":
      return {
        ...s,
        eventLog: [
          ...s.eventLog,
          {
            id: `death-${evt.data.agent_id}-${evt.data.turn}`,
            turn: evt.data.turn,
            text: `${evt.data.agent_name} died.`,
          },
        ],
      };
    case "narration":
      return { ...s, narration: evt.data.text };
    case "turn_advanced":
      return {
        ...s,
        turnNumber: evt.data.turn_number,
        legitimacy: evt.data.legitimacy,
        playerHealth: evt.data.player_health,
        costCents: evt.data.cost_cents,
      };
    case "proposals_ready": {
      const event = evt.data.current_event;
      // The graph emits proposals_ready twice for the same event when a
      // disconnected client reconnects (backend short-circuits on a re-POST
      // of /turn while paused). Clear debate + narration only when we're
      // genuinely on a new turn — keep them visible while the player is
      // weighing the choice.
      const isNewTurn =
        event !== null &&
        s.currentEvent !== null &&
        s.currentEvent.id !== event.id;
      const logEntry =
        event && !s.eventLog.some((e) => e.id === event.id)
          ? {
              id: event.id,
              turn: evt.data.turn_number,
              text: event.title.toLowerCase(),
            }
          : null;
      // Continue gate: when narration is showing AND we're advancing to a
      // genuinely new event, hold the new turn data in pending slots until
      // the player clicks Continue. Otherwise the narration disappears the
      // moment the next event renders.
      if (isNewTurn && s.narration) {
        return {
          ...s,
          phase: "awaiting_continue",
          pendingEvent: event,
          pendingProposals: evt.data.proposals,
          pendingTurnNumber: evt.data.turn_number,
          pendingLogEntry: logEntry,
        };
      }
      return {
        ...s,
        phase: "awaiting_choice",
        turnNumber: evt.data.turn_number,
        currentEvent: event,
        proposals: evt.data.proposals,
        debateLines: isNewTurn ? [] : s.debateLines,
        narration: isNewTurn ? null : s.narration,
        eventLog: logEntry ? [...s.eventLog, logEntry] : s.eventLog,
      };
    }
    case "run_over":
      return {
        ...s,
        phase: "ended",
        endState: evt.data.end_state,
        turnNumber: evt.data.turn_number,
        costCents: evt.data.cost_cents,
      };
    case "error":
      return {
        ...s,
        phase: "error",
        error: { code: evt.data.code, message: evt.data.message },
      };
    case "done":
      return s;
  }
}

function appendDebateLine(
  lines: DebateLine[],
  d: { agent_id: string; text: string },
): DebateLine[] {
  return [...lines, { agent_id: d.agent_id, text: d.text, partial: false }];
}
