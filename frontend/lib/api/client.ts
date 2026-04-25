/**
 * FastAPI backend base URL. Same value at build and runtime since
 * NEXT_PUBLIC_* is inlined.
 */
export const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

export type MeResponse = {
  user: {
    id: string;
    email: string | null;
    display_name: string | null;
  };
  daily_gate: {
    can_start_run: boolean;
    /** ISO 8601 string. Set when `can_start_run` is false. */
    next_run_at: string | null;
    last_run_started_at: string | null;
    total_runs: number;
  };
};

export type StartRunResponse = {
  game_id: string;
  setting: { id: string; name: string };
};
