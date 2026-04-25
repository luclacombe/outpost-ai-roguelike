import { redirect } from "next/navigation";

import { CrtOverlay } from "@/components/CrtOverlay";
import { createClient } from "@/lib/supabase/server";

/**
 * Authed-route gate. Anything under (authed)/* requires a Supabase session.
 *
 * Per `@supabase/ssr` docs, prefer `getUser()` over `getSession()` here:
 * `getSession()` returns whatever's in the cookie unverified, while
 * `getUser()` re-checks against Supabase Auth — important on the server,
 * where a tampered cookie would otherwise be trusted.
 *
 * Phase 6 placeholder: also mounts the CRT overlay and a default
 * `data-setting`. Phase 7 will move the setting attribute onto the run
 * shell once we know which colony the player is in.
 */
export default async function AuthedLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  // No Supabase configured locally → no auth possible → bounce to landing.
  if (
    !process.env.NEXT_PUBLIC_SUPABASE_URL ||
    !process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY
  ) {
    redirect("/");
  }

  const supabase = await createClient();
  const {
    data: { user },
  } = await supabase.auth.getUser();

  if (!user) {
    redirect("/");
  }

  return (
    <div data-setting="arctic" className="contents">
      <CrtOverlay />
      {children}
    </div>
  );
}
