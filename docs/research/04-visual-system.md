# Research: Visual System — Pixel-Art Dashboard

> Research agent output from 2026-04-23. Source for visual design decisions. Consulted when making decisions in `docs/SPEC.md` section 10 (aesthetic), `docs/ARCHITECTURE.md` section 8 (frontend).

## DiceBear Pixel Art Style

### Install
```bash
npm i @dicebear/core @dicebear/pixel-art
```

### React Usage (deterministic from agent ID)
```tsx
import { createAvatar } from '@dicebear/core';
import { pixelArt } from '@dicebear/pixel-art';

function AgentPortrait({ agentId }: { agentId: string }) {
  const svg = createAvatar(pixelArt, {
    seed: agentId,            // any string — same seed = same avatar forever
    size: 64,
    accessories: ['glasses'],
    clothingColor: ['3c4f5c'],
    backgroundColor: ['1a1a1a'],
  }).toString();

  const dataUri = `data:image/svg+xml;utf8,${encodeURIComponent(svg)}`;
  return <img src={dataUri} alt="Agent" width={64} height={64} />;
}
```

### Output: SVG string. Convert to data URI for `<img>` or render inline.

### Pixel-Art Style Options Confirmed
- `accessories` (glasses, etc. — multiple variants)
- `beard` (variants)
- `clothingColor` (hex array)
- `eyesColor` (hex array)
- `backgroundColor` (hex array)
- `seed` (any string, deterministic PRNG)

### Setting-Based Variation
Pass an options object varying by game setting. Arctic: force cold-tone `clothingColor`. Space: restrict to helmet-compatible `accessories`. Granularity is "which variant pool to allow," enough for per-setting flavoring.

### Alternatives to Consider
- `pixel-art-neutral` — no gender cues, simpler silhouette. Better for ambiguous agents.
- `thumbs` — stylized, minimal. Works at 32px for agent list rows.
- Avoid `adventurer` — breaks pixel aesthetic.

**Recommendation:** `pixel-art` as primary with `seed: agent.id`. For small portraits (roster rows), `thumbs` at 32px.

## Pixel-Art CSS for shadcn/ui

### Core Reset
```css
@layer base {
  :root {
    --radius: 0;                    /* nuke all border-radius */
    --shadow-pixel: 4px 4px 0 0 var(--color-shadow);
  }
}
```

### NES.css-Style Stepped Border (no actual `border` property)
```css
.pixel-border {
  box-shadow:
    /* outer frame — light top/left, dark bottom/right */
    -4px  0    0 var(--px-border),
     4px  0    0 var(--px-border),
     0   -4px  0 var(--px-border),
     0    4px  0 var(--px-border),
    /* inner chamfer — stepped corner illusion */
    -8px  0    0 var(--px-bg),
     8px  0    0 var(--px-bg),
     0   -8px  0 var(--px-bg),
     0    8px  0 var(--px-bg);
}
```

Box-shadow steps at pixel multiples instead of `border-radius`. **Borrow pattern — do not install NES.css** (conflicts with Tailwind reset).

### Tailwind v4 Theme Config
```css
@theme {
  --spacing-1: 4px;
  --spacing-2: 8px;
  --spacing-3: 12px;
  --spacing-4: 16px;
  --shadow-pixel: 4px 4px 0 var(--color-shadow-hard);
  --shadow-inset-pixel: inset 2px 2px 0 var(--color-shadow-inset);
}
```

### shadcn/ui Re-Theme Verdict
Re-themeable aggressively via CSS variables + `--radius: 0`. Button, Card, Dialog, Badge all respond. Only components with hardcoded `rounded-*` classes fight back — copy those and remove the class. Level 2 (CSS vars) for 80% of components; Level 3 (source override) for Dialog/Sheet.

## Typography

