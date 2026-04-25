import {
  type AgentSpokePayload,
  type AutonomousActionPayload,
  type DeathPayload,
  type DecisionAppliedPayload,
  type ErrorPayload,
  type NarrationPayload,
  type ProposalsReadyPayload,
  type RunOverPayload,
  type TurnAdvancedPayload,
  type TurnStreamEvent,
} from "@/lib/api/client";

/**
 * Parse a single SSE frame `event: X\ndata: Y` into a typed `TurnStreamEvent`.
 *
 * The backend uses a literal `[DONE]` sentinel for the terminal frame's data,
 * not JSON — see `backend/app/agent/service.py::_DONE_SENTINEL`.
 *
 * Returns `null` for malformed frames; the caller logs and continues so a
 * single bad frame doesn't kill the stream.
 */
export function parseSseFrame(frame: string): TurnStreamEvent | null {
  let event: string | null = null;
  let data: string | null = null;

  for (const line of frame.split("\n")) {
    if (line.startsWith("event:")) event = line.slice(6).trim();
    else if (line.startsWith("data:")) {
      // Servers MAY split a single data field across multiple `data:` lines;
      // the SSE spec joins them with newlines. Concatenate defensively.
      const piece = line.slice(5).trim();
      data = data === null ? piece : `${data}\n${piece}`;
    }
  }

  if (!event) return null;

  if (event === "done") return { type: "done" };
  if (data === null) return null;

  try {
    switch (event) {
      case "proposals_ready":
        return {
          type: "proposals_ready",
          data: JSON.parse(data) as ProposalsReadyPayload,
        };
      case "decision_applied":
        return {
          type: "decision_applied",
          data: JSON.parse(data) as DecisionAppliedPayload,
        };
      case "agent_spoke":
        return {
          type: "agent_spoke",
          data: JSON.parse(data) as AgentSpokePayload,
        };
      case "autonomous_action":
        return {
          type: "autonomous_action",
          data: JSON.parse(data) as AutonomousActionPayload,
        };
      case "death":
        return {
          type: "death",
          data: JSON.parse(data) as DeathPayload,
        };
      case "narration":
        return {
          type: "narration",
          data: JSON.parse(data) as NarrationPayload,
        };
      case "turn_advanced":
        return {
          type: "turn_advanced",
          data: JSON.parse(data) as TurnAdvancedPayload,
        };
      case "run_over":
        return {
          type: "run_over",
          data: JSON.parse(data) as RunOverPayload,
        };
      case "error":
        return { type: "error", data: JSON.parse(data) as ErrorPayload };
      default:
        return null;
    }
  } catch {
    return null;
  }
}

/**
 * Async iterator over typed SSE events from a fetch Response. Yields each
 * `TurnStreamEvent` as it arrives.
 *
 * Uses ReadableStream + TextDecoder to keep us off `EventSource` (no headers
 * support, can't send a Bearer token).
 *
 * The caller is responsible for:
 *   - awaiting the iterator to completion (or break-ing out + abort()-ing)
 *   - handling 401 by refreshing the token and re-posting upstream
 */
export async function* iterateSse(
  response: Response,
  signal: AbortSignal,
): AsyncGenerator<TurnStreamEvent> {
  if (!response.body) {
    throw new Error("SSE response has no body");
  }

  const reader = response.body
    .pipeThrough(new TextDecoderStream(), { signal })
    .getReader();

  let buffer = "";
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += value;
      // Frames are separated by a blank line. Browsers may deliver "\r\n\r\n";
      // normalize before splitting.
      buffer = buffer.replace(/\r\n/g, "\n");
      let sepIndex = buffer.indexOf("\n\n");
      while (sepIndex !== -1) {
        const frame = buffer.slice(0, sepIndex);
        buffer = buffer.slice(sepIndex + 2);
        const parsed = parseSseFrame(frame);
        if (parsed) yield parsed;
        sepIndex = buffer.indexOf("\n\n");
      }
    }
  } finally {
    try {
      await reader.cancel();
    } catch {
      // ignore — already aborted
    }
  }
}
