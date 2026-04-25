import { redirect } from "next/navigation";

import { CrtOverlay } from "@/components/CrtOverlay";
import { PostMortemView } from "@/components/postmortem/PostMortemView";
import {
  API_BASE_URL,
  type PostMortem,
  isApiErrorEnvelope,
} from "@/lib/api/client";
import { createClient } from "@/lib/supabase/server";

type Params = { id: string };

async function loadPostMortem(
  id: string,
): Promise<
  | { ok: true; data: PostMortem }
  | { ok: false; status: number; reason: string }
> {
  const supabase = await createClient();
  const {
    data: { session },
  } = await supabase.auth.getSession();
  if (!session) return { ok: false, status: 401, reason: "no_session" };

  try {
    const res = await fetch(`${API_BASE_URL}/game/${id}/post-mortem`, {
      headers: { Authorization: `Bearer ${session.access_token}` },
      cache: "no-store",
    });
    if (!res.ok) {
      const body = (await res.json().catch(() => ({}))) as unknown;
      const reason = isApiErrorEnvelope(body)
        ? body.error.code
        : `http_${res.status}`;
      return { ok: false, status: res.status, reason };
    }
    const data = (await res.json()) as PostMortem;
    return { ok: true, data };
  } catch (e) {
    return {
      ok: false,
      status: 0,
      reason: e instanceof Error ? e.message : "network_error",
    };
  }
}

/**
 * Player-private post-mortem.
 *
 * Same `PostMortemView` as `/share/[token]`; difference is the upstream
 * endpoint (`/game/{id}/post-mortem` mints the share_token on first view,
 * so the share button in this view always has a non-null URL). Auth-gated
 * by the parent `(authed)/layout.tsx`.
 *
 * 404 from the API → bounce to `/play`. 409 (`game_not_ended`) → bounce
 * to `/run/{id}` so the player keeps playing instead of staring at a
 * dead-end error page.
 */
export default async function PostMortemPage({
  params,
}: {
  params: Promise<Params>;
}) {
  const { id } = await params;
  const result = await loadPostMortem(id);

  if (!result.ok) {
    if (result.status === 404) redirect("/play");
    if (result.status === 409) redirect(`/run/${id}`);
    return (
      <main className="mx-auto flex min-h-screen w-full max-w-2xl flex-col gap-6 px-6 py-12">
        <div className="pixel-border-strong space-y-2 p-4">
          <p className="font-heading text-sm uppercase">
            POST-MORTEM UNREACHABLE
          </p>
          <p className="font-terminal text-xl">
            {result.status ? `HTTP ${result.status}` : "no response"} ·{" "}
            {result.reason}
          </p>
        </div>
      </main>
    );
  }

  return (
    <>
      <CrtOverlay />
      <PostMortemView data={result.data} />
    </>
  );
}