| Font | Best Use | Body Text? | Notes |
|------|----------|-----------|-------|
| **Press Start 2P** | Short headers only (8–16px) | No | 8px grid bitmap. Unreadable at 11px. Use 2–3 words max. |
| **VT323** | Terminal-flavored medium text, event log | Acceptable (20px+) | Works for 2–3 line snippets, fatiguing for long prose. |
| **Silkscreen** | UI labels, resource names, button text | No | Tighter than VT323, cleaner at 10–12px. |
| **Pixelify Sans** | Larger type | Marginal | Less aggressive bitmap feel. |
| **IBM Plex Mono** | Body/narrative text | Yes | Real type, monospace, legible. OFL licensed. |
| **Share Tech Mono** | Body/narrative alternative | Yes | Warmer than IBM Plex. OFL. |

### Recommended Pairing
- **Headers/chrome/buttons:** Silkscreen (labels, stat names) + Press Start 2P sparingly (title/section)
- **Narrative/event log/advisor text:** IBM Plex Mono at 14–16px, line-height 1.6
- **Event log timestamps/codes:** VT323 at 18–20px

### CSS Rendering
```css
.font-pixel {
  -webkit-font-smoothing: none;
  -moz-osx-font-smoothing: grayscale; /* paradoxically sharper on macOS */
  font-smooth: never;
  text-rendering: optimizeSpeed;
}
```

Essential for Press Start 2P and Silkscreen — without them they render blurry on HiDPI.

### Google Fonts URLs (all OFL)
- Silkscreen, Press Start 2P, VT323, IBM Plex Mono — all on Google Fonts

## Pixel Icon Sets

### game-icons.net
Not pixel art — clean SVG line/fill art with unified aesthetic. Works at small sizes with `fill` color control via CSS. **CC BY 3.0.** 4000+ icons covering food, weapons, health, morale, weather. Visual language is compatible with the grim tactical theme.

### Integration (SVG Sprite)
```tsx
// icons/sprite.svg — combined <symbol> elements
function Icon({ id, size = 16 }: { id: string; size?: number }) {
  return (
    <svg width={size} height={size} aria-hidden>
      <use href={`/icons/sprite.svg#${id}`} />
    </svg>
  );
}
// Usage: <Icon id="wheat" size={16} />
```

Zero HTTP requests after first load, full CSS colorability, no JS bundle cost.

### For Actual Pixel-Art Icons
- [itch.io free pixel art icons](https://itch.io/game-assets/free/tag-icons/tag-pixel-art)
- [Kenney Game Icons](https://kenney.nl/assets/game-icons) — CC0, 105 icons, clean but not pixel art

**Recommendation:** game-icons.net for MVP; swap to pixel-art packs from itch.io if visual gap feels wrong.

## Dashboard Layout — Reference Games

### FTL
- Left 1/3: ship map (spatial status) — always visible
- Right 2/3: split between event narrative (top) and crew/systems (bottom)
- Principle: status is spatial, not tabular

**For Outpost:** the colony IS the map — agent portraits in persistent column, event narrative beside it.

### Papers Please
- Everything is a "document/object" on a desk — spatial, not widget-based
- Information comes to you rather than drilling into menus
- Moral weight comes from what you must look at — force the eye onto human element + data simultaneously

**For Outpost:** show agent portrait + event text in the same visual region.

### Into the Breach
- Tight information density, color + iconography reduce words
- Status communicated in 1–2 icons per unit, not prose

**For Outpost:** resource bars use icon + number + bar (3 signals), not label + number + bar.

### Mobile Collapse Strategy
Stacked with priority ordering:
1. Current event narrative + action choices (always visible)
2. Resource status (compact bar row)
3. Agent roster (accordion — tap to expand)
4. Event log (hidden behind tab/button)

CSS Grid with named areas, swap to single-column with `@media (max-width: 768px)`.

## Color Palette and CRT Atmosphere

### CSS Custom Properties Structure
```css
:root {
  /* Base (shared across all settings) */
  --color-bg:        #1a1a18;
  --color-surface:   #242420;
  --color-border:    #3a3a34;
  --color-text:      #c8c4b0;
  --color-text-dim:  #7a7a6e;
  --color-danger:    #8b2020;
  --color-warning:   #7a6020;
  --color-ok:        #2a5c2a;

  /* Setting accent — swap this one variable per setting */
  --color-accent:    #4a7c59;   /* forest green (default) */
}

