import Link from "next/link";

import { Button } from "@/components/ui/button";

type Search = Promise<{ reason?: string; message?: string }>;

export default async function AuthErrorPage({
  searchParams,
}: {
  searchParams: Search;
}) {
  const { reason, message } = await searchParams;

  return (
    <main className="mx-auto flex min-h-screen w-full max-w-xl flex-col items-start justify-center gap-6 px-6 py-12">
      <p className="font-display text-xs uppercase tracking-widest text-px-text-dim">
        AUTH · ERROR
      </p>
      <h1 className="font-display text-2xl leading-tight">SIGN-IN FAILED</h1>
      <div className="pixel-border space-y-3 p-4">
        <p className="font-heading text-sm uppercase">Reason</p>
        <p className="font-terminal text-xl">{reason || "unknown"}</p>
        {message ? (
          <p className="text-sm text-px-text-dim">{message}</p>
        ) : null}
      </div>
      <p className="text-base text-px-text">
        Try again. If it keeps failing, your Google account may not be allowed
        for this build, or the cookie was lost in transit.
      </p>
      <Button asChild>
        <Link href="/">Back to landing</Link>
      </Button>
    </main>
  );
}
