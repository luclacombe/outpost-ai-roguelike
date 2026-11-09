import { NextResponse, type NextRequest } from "next/server";

import { createClient } from "@/lib/supabase/server";

/**
 * Supabase OAuth callback. Exchanges the `?code=...` query for a session
 * (Supabase writes the cookie via the `setAll` handler in our server
 * client) and redirects the user to `next`.
 *
 * Failure modes:
 *   - missing/invalid code: redirect to /auth/error
 *   - exchange error: redirect to /auth/error with the message
 */
export async function GET(request: NextRequest) {
  const url = new URL(request.url);
  const code = url.searchParams.get("code");
  const next = url.searchParams.get("next") || "/play";

  if (!code) {
    return NextResponse.redirect(new URL("/auth/error?reason=missing_code", url.origin));
  }

  if (
    !process.env.NEXT_PUBLIC_SUPABASE_URL ||
    !process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY
  ) {
    return NextResponse.redirect(
      new URL("/auth/error?reason=supabase_not_configured", url.origin),
    );
  }

  const supabase = await createClient();
  const { error } = await supabase.auth.exchangeCodeForSession(code);

  // Redirect to the requested destination — keep it relative to our origin
  // to prevent open-redirect vectors. "//host" and "/\host" are
  // protocol-relative and would leave the site.
  const safeNext = /^\/(?![/\\])/.test(next) ? next : "/play";

  if (error) {
    // A replayed callback (double click, back button, reopened URL) fails
    // the exchange because the verifier was already used. If the browser
    // already holds a valid session, the player is signed in: carry on.
    const { data } = await supabase.auth.getUser();
    if (data.user) {
      return NextResponse.redirect(new URL(safeNext, url.origin));
    }

    const errorUrl = new URL("/auth/error", url.origin);
    errorUrl.searchParams.set("reason", "exchange_failed");
    errorUrl.searchParams.set("message", error.message);
    return NextResponse.redirect(errorUrl);
  }

  return NextResponse.redirect(new URL(safeNext, url.origin));
}