[data-setting="arctic"] { --color-accent: #4a6c8c; }
[data-setting="space"]  { --color-accent: #6c4a8c; }
```

### CRT Scanline Overlay (pure CSS)
```css
.crt-overlay {
  pointer-events: none;
  position: fixed;
  inset: 0;
  z-index: 9999;
  background: repeating-linear-gradient(
    0deg,
    transparent,
    transparent 1px,
    rgba(0, 0, 0, 0.08) 1px,
    rgba(0, 0, 0, 0.08) 2px
  );
  animation: flicker 0.15s infinite;
}

@keyframes flicker {
  0%, 100% { opacity: 1; }
  92%       { opacity: 0.97; }
}
```

Keep scanline opacity 0.06–0.10 or it kills contrast.

### Living UI — Cheap Animations
```css
/* Blinking cursor */
.cursor::after {
  content: '▮';
  animation: blink 1s step-end infinite;
}
@keyframes blink { 50% { opacity: 0; } }

/* Resource bar — stepped = pixelated motion */
.resource-bar-fill {
  transition: width 400ms steps(20);
}

/* Status pulse */
.status-critical {
  animation: pulse 2s step-start infinite;
}
@keyframes pulse {
  0%, 49% { color: var(--color-danger); }
  50%, 100% { color: var(--color-text-dim); }
}
```

`steps()` easing is critical — makes transitions feel like pixel art frame animation.

### Contrast Note
Muted palette + dark background risks WCAG failure. Target 4.5:1 minimum. Palette above (`c8c4b0` on `1a1a18`) passes at ~9:1. Danger/warning on dark commonly fails — add text label alongside color-only indicators.

## Recommended Visual Stack

| Layer | Choice |
|-------|--------|
| Header font | Silkscreen |
| Display font | Press Start 2P — 1–3 word titles only |
| Body/narrative font | IBM Plex Mono 14px, line-height 1.6 |
| Terminal details | VT323 18px for event log timestamps, agent IDs |
| Agent portraits | `@dicebear/pixel-art`, `seed: agent.id`, `backgroundColor: transparent` |
| Agent roster rows | `@dicebear/thumbs` at 32px |
| Icons | game-icons.net SVGs via sprite sheet for MVP |
| CSS approach | shadcn/ui Level 2 re-theme (`--radius: 0`) + NES.css-pattern pixel borders as Tailwind utilities |
| Color system | CSS custom property tokens with `[data-setting]` swap |
| CRT effect | `repeating-linear-gradient` overlay, 0.08 opacity, `pointer-events: none` |
| Transitions | `steps()` easing on all animated values |

## Sources
- [DiceBear Pixel Art Style](https://www.dicebear.com/styles/pixel-art/)
- [DiceBear React Guide](https://www.dicebear.com/guides/use-the-library-with-react/)
- [shadcn/ui Theming](https://ui.shadcn.com/docs/theming)
- [NES.css](https://nostalgic-css.github.io/NES.css/)
- [CSS Pixel Art with box-shadow](https://medium.com/@gokhanozturk/css-pixel-art-with-box-shadow-e16fdabaf5fe)
- [Retro CRT terminal screen CSS](https://dev.to/ekeijl/retro-crt-terminal-screen-in-css-js-4afh)
- [FTL UI — Interface In Game](https://interfaceingame.com/games/ftl-faster-than-light/)
- [Papers Please UX Review](https://medium.com/@sam.cuevasp/papers-please-ux-review-672a151969e)
- [SVG sprites in React — Jacob Paris](https://www.jacobparis.com/content/svg-icons)
