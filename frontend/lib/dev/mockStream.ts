import { DEV_DEBATE_LINES, DEV_GAME_STATE } from "@/lib/dev/fixtures";

/**
 * Build a streaming SSE body that mirrors the backend's /turn flow:
 *   agent_spoke* (one per alive advisor)
 *   proposals_ready
 *   done [DONE]
 */
export function buildTurnStream(): ReadableStream<Uint8Array> {
  const enc = new TextEncoder();
  const frames: string[] = [];

  for (const line of DEV_DEBATE_LINES) {
    frames.push(
      `event: agent_spoke\ndata: ${JSON.stringify({
        agent_id: line.agent_id,
        text: line.text,
      })}\n\n`,
    );
  }

  frames.push(
    `event: proposals_ready\ndata: ${JSON.stringify({
      turn_number: DEV_GAME_STATE.current_turn,
      current_event: DEV_GAME_STATE.current_event,
      proposals: DEV_GAME_STATE.turn_proposals,
    })}\n\n`,
  );
  frames.push(`event: done\ndata: [DONE]\n\n`);

  return new ReadableStream<Uint8Array>({
    async start(controller) {
      for (const frame of frames) {
        controller.enqueue(enc.encode(frame));
        // Yield once per frame so the client renders progressively.
        await new Promise((r) => setTimeout(r, 80));
      }
      controller.close();
    },
  });
}

/**
 * Build a streaming SSE body for /decide:
 *   decision_applied
 *   autonomous_action* (one)
 *   narration
 *   turn_advanced
 *   proposals_ready (next turn) OR run_over
 *   done [DONE]
 */
export function buildDecideStream(
  chosenProposalId: string,
): ReadableStream<Uint8Array> {
  const enc = new TextEncoder();
  const nextTurn = DEV_GAME_STATE.current_turn + 1;
  const frames: string[] = [
    `event: decision_applied\ndata: ${JSON.stringify({
      chosen_proposal_id: chosenProposalId,
      turn_number: DEV_GAME_STATE.current_turn,
    })}\n\n`,
    `event: autonomous_action\ndata: ${JSON.stringify({
      agent_id: DEV_GAME_STATE.agents[3].id,
      tool_name: "remember_event",
      args: { text: "Marko backed me when Nadia objected." },
    })}\n\n`,
    `event: narration\ndata: ${JSON.stringify({
      text: "Yuri worked through the night under Marko's flashlight. By dawn, the bypass held. Nadia did not speak at breakfast.",
    })}\n\n`,
    `event: turn_advanced\ndata: ${JSON.stringify({
      turn_number: nextTurn,
      legitimacy: 64,
      player_health: DEV_GAME_STATE.player_health,
      cost_cents: 7,
      resources: DEV_GAME_STATE.resources.map((r, i) =>
        i === 0 ? { ...r, value: Math.max(0, r.value - 10) } : r,
      ),
      agents: DEV_GAME_STATE.agents,
    })}\n\n`,
    `event: proposals_ready\ndata: ${JSON.stringify({
      turn_number: nextTurn,
      current_event: {
        ...DEV_GAME_STATE.current_event!,
        id: "evt-followup",
        title: "Bypass holds — but the alternator is whining.",
        body: "Yuri's rig held but the alternator is making a sound. Either replace it now (costs fuel, costs Yuri's morning), or pray it lasts the day.",
        severity: 3,
      },
      proposals: DEV_GAME_STATE.turn_proposals.map((p) => ({
        ...p,
        id: `${p.id}-t2`,
      })),
    })}\n\n`,
    `event: done\ndata: [DONE]\n\n`,
  ];

  return new ReadableStream<Uint8Array>({
    async start(controller) {
      for (const frame of frames) {
        controller.enqueue(enc.encode(frame));
        await new Promise((r) => setTimeout(r, 80));
      }
      controller.close();
    },
  });
}

export const SSE_HEADERS = {
  "Content-Type": "text/event-stream",
  "Cache-Control": "no-cache",
  "X-Accel-Buffering": "no",
} as const;
