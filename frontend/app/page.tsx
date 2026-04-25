import Link from "next/link";

import { Portrait } from "@/components/agent/Portrait";
import { SignInButton } from "@/components/auth/SignInButton";

const HERO_AGENTS = [
  { seed: "nadia", setting: "arctic" as const, role: "QUARTERMASTER" },
  { seed: "marko", setting: "arctic" as const, role: "COMMISSAR" },
  { seed: "irena", setting: "arctic" as const, role: "MEDIC" },
  { seed: "yuri", setting: "arctic" as const, role: "RADIO" },
];

export default function Landing() {
  return (
    <main className="relative mx-auto flex min-h-screen w-full max-w-5xl flex-col gap-16 px-6 py-12">
      {/* ── Header / hero ─────────────────────────────────────────── */}
      <header className="flex items-start justify-between gap-6">
        <div>
          <p className="font-display text-[10px] uppercase tracking-widest text-px-text-dim">
            v0.1 · arctic · daily
          </p>
          <h1 className="font-display mt-2 text-3xl leading-tight md:text-5xl">
            OUTPOST
          </h1>
        </div>
        <p className="font-terminal hidden text-2xl text-px-text-dim md:block">
          one run<span className="cursor" />
        </p>
      </header>

      {/* ── Pitch ─────────────────────────────────────────────────── */}
      <section className="grid grid-cols-1 gap-8 md:grid-cols-[3fr_2fr]">
        <div className="space-y-5">
          <p className="font-heading text-sm uppercase tracking-wider text-px-text-dim">
            Premise
          </p>
          <p className="font-terminal text-2xl leading-snug md:text-3xl">
            Four advisors run a fragile colony. You give the orders.
            Permadeath. One run a day.
          </p>
          <p className="text-base leading-relaxed text-px-text">
            A daily narrative roguelike. Each turn presents a crisis. The
            advisors argue. You pick from three. Decisions cost something —
            a resource, a life, the loyalty of the people you depend on.
          </p>
          <p className="text-base leading-relaxed text-px-text">
            Your run ends in eight to twelve turns. The post-mortem shows
            what happened, and what would have happened on the paths you
            didn&apos;t take.
          </p>

          <div className="flex flex-wrap items-center gap-4 pt-2">
            <SignInButton>Sign in with Google</SignInButton>
            <p className="font-terminal text-lg text-px-text-dim">
              Google account required. One run per day, server-checked.
            </p>
          </div>
        </div>

        {/* ── Hero crew ─────────────────────────────────────────── */}
        <aside className="pixel-border-accent space-y-3 p-5">
          <p className="font-heading text-xs uppercase tracking-wider text-px-text-dim">
            CREW · VORGRAD STATION
          </p>
          <ul className="space-y-3">
            {HERO_AGENTS.map((a) => (
              <li key={a.seed} className="flex items-center gap-3">
                <Portrait
                  seed={a.seed}
                  setting={a.setting}
                  size={48}
                  alt={`${a.role} portrait`}
                />
                <div className="flex flex-col">
                  <span className="font-heading text-sm uppercase">
                    {a.role}
                  </span>
                  <span className="font-terminal text-base text-px-text-dim">
                    advisor · alive
                  </span>
                </div>
              </li>
            ))}
          </ul>
        </aside>
      </section>

      {/* ── Three pillars ─────────────────────────────────────────── */}
      <section className="space-y-4">
        <p className="font-heading text-sm uppercase tracking-wider text-px-text-dim">
          What this is
        </p>
        <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
          <Pillar
            label="Decisions weigh"
            body="Every choice costs a resource, a life, or the loyalty of someone you need. The ones that don&apos;t cost anything aren&apos;t real choices."
          />
          <Pillar
            label="Consequences delay"
            body="What you decide on day three may not matter until day eight. Some seeds never fire — until they do."
          />
          <Pillar
            label="The dead stay dead"
            body="No save-scumming. No second tries today. The post-mortem is the artifact you take from the run."
          />
        </div>
      </section>

      {/* ── Setting strip ─────────────────────────────────────────── */}
      <section className="space-y-3">
        <p className="font-heading text-sm uppercase tracking-wider text-px-text-dim">
          Three settings, rotated
        </p>
        <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
          <SettingCard
            tag="arctic"
            name="VORGRAD STATION"
            body="The bones of a Soviet polar station, reactivated. Ice was never supposed to be this political."
          />
          <SettingCard
            tag="orbital"
            name="HELIOS-7"
            body="Fourteen months since Earth went dark. Garden bay yields drop each week. Someone below is still broadcasting."
          />
          <SettingCard
            tag="desert"
            name="CARMINE JUNCTION"
            body="The old highway runs past your station. Travelers stop, trade, sometimes stay. Some of them shouldn&apos;t."
          />
        </div>
      </section>

      {/* ── Footer ────────────────────────────────────────────────── */}
      <footer className="mt-auto flex flex-wrap items-center justify-between gap-4 border-t border-px-border pt-6 text-px-text-dim">
        <p className="font-terminal text-base">
          built for the patient. one cigarette&apos;s worth of dread per day.
        </p>
        <div className="flex gap-4 text-sm">
          <Link
            href="/about"
            className="font-heading uppercase hover:text-px-text"
          >
            About
          </Link>
          <a
            href="https://github.com/luclacombe/Outpost"
            target="_blank"
            rel="noreferrer noopener"
            className="font-heading uppercase hover:text-px-text"
          >
            Source
          </a>
        </div>
      </footer>
    </main>
  );
}

function Pillar({ label, body }: { label: string; body: string }) {
  return (
    <div className="pixel-border space-y-2 p-4">
      <p className="font-heading text-sm uppercase">{label}</p>
      <p className="text-sm leading-relaxed text-px-text">{body}</p>
    </div>
  );
}

function SettingCard({
  tag,
  name,
  body,
}: {
  tag: "arctic" | "orbital" | "desert";
  name: string;
  body: string;
}) {
  return (
    <div data-setting={tag} className="pixel-border-accent space-y-2 p-4">
      <p className="font-heading text-xs uppercase text-px-text-dim">
        {tag}
      </p>
      <p className="font-display text-xs leading-tight">{name}</p>
      <p className="text-sm leading-relaxed text-px-text">{body}</p>
    </div>
  );
}
