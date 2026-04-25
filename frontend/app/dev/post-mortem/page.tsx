import { notFound } from "next/navigation";

import { CrtOverlay } from "@/components/CrtOverlay";
import { PostMortemView } from "@/components/postmortem/PostMortemView";
import { type PostMortem } from "@/lib/api/client";
import postMortemFixture from "@/lib/dev/postMortemFixture.json";
import postMortemFixtureRich from "@/lib/dev/postMortemFixtureRich.json";

/**
 * Dev-only post-mortem fixture for visual iteration.
 *
 * Mirrors `/dev/dashboard` — loads a frozen JSON snapshot of a real run
 * and pipes it into the same components `/post-mortem/[id]` will use.
 * No backend required; offline-iterable.
 *
 * Two fixtures, both real (round-tripped through the actual aggregator):
 *
 *   default          — Phase 7 exit run id `7451e10a-...`. ENDURED, no
 *                      seeds, no near-miss coups. Tests the empty-state
 *                      path of the Counterfactuals panel.
 *   `?fixture=rich`  — synthesized 3-turn run with a fired seed, two
 *                      dormant seeds, and a near-miss coup pair. Generated
 *                      via the build_post_mortem call in the test
 *                      scenario; round-tripped through the real
 *                      aggregator, never hand-written. Tests the
 *                      populated-state path.
 */
export default async function DevPostMortemPage({
  searchParams,
}: {
  searchParams: Promise<{ fixture?: string }>;
}) {
  if (process.env.NODE_ENV === "production") notFound();
  const params = await searchParams;
  const data = (
    params.fixture === "rich" ? postMortemFixtureRich : postMortemFixture
  ) as PostMortem;
  return (
    <>
      <CrtOverlay />
      <PostMortemView data={data} />
    </>
  );
}
