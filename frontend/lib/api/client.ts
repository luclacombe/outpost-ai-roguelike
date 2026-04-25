/**
 * FastAPI backend base URL. Same value at build and runtime since
 * NEXT_PUBLIC_* is inlined.
 */
export const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

export type Setting = "arctic" | "orbital" | "desert";

/**
 * GET /me — flat shape from `backend/app/auth/schemas.py::UserInfo`.
 *
 * `next_run_at === null` means the user is eligible to start a run right
 * now. Any non-null value is the ISO-8601 UTC string of the next 00:00 the
 * daily gate opens. Total-runs / last-run timestamps are not exposed at this
 * endpoint; the dashboard derives what it needs from /game/{id} after a run
 * starts.
 */
export type MeResponse = {
  id: string;
  email: string | null;
  display_name: string | null;
  next_run_at: string | null;
};

// ---------------------------------------------------------------------------
// Game session shapes — mirrors backend/app/game/schemas_io.py
// ---------------------------------------------------------------------------

export type Quadrant = "ACCOUNTANT" | "OPERATOR" | "CONSCIENCE" | "CRUSADER";
export type ProposalFlavor = "DOMINANT" | "MORAL" | "WILDCARD";
export type EventOrigin = "NATURAL" | "SEED_FIRED" | "FORCED_ESCALATION";
export type EndState = "ENDURED" | "COLLAPSED" | "OUSTED" | "KILLED";

export type AgentPublic = {
  id: string;
  name: string;
  role: string;
  archetype_quadrant: Quadrant;
  portrait_seed: string;
  verbal_tic: string;
  health: number;
  morale: number;
  grievance: number;
  is_alive: boolean;
};

export type ResourcePublic = {
  id: string;
  name: string;
  icon: string;
  value: number;
  max: number;
  critical_threshold: number;
};

export type ProposalPublic = {
  id: string;
  proposed_by_agent_id: string | null;
  flavor: ProposalFlavor;
  name: string;
  description: string;
  effects_preview: string[];
};

export type EventPublic = {
  id: string;
  category: string;
  title: string;
  body: string;
  severity: number;
  origin: EventOrigin;
  origin_seed_id: string | null;
};

export type GameSessionResponse = {
  game_id: string;
  setting_id: string;
  setting_name: string;
  max_turns: number;
  current_turn: number;
  legitimacy: number;
  player_health: number;
  agents: AgentPublic[];
  resources: ResourcePublic[];
  started_at: string;
  current_event: EventPublic | null;
  turn_proposals: ProposalPublic[];
  /** Null while the run is live; populated once the run terminates. */
  end_state: EndState | null;
};

// ---------------------------------------------------------------------------
// SSE event payloads — mirrors backend/app/agent/schemas_io.py docstring
// ---------------------------------------------------------------------------

export type ProposalsReadyPayload = {
  turn_number: number;
  current_event: EventPublic | null;
  proposals: ProposalPublic[];
};

export type DecisionAppliedPayload = {
  chosen_proposal_id: string;
  turn_number: number;
};

export type AgentSpokePayload = {
  agent_id: string;
  text: string;
};

export type AutonomousActionPayload = {
  agent_id: string | null;
  tool_name: string;
  args: Record<string, unknown>;
};

export type DeathPayload = {
  agent_id: string;
  agent_name: string;
  turn: number;
};

export type NarrationPayload = {
  text: string;
};

export type TurnAdvancedPayload = {
  turn_number: number;
  legitimacy: number;
  player_health: number;
  cost_cents: number;
};

export type RunOverPayload = {
  end_state: EndState | null;
  turn_number: number;
  cost_cents: number;
};

export type ErrorPayload = {
  code: string;
  message: string;
};

export type TurnStreamEvent =
  | { type: "proposals_ready"; data: ProposalsReadyPayload }
  | { type: "decision_applied"; data: DecisionAppliedPayload }
  | { type: "agent_spoke"; data: AgentSpokePayload }
  | { type: "autonomous_action"; data: AutonomousActionPayload }
  | { type: "death"; data: DeathPayload }
  | { type: "narration"; data: NarrationPayload }
  | { type: "turn_advanced"; data: TurnAdvancedPayload }
  | { type: "run_over"; data: RunOverPayload }
  | { type: "error"; data: ErrorPayload }
  | { type: "done" };

// ---------------------------------------------------------------------------
// Setting palette helpers
// ---------------------------------------------------------------------------

export function settingFromId(setting_id: string): Setting {
  if (setting_id.startsWith("arctic")) return "arctic";
  if (setting_id.startsWith("orbital")) return "orbital";
  if (setting_id.startsWith("desert")) return "desert";
  return "arctic";
}

// ---------------------------------------------------------------------------
// API error envelope shape — see root CLAUDE.md
// ---------------------------------------------------------------------------

export type ApiErrorEnvelope = {
  error: {
    code: string;
    message: string;
    detail?: Record<string, unknown>;
  };
};

export function isApiErrorEnvelope(x: unknown): x is ApiErrorEnvelope {
  return (
    typeof x === "object" &&
    x !== null &&
    "error" in x &&
    typeof (x as { error: unknown }).error === "object" &&
    (x as { error: { code?: unknown } }).error !== null
  );
}
