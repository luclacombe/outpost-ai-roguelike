import { cookies } from "next/headers";
import { createServerClient } from "@supabase/ssr";

/**
 * Server-side Supabase client for Server Components, Route Handlers,
 * and Server Actions. The middleware (../middleware.ts) is what actually
 * refreshes sessions; this client reads cookies for the current request.
 *
 * From a Server Component, calls to `setAll` are no-ops (RSCs cannot mutate
 * cookies). Token refresh therefore must happen in middleware, not here.
 */
export async function createClient() {
  const cookieStore = await cookies();

  return createServerClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL!,
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!,
    {
      cookies: {
        getAll() {
          return cookieStore.getAll();
        },
        setAll(cookiesToSet) {
          try {
            for (const { name, value, options } of cookiesToSet) {
              cookieStore.set(name, value, options);
            }
          } catch {
            // Called from a Server Component — ignore. Middleware handles refresh.
          }
        },
      },
    },
  );
}
