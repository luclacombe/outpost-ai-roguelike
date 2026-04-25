import { NextResponse } from "next/server";

import { SSE_HEADERS, buildDecideStream } from "@/lib/dev/mockStream";

export const dynamic = "force-dynamic";

export async function POST(req: Request) {
  if (process.env.NODE_ENV === "production") {
    return NextResponse.json({ error: "not_found" }, { status: 404 });
  }
  const body = (await req.json().catch(() => ({}))) as {
    chosen_proposal_id?: string;
  };
  const chosen = body.chosen_proposal_id ?? "unknown";
  return new Response(buildDecideStream(chosen), { headers: SSE_HEADERS });
}
