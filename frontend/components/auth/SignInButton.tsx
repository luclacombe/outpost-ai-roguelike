"use client";

import { useState } from "react";

import { Button } from "@/components/ui/button";
import { createClient } from "@/lib/supabase/client";

type Props = {
  /** Where to land after the OAuth round-trip. Defaults to /play. */
  redirectTo?: string;
  className?: string;
  children?: React.ReactNode;
};

export function SignInButton({
  redirectTo = "/play",
  className,
  children = "Sign in with Google",
}: Props) {
  const [pending, setPending] = useState(false);

  async function signIn() {
    setPending(true);

    // Surface env misconfiguration as a clean redirect to /auth/error
    // instead of an unhandled browser exception.
    if (
      !process.env.NEXT_PUBLIC_SUPABASE_URL ||
      !process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY
    ) {
      window.location.href = "/auth/error?reason=supabase_not_configured";
      return;
    }

    try {
      const supabase = createClient();
      const { error } = await supabase.auth.signInWithOAuth({
        provider: "google",
        options: {
          redirectTo: `${window.location.origin}/auth/callback?next=${encodeURIComponent(redirectTo)}`,
        },
      });
      if (error) {
        setPending(false);
        console.error("OAuth start failed", error);
      }
      // On success the browser navigates away to Google.
    } catch (e) {
      setPending(false);
      console.error("Sign-in threw before OAuth round-trip", e);
    }
  }

  return (
    <Button
      onClick={signIn}
      disabled={pending}
      className={className}
      size="lg"
    >
      {pending ? "Connecting…" : children}
    </Button>
  );
}
