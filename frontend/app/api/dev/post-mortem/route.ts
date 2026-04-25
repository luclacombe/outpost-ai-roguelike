import { NextResponse } from "next/server";

import postMortemFixture from "@/lib/dev/postMortemFixture.json";

/**
 * Dev mock for `GET /game/{id}/post-mortem`.
 *
 * Returns a frozen JSON snapshot taken from the real Phase 7 exit run
 * (id `7451e10a-092a-549d-907b-25b80282f4fa`) so the post-mortem UI can
 * iterate offline without re-playing turns or hitting the backend.
 *
 * Generated via `uv run python -c "from app.game.post_mortem import
 * build_post_mortem; ..." → JSON dump`. Re-run that snippet whenever the
 * `PostMortem` schema gains fields.
 *
 * Production-build gated to 404 — the live route is the FastAPI endpoint.
 */
export const dynamic = "force-dynamic";

export async function GET() {
  if (process.env.NODE_ENV === "production") {
    return NextResponse.json({ error: "not_found" }, { status: 404 });
  }
  return NextResponse.json(postMortemFixture);
}
