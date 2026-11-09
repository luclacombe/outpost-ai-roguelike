"use client";

import Link from "next/link";
import { useState } from "react";

import { Button } from "@/components/ui/button";

type Props = {
  /** Null on the public share view; the player's own view always has one. */
  shareToken: string | null;
};

/**
 * Closing actions under a post-mortem: copy the public link, open it, go
 * home. Without these a finished run was a dead end.
 */
export function PostMortemActions({ shareToken }: Props) {
  const [copied, setCopied] = useState<"idle" | "done" | "failed">("idle");
  const sharePath = shareToken ? `/share/${shareToken}` : null;

  async function copy() {
    if (!sharePath) return;
    try {
      await navigator.clipboard.writeText(`${window.location.origin}${sharePath}`);
      setCopied("done");
    } catch {
      setCopied("failed");
    }
  }

  return (
    <nav className="flex flex-wrap items-center gap-4 border-t border-px-border bg-px-bg px-6 py-8 md:px-10">
      {sharePath ? (
        <>
          <Button onClick={copy}>
            {copied === "done" ? "Link copied" : "Copy share link"}
          </Button>
          <Link
            href={sharePath}
            className="font-heading text-sm uppercase text-px-text-dim hover:text-px-text"
          >
            Open public page
          </Link>
          {copied === "failed" ? (
            <span className="font-terminal text-lg text-px-text-dim">
              Copy blocked. Open the public page and share its address.
            </span>
          ) : null}
        </>
      ) : null}
      <Link
        href="/"
        className="font-heading text-sm uppercase text-px-text-dim hover:text-px-text"
      >
        Back to landing
      </Link>
    </nav>
  );
}
