import type { Metadata } from "next";
import { notFound } from "next/navigation";

import { CrtOverlay } from "@/components/CrtOverlay";
import { PostMortemView } from "@/components/postmortem/PostMortemView";
import {
  API_BASE_URL,
  type PostMortem,
  isApiErrorEnvelope,
} from "@/lib/api/client";

type Params = { token: string };

async function loadShare(token: string): Promise<PostMortem | null> {
  // Dev-only fixture-backed tokens — see opengraph-image.tsx for the same
  // gate. Allows offline iteration on the share UX without a live backend.
  if (process.env.NODE_ENV !== "production") {
    if (token === "dev") {
      const f = await import("@/lib/dev/postMortemFixture.json");
      return (f.default ?? f) as unknown as PostMortem;
    }
    if (token === "dev-rich") {
      const f = await import("@/lib/dev/postMortemFixtureRich.json");
      return (f.default ?? f) as unknown as PostMortem;
    }
  }
  const res = await fetch(`${API_BASE_URL}/game/share/${token}`, {
    cache: "no-store",
  });
  if (!res.ok) {
    if (res.status === 404) return null;
    const body = (await res.json().catch(() => ({}))) as unknown;
    const code = isApiErrorEnvelope(body) ? body.error.code : `http_${res.status}`;
    throw new Error(`/share fetch failed: ${code}`);
  }
  return (await res.json()) as PostMortem;
}

/**
 * Public, unauthenticated post-mortem view.
 *
 * The URL the player pastes into Twitter / Discord / Bluesky lands here.
 * Bearer-token auth is replaced by the share_token in the path — anyone
 * with the URL can read; nothing else.
 *
 * Metadata is derived from the run so each share unfurls with its own
 * title, description, and (via co-located `opengraph-image.tsx`) image.
 */
export async function generateMetadata({
  params,
}: {
  params: Promise<Params>;
}): Promise<Metadata> {
  const { token } = await params;
  const data = await loadShare(token).catch(() => null);
  if (!data) {
    return {
      title: "Outpost — shared run",
      description: "A daily narrative roguelike. Survive a run.",
    };
  }
  const title = `${data.setting_name} · ${data.end_state}`;
  const description =
    data.closing_line ?? data.opening_line ?? `A ${data.total_turns}-turn run.`;
  return {
    title,
    description,
    openGraph: { title, description, type: "article" },
    twitter: { card: "summary_large_image", title, description },
  };
}

export default async function SharedPostMortemPage({
  params,
}: {
  params: Promise<Params>;
}) {
  const { token } = await params;
  const data = await loadShare(token);
  if (!data) notFound();
  return (
    <>
      <CrtOverlay />
      <PostMortemView data={data} />
    </>
  );
}
