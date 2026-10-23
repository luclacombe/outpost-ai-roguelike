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

---

## Dashboard grid (Phase 7)

Layout lives in `globals.css` under `.dashboard-grid`. Three named areas, two breakpoints:

| Width | Layout | Notes |
|---|---|---|
| ≥1024 (`lg`) | 2 columns; `h-svh`; event panel scrolls in place | Status spans both right rows |
| 768–1023 | 2 columns (`260px / 1fr`); `min-h-svh`; page scrolls | Event panel does NOT internally scroll |
| ≤768 | 1 column; ordered `header → event → choose → status` | Action above the fold; status sinks to bottom |

**Why `lg:` and not `md:` for the desktop pinning.** Tailwind's `md:` is `min-width: 768`, which collides at exactly 768 with the mobile `@media (max-width: 768px)` block. Using `lg:` (1024) avoids the boundary fight. If you need a tablet-specific tweak, write a custom `@media` rule, not `md:`.

**`overflow-y-auto` on `.dashboard-area-event`.** Apply with the `lg:overflow-y-auto` Tailwind utility so the scroll container only exists in the desktop fixed-height grid. On mobile the event area must be `overflow: visible` or its content gets clipped under the choose panel.

**`h-full` on inner panels (`EventDebatePanel`, `ColonyStatus`).** Same rule: gate behind `lg:`. On mobile these grow to content. Without the gate, the layout collapses at 768 because `h-full` on auto-sized parents resolves to a fixed pixel value the row never gets back.

---

## SSE consumption pattern (`hooks/useTurnStream.ts`)

`fetch` + `ReadableStream` + `TextDecoderStream` — never `EventSource` (no `Authorization` header support). Frame parser lives in `lib/api/sse.ts` (`parseSseFrame`, `iterateSse`). Final sentinel is the literal string `[DONE]`, not JSON.

**Hook shape.**

```ts
const stream = useTurnStream(gameId, seed, opts?);
// → { phase, turnNumber, currentEvent, proposals, debateLines,
//     narration, legitimacy, playerHealth, costCents, resources, agents,
//     endState,
//     error, eventLog, beginTurn, submitChoice }
```

`opts` is the test/dev seam:

| Option | Default | When to override |
|---|---|---|
| `beginTurnUrl` / `decideUrl` | `${API_BASE_URL}/game/{id}/turn` etc. | Dev preview points these at `/api/dev/turn` |
| `getAuthToken` | `supabase.auth.getSession().access_token` | Dev preview returns `"dev-mock"`; tests can return any string |
| `refreshSession` | `supabase.auth.refreshSession()` | Dev preview no-ops; tests assert it was called |

**Token refresh discipline (PLAN 7.6, hard rule).**

1. `getAuthToken()` runs BEFORE every fetch. Never close-over a token from mount.
2. On `401` (response status OR thrown stream error), if `attempt === 0`, call `refreshSession()` and re-POST. `/turn` is idempotent while paused — the backend short-circuits to re-emit `proposals_ready`. `/decide` is NOT safely retryable past pause; surface error and let the caller refresh state via `GET /game/{id}`.
3. The reducer keeps `debateLines` and `narration` across a re-emitted `proposals_ready` IFF the event id is unchanged (see `applyEvent` → `proposals_ready` branch). This is what lets reconnect look idempotent to the player.

**Reducer is exported.** `applyEvent(state, event)` is pure and can be unit-tested without React. Add tests there before the hook proper, since most logic lives in the reducer.

---

## Dev SSE harness (`/api/dev/turn`, `/api/dev/decide`)

In-process Next route handlers that mirror the backend's SSE shape. Gated by `process.env.NODE_ENV !== "production"` (returns 404 in production builds). Used by `/dev/dashboard` to drive the real `useTurnStream` hook without a running backend.

Query switches on `/api/dev/turn`:
- `?fail_first=1` — first POST returns 401, retry succeeds. Verifies the refresh+reconnect path.
- `?fail_always=1` — every POST returns 401. Verifies the error UX.

Session distinction across reloads is via the `x-dev-session` header (set in `DashboardPreview` via a one-shot `window.fetch` patch).

---

## Error UX

Stream errors surface as an inline alert inside the event panel:

```tsx
<div role="alert" data-testid="stream-error" className="border border-px-danger ...">
  {code} · {message}
</div>
```

Inline (not a toast or dialog) because errors are tied to the current turn — the player needs the message in context with the dashboard, not popping out of nowhere. The `data-testid` is for Playwright; the role is for AT.

Per-action errors (e.g. proposal rejected) reset on the next user action via `error: null` in `beginTurn`/`submitChoice`.

Pre-stream HTTP errors (4xx other than 401) get the same alert with the backend's `error.code` / `error.message` from the standard error envelope (root `CLAUDE.md`).

---

## CRT overlay & WCAG

`<CrtOverlay />` is a shared component mounted in `(authed)/layout.tsx` and `dev/dashboard`. The 0.08 scanline opacity is the upper bound — at 0.10+ the dimmer body text drops below WCAG AA on `--px-bg`.

