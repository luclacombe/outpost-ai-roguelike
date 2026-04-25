import { createServerClient } from "@supabase/ssr";
import { NextResponse, type NextRequest } from "next/server";

/**
 * Middleware-side Supabase setup. Refreshes the session cookie on every
 * request and propagates fresh cookies to both the request (for downstream
 * Server Components) and the response (for the browser).
 *
 * Per @supabase/ssr docs: middleware-driven refresh is mandatory because
 * Server Components cannot set cookies, and parallel requests must not
 * collide on a single-use refresh token.
 */
export async function updateSession(request: NextRequest) {
  let response = NextResponse.next({ request });

  const supabaseUrl = process.env.NEXT_PUBLIC_SUPABASE_URL;
  const supabaseAnon = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;

  // No Supabase configured (e.g. fresh clone, no .env.local) — skip auth.
  // Production deployments are expected to set both vars; CI checks for them.
  if (!supabaseUrl || !supabaseAnon) {
    return response;
  }

  const supabase = createServerClient(
    supabaseUrl,
    supabaseAnon,
    {
      cookies: {
        getAll() {
          return request.cookies.getAll();
        },
        setAll(cookiesToSet) {
          for (const { name, value } of cookiesToSet) {
            request.cookies.set(name, value);
          }
          response = NextResponse.next({ request });
          for (const { name, value, options } of cookiesToSet) {
            response.cookies.set(name, value, options);
          }
        },
      },
    },
  );

  // Touching getUser() is the call that causes @supabase/ssr to refresh
  // the access token if it's expired. Do this on every authed request.
  await supabase.auth.getUser();

  return response;
}
