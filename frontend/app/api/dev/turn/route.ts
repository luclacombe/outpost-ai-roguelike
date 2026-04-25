import { NextResponse } from "next/server";

import { SSE_HEADERS, buildTurnStream } from "@/lib/dev/mockStream";

export const dynamic = "force-dynamic";

/**
 * Dev-only fixture for the dashboard. Mirrors the backend `/game/{id}/turn`
 * SSE shape. Two switches via query string:
 *
 *   ?fail_first=1   → 401 on the first hit, success on retry. Lets us
 *                     verify the hook's token-refresh + reconnect path.
 *   ?fail_always=1  → 401 every time. Triggers the error UX.
 */
const FAILED_ONCE = new Set<string>();

export async function POST(req: Request) {
  if (process.env.NODE_ENV === "production") {
    return NextResponse.json({ error: "not_found" }, { status: 404 });
  }
  const url = new URL(req.url);

  if (url.searchParams.get("fail_always") === "1") {
    return new NextResponse(null, { status: 401 });
  }

  const sessionKey =
    req.headers.get("x-dev-session") ?? "default";

  if (
    url.searchParams.get("fail_first") === "1" &&
    !FAILED_ONCE.has(sessionKey)
  ) {
    FAILED_ONCE.add(sessionKey);
    // 401 invites the hook to refresh + retry. The retry will see the
    // session key in FAILED_ONCE and return a real stream.
    return new NextResponse(null, { status: 401 });
  }

  return new Response(buildTurnStream(), { headers: SSE_HEADERS });
}