**`--px-text-dim` floor: `#968f7d`** (5.12:1 vs `--px-bg` with the CRT overlay applied; AA requires 4.5:1). The previous value `#7a7a6e` failed AA at 4.01:1 even without the overlay. If you find yourself wanting a darker dim — pick a new color and re-run the contrast formula in dev tools first; don't drop the floor.

---

## shadcn re-theme status (Phase 7)

All dashboard primitives so far hit Level 2 (CSS-var override only). No Level-3 copies needed yet. The Button's hardcoded `rounded-*` classes are already neutralized by `--radius: 0` on the token, so the pixel-art look survives without a local fork. Re-evaluate when adding `Dialog` / `Sheet` / `AlertDialog` (likely Level 3 on those — Phase 8 territory).

---

## Post-mortem routing (Phase 8)

Two surfaces consume the same `PostMortemView`:

| Route | Auth | Backend | Purpose |
|---|---|---|---|
| `(authed)/post-mortem/[id]` | bearer JWT | `GET /game/{id}/post-mortem` | Player-private view of their own runs. Mints a `share_token` on first view (idempotent), so the page can render a share button without a separate "create share link" step. |
| `share/[token]` | none | `GET /game/share/{token}` | Public, unfurlable view linked from social shares. The token is path-positional auth — anyone holding the URL gets the run. No revocation in MVP. |

**Why two routes, not one with optional auth.** The public route is reachable to crawlers (Twitter, Discord, Bluesky). The authed route's parent `(authed)/layout.tsx` enforces `getUser()` — a crawler hitting `/post-mortem/<id>` would 401. Keep the public surface explicitly public.

**`/run/<id>` redirects to `/post-mortem/<id>` when `end_state !== null`.** That redirect only runs on page load, so the dashboard's "ended" footer carries a "Read the post-mortem" link (`ChoosePanel` `postMortemHref`); before 2026-10-23 it had none and a live player was stuck on "being assembled" until they reloaded.

**Colony stats come from the stream.** `turn_advanced` carries every resource and advisor (`resources`, `agents`); `DashboardShell` renders `stream.resources` / `stream.agents`, seeded from `/game/{id}`. Reading `initialState.*` for anything that changes per turn freezes it for the whole run (the 2026-10-23 live run showed food 60 while the engine had 6).

## OG image patterns (Phase 8)

Co-located `opengraph-image.tsx` next to a public `page.tsx`. Two metadata exports gate behavior:

```ts
export const size = { width: 1200, height: 630 };
export const contentType = "image/png";
export const alt = "Outpost — shared run";
```

- `params` is a Promise in Next.js 16, even inside `opengraph-image.tsx`. Always `await params` — sync access logs a deprecation warning AND returns `undefined` for the property, which falls through to the fallback card.
- **Width 1200 × 630 is the cross-platform safe default.** SPEC §11.3 calls for "compact square" but Twitter/Discord/Bluesky behavior with square images is uneven (Twitter crops centre, Bluesky may stretch). Wide-format unfurls predictably across all three.
- **No external font loads.** Satori's default works for the share card. Adding a Google Font would slow the OG endpoint by a network round-trip per render.
- **Inline styles only.** `tw=""` Tailwind support exists but every variant we'd want is one-off; prop styles keep the layout legible without a Tailwind context.
- **Dev fallback tokens.** `/share/dev` and `/share/dev-rich` hydrate from `lib/dev/postMortemFixture.json` / `postMortemFixtureRich.json` so the OG image and share page can be iterated offline without the backend running. Gated by `NODE_ENV !== "production"`.

## Post-mortem fixtures (Phase 8)

Two static fixtures live under `lib/dev/`:

- `postMortemFixture.json` — frozen API response from the Phase 7 exit run id `7451e10a-...`. ENDURED, no consequence_seeds, no near-miss coups. The truthful empty case for the Counterfactuals panel.
- `postMortemFixtureRich.json` — synthesized 3-turn run inserted via the test scaffolding in `tests/test_post_mortem.py`'s pattern, then round-tripped through the real `build_post_mortem` aggregator. Has a fired seed, two dormant seeds, and a near-miss coup. The populated case.

Both are real outputs from `app.game.post_mortem.build_post_mortem` — never hand-edited. Regenerate with the Python snippet at the top of each file's git history when the schema changes.

The dev page at `app/dev/post-mortem/page.tsx` toggles between them via `?fixture=rich`.

## SSE Continue gate (Phase 8 P1 fix)

`useTurnStream`'s reducer holds the next turn's `proposals_ready` payload in `pendingEvent` / `pendingProposals` / `pendingTurnNumber` / `pendingLogEntry` slots when narration is showing AND the new event differs from the current one. The new phase `awaiting_continue` surfaces a Continue button via `ChoosePanel`; clicking it calls `continueAfterNarration()` which promotes pending → main slots and clears narration + debate.

Without this gate, `/decide`'s SSE stream auto-advances past `narration` into the next turn's `proposals_ready` immediately, so the player has no time to absorb consequences. The pending-slot pattern keeps the rest of the reducer (turn_advanced, etc.) unaffected — the resolved turn's state changes still apply (legitimacy, cost), only the next event/proposals are gated.
