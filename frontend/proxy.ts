import type { NextRequest } from "next/server";

import { updateSession } from "@/lib/supabase/middleware";

/**
 * Refresh Supabase session cookies on every request. Skips static assets,
 * Next internals, and image optimization to avoid cookie work on every
 * .png/.svg request.
 *
 * Note: Next.js 16 renamed the `middleware` file convention to `proxy`
 * (and the export to `proxy`). This file lives at the project root as
 * `proxy.ts`. Keep the supabase helper module named `middleware.ts`
 * since it predates the rename and other repos still call it that.
 */
export async function proxy(request: NextRequest) {
  return updateSession(request);
}

export const config = {
  matcher: [
    /*
     * Match all request paths except:
     * - _next/static (static files)
     * - _next/image (image optimization)
     * - favicon.ico, robots.txt, sitemap.xml
     * - Asset extensions (jpg/png/svg/...)
     */
    "/((?!_next/static|_next/image|favicon.ico|robots.txt|sitemap.xml|.*\\.(?:png|jpg|jpeg|gif|svg|ico|webp|avif|woff|woff2)$).*)",
  ],
};
