@AGENTS.md

# CLAUDE.md — Outpost frontend

Next.js 16 (App Router) · React 19 · TypeScript strict · Tailwind v4 · shadcn/ui (re-themed) · DiceBear · Supabase Auth.

Game design + technical context lives in the root `CLAUDE.md` and `docs/`. This file captures the non-obvious frontend conventions that emerge during build.

---

## Pixel-art theme

The theme is in `app/globals.css`. Two layers:

1. **Outpost palette** — `--px-bg`, `--px-surface`, `--px-border`, `--px-text`, `--px-text-dim`, `--px-danger`, `--px-warning`, `--px-ok`, `--px-accent`. These are the source of truth.
2. **shadcn token bindings** — `--background`, `--foreground`, `--primary`, etc. wired in `:root` to point at the Outpost palette so shadcn primitives inherit the look.

Tailwind v4 generates utilities from `--color-*` declarations inside `@theme inline`. So `bg-px-accent`, `text-px-text-dim`, `border-px-border` all work. Use the `px-*` aliases for game-specific surfaces; use shadcn tokens (`bg-background`, `bg-card`) when composing shadcn primitives.

### Hard rules

- **`--radius: 0`.** Pixel art has no rounded corners. Do not add `rounded-*` utilities. If a shadcn component ships with hardcoded `rounded-*` classes, copy-edit it (Level 3 — see below).
- **No CSS gradients on surfaces.** Solid fills only. Reserve gradients for the CRT overlay.
- **No drop shadows.** Use the `pixel-border` family for separation.

### NES-pattern stepped borders

Three utility classes in `globals.css`:

- `.pixel-border` — neutral border in `--px-border`
- `.pixel-border-strong` — text-color border for emphasis
- `.pixel-border-accent` — setting-accent border (flips with `data-setting`)

They paint from `box-shadow` (no `border-radius`, no `border` property). Never combine with shadcn `rounded-*` — they fight.

### Setting palette swap

Set `data-setting="arctic|orbital|desert"` on any element to flip `--px-accent` for that subtree. The root layout sets `data-setting="arctic"` on `<html>` as the default; the run dashboard will swap this per active game.

```tsx
<html data-setting="arctic">  // root default
<section data-setting="orbital">  // local override
```

The accent flows into shadcn primitives that consume `--primary` / `--accent` / `--ring`, so a button inside `data-setting="desert"` automatically picks up oxide red.

### CRT overlay

`.crt-overlay` is a fixed full-screen `pointer-events: none` element. Mount once in the authed layout. Keep its scanline opacity ≤ `0.10` — anything higher drops body text below WCAG AA on `--px-bg`.

### Pixel-rendering primitives

- `.pixel` — `image-rendering: pixelated`. Apply to `<img>` / SVG portraits to keep DiceBear pixels crisp at any size.
- `.font-pixel` / `.font-display` / `.font-heading` — disable font smoothing so bitmap fonts render sharp on HiDPI macOS Chrome.
- `.cursor` — terminal-style blinking block (`▮`). Drop on a `<span>` after running text.
- `.resource-bar-fill` — `transition: width 400ms steps(20)` for stepped pixel-feel transitions on resource bars.
- `.status-critical` — pulsing `step-start` color animation for at-threshold values.

---

## Font role mapping

Four fonts loaded via `next/font/google` in `app/layout.tsx`. Each gets a CSS variable. Tailwind exposes them as `font-*` utilities via `@theme inline`.

| Tailwind class | Font | When to use |
|---|---|---|
| `font-display` | Press Start 2P | Section titles, headers, hero numbers — **2–3 words max**. Unreadable below 11px. |
| `font-heading` | Silkscreen | UI labels, resource names, button labels, stat names. Cleaner than Press Start at 10–12px. |
| `font-terminal` | VT323 | Event log timestamps, status codes, agent IDs, terminal-flavored medium-length text (20px+). |
| `font-body` | IBM Plex Mono | Narrative copy, advisor lines, event log prose. **The default body font.** Real type, monospace, legible at 14–16px. |

`font-sans` and `font-mono` both alias `font-body` — never use them for display copy.

**Anti-pattern:** Press Start 2P at body size. Always pair with one of: `text-xs`, `text-sm`, `text-base`. Avoid `text-lg`+ for Press Start.

---

## shadcn composition strategy

### Level 2 (CSS variables) — **default**

shadcn components inherit our palette and `--radius: 0` automatically because we override their token vars in `:root`. For Button, Card, Input, Badge, Tooltip — just use them. No source edits.

```tsx
import { Button } from "@/components/ui/button"
<Button>Begin run</Button>            // already pixel-themed
<Button variant="destructive">Order</Button>  // already in danger red
```

### Level 3 (copy-edit) — only when forced

If a shadcn primitive ships with hardcoded `rounded-*` classes (rare), copy the file in `components/ui/` and remove the radius utility. Do this for:
- Dialog (rounded corners on the panel)
- Sheet (rounded corners on the slide-out)

Document the override with a single-line comment: `// override: rounded-* removed for pixel theme`.

Do **not** Level-3 a component for cosmetic preference — only when CSS-var overrides cannot reach the property.

### Where to compose

- Settings / config panes — `Tabs` + `Card` per group
- Auth — centered `Card` + inline `Alert` for errors
- Destructive flows (issue Order, force coup) — `AlertDialog`, **not** `Dialog`
- Mobile nav (post-mortem detail drawer) — `Sheet`

Never replace a shadcn primitive with a raw `<div>` for "simplicity."

---

## Anti-patterns

- Using `rounded-*` utilities — pixel art has no curved corners.
- Importing `@dicebear/collection` — we use the per-style `@dicebear/pixel-art` and `@dicebear/thumbs` packages.
- Using `Image` from `next/image` for DiceBear avatars — DiceBear returns SVG strings; render them via `<img src={dataUri} />` with `className="pixel"`.
- Letting Tailwind's `dark:` variants leak in — we have one theme (dark). The `dark` class is unused; do not gate styles on it.
- Hardcoded hex colors in components — always go through palette vars (`var(--px-accent)` or `bg-px-accent`).
- `text-zinc-*`, `text-neutral-*`, `bg-slate-*` — these are scaffold leftovers. Use `text-foreground`, `text-px-text-dim`, `bg-card`, `bg-px-surface` instead.

---

## Next.js 16 conventions to remember

- **`proxy.ts`, not `middleware.ts`.** Next 16 renamed the file convention. The exported function is also `proxy` (not `middleware`). The supabase helper at `lib/supabase/middleware.ts` keeps its old name internally — only the root file is `proxy.ts`.
- **`searchParams` is a Promise.** Page components receive `searchParams: Promise<{...}>`; `await` it before reading. Same for `params`.
- **`cookies()` is async.** Always `await cookies()` before accessing.
- **Client component triggers OAuth.** Server actions don't expose `window.location.origin`, which we need for `redirectTo`. Sign-in is a client `'use client'` component.
- **Auth gate uses `getUser()`, not `getSession()`.** `getSession()` trusts the cookie; `getUser()` re-validates against Supabase. Always `getUser()` on the server.
- **Middleware (proxy) skips assets via matcher.** The matcher excludes `_next/*`, image extensions, and fonts — don't widen it without reason; every match runs a Supabase token refresh.

## Supabase env tolerance

Both the proxy and the `(authed)` layout no-op when `NEXT_PUBLIC_SUPABASE_URL` / `NEXT_PUBLIC_SUPABASE_ANON_KEY` are unset. The landing remains reachable in a fresh clone with no `.env.local`. CI / production must set both — the SignInButton click will surface the failure visibly otherwise.
