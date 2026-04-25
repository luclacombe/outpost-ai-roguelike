import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Portrait, type Setting } from "@/components/agent/Portrait";

const PORTRAIT_SEEDS = ["nadia", "marko", "irena", "yuri"] as const;

export default function ThemePreviewPage() {
  return (
    <main className="mx-auto flex w-full max-w-4xl flex-col gap-8 px-6 py-10">
      <header className="space-y-2">
        <p className="font-display text-xs text-px-text-dim">VORGRAD STATION · DAY 05 / 10</p>
        <h1 className="font-display text-3xl leading-tight">OUTPOST</h1>
        <p className="font-terminal text-2xl text-px-text-dim">
          Four advisors. One colony. One run a day.
        </p>
      </header>

      <section className="grid grid-cols-1 gap-4 md:grid-cols-2">
        <div className="pixel-border p-4">
          <p className="font-heading text-sm">PIXEL-BORDER · SURFACE</p>
          <p className="mt-2 text-sm">
            NES-pattern stepped border. No <code>border-radius</code>. Paints from
            <code> box-shadow</code>.
          </p>
        </div>
        <div className="pixel-border-accent p-4">
          <p className="font-heading text-sm">PIXEL-BORDER · ACCENT</p>
          <p className="mt-2 text-sm">
            Setting accent swap via <code>data-setting</code>.
          </p>
        </div>
      </section>

      <section className="grid grid-cols-1 gap-4 md:grid-cols-3">
        {(["arctic", "orbital", "desert"] as const).map((s) => (
          <div key={s} data-setting={s} className="pixel-border-accent p-4">
            <p className="font-heading text-xs text-px-text-dim">SETTING</p>
            <p className="font-display mt-1 text-sm uppercase">{s}</p>
            <p className="mt-2 text-xs text-px-text-dim">
              accent flips via <code>data-setting=&quot;{s}&quot;</code>
            </p>
          </div>
        ))}
      </section>

      <section>
        <Card className="pixel-border">
          <CardHeader>
            <CardTitle className="font-heading">SHADCN CARD · RE-THEMED</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <p className="text-sm">
              Body copy in IBM Plex Mono. Real type, monospace, legible at 14–16px.{" "}
              <span className="cursor" />
            </p>
            <div className="flex flex-wrap gap-3">
              <Button>Begin run</Button>
              <Button variant="secondary">Sign in</Button>
              <Button variant="outline">View post-mortem</Button>
              <Button variant="destructive">Issue order</Button>
            </div>
          </CardContent>
        </Card>
      </section>

      <section className="space-y-2">
        <p className="font-heading text-sm text-px-text-dim">RESOURCE BARS</p>
        {([
          ["fuel", 58],
          ["food", 34],
          ["warmth", 50],
          ["medicine", 31],
          ["morale", 55],
        ] as const).map(([name, pct]) => (
          <div key={name} className="flex items-center gap-3">
            <span className="font-heading w-24 text-xs uppercase">{name}</span>
            <div className="h-3 flex-1 bg-px-surface">
              <div
                className="resource-bar-fill h-full bg-px-accent"
                style={{ width: `${pct}%` }}
              />
            </div>
            <span className="font-terminal w-12 text-right text-lg">{pct}%</span>
          </div>
        ))}
      </section>

      <section className="space-y-2">
        <p className="font-heading text-sm text-px-text-dim">TYPOGRAPHY ROLES</p>
        <p className="font-display text-base">FONT-DISPLAY · PRESS START 2P</p>
        <p className="font-heading text-base">FONT-HEADING · SILKSCREEN</p>
        <p className="font-terminal text-2xl">font-terminal · VT323 18–24px</p>
        <p className="font-body text-sm">
          font-body · IBM Plex Mono · narrative copy, advisor lines, event log prose.
        </p>
      </section>

      <section className="space-y-3">
        <p className="font-heading text-sm text-px-text-dim">
          PORTRAITS · DICEBEAR PIXEL-ART · per-setting clothing tones
        </p>
        {(["arctic", "orbital", "desert"] as const).map((setting) => (
          <div
            key={setting}
            data-setting={setting}
            className="pixel-border-accent flex items-center gap-4 p-4"
          >
            <p className="font-display w-24 text-xs uppercase">{setting}</p>
            <div className="flex gap-3">
              {PORTRAIT_SEEDS.map((seed) => (
                <Portrait
                  key={seed}
                  seed={seed}
                  setting={setting as Setting}
                  size={64}
                  alt={`${seed} (${setting})`}
                />
              ))}
            </div>
            <div className="ml-auto flex items-center gap-2">
              <p className="font-heading text-xs text-px-text-dim">THUMB 32</p>
              {PORTRAIT_SEEDS.map((seed) => (
                <Portrait
                  key={`t-${seed}`}
                  seed={seed}
                  setting={setting as Setting}
                  variant="thumb"
                  size={32}
                  alt=""
                />
              ))}
            </div>
          </div>
        ))}
      </section>

      <footer className="font-terminal text-px-text-dim text-sm">
        d05 · heating core leak · resolved
      </footer>
    </main>
  );
}
