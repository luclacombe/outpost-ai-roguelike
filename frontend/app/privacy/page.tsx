import type { Metadata } from "next";
import Link from "next/link";

import { Button } from "@/components/ui/button";

export const metadata: Metadata = {
  title: "Privacy · Outpost",
};

const CONTACT = "luc.c.lacombe@gmail.com";

const SECTIONS: { label: string; body: React.ReactNode }[] = [
  {
    label: "What is stored",
    body: (
      <>
        Signing in with Google gives Outpost your name, email address and a
        Google account ID. Outpost also stores your runs: the setting, the
        advisors, each turn&apos;s event, your choices and the outcome.
      </>
    ),
  },
  {
    label: "What it is used for",
    body: (
      <>
        To sign you in, to enforce one run per day, and to show you the
        post-mortem of your runs. Nothing is sold, and there are no ads or
        analytics trackers. The only cookies are the ones that keep you signed
        in.
      </>
    ),
  },
  {
    label: "Who processes it",
    body: (
      <>
        Supabase holds accounts and game data (EU, Ireland). Vercel serves the
        site and Railway runs the game server. Turns are written by a language
        model through OpenRouter: it receives the game state and your choices,
        never your name or email. Model calls are logged to Langfuse under a
        random account ID for debugging.
      </>
    ),
  },
  {
    label: "Shared runs",
    body: (
      <>
        A post-mortem gets a share link. Anyone holding that link can read
        that run. It does not show your name or email.
      </>
    ),
  },
  {
    label: "Deleting your data",
    body: (
      <>
        Email{" "}
        <a href={`mailto:${CONTACT}`} className="underline hover:text-px-text">
          {CONTACT}
        </a>{" "}
        from the address you signed in with, and your account and runs will be
        deleted.
      </>
    ),
  },
];

export default function PrivacyPage() {
  return (
    <main className="mx-auto flex min-h-screen w-full max-w-2xl flex-col items-start gap-6 px-6 py-12">
      <p className="font-display text-xs uppercase tracking-widest text-px-text-dim">
        OUTPOST · PRIVACY
      </p>
      <h1 className="font-display text-2xl leading-tight">PRIVACY</h1>
      <p className="text-base leading-relaxed text-px-text">
        Outpost is a personal project by Luc Lacombe. This page says what it
        keeps about you and why.
      </p>
      {SECTIONS.map(({ label, body }) => (
        <section key={label} className="pixel-border w-full space-y-2 p-4">
          <h2 className="font-heading text-sm uppercase">{label}</h2>
          <p className="text-sm leading-relaxed text-px-text">{body}</p>
        </section>
      ))}
      <p className="font-terminal text-lg text-px-text-dim">
        Last updated 9 November 2026.
      </p>
      <Button asChild>
        <Link href="/">Back to landing</Link>
      </Button>
    </main>
  );
}
