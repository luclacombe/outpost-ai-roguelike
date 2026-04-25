/**
 * CRT scanline overlay. Fixed full-screen, pointer-events: none, low-opacity.
 *
 * The 0.08 scanline opacity is the upper bound — at 0.10+ the dimmer body text
 * (`var(--px-text-dim)`) drops below WCAG AA on `var(--px-bg)`. Don't bump it
 * without re-running a contrast check on the dimmer text first.
 *
 * Mounted once near the root of any surface that wants the look — currently
 * the (authed) layout (real run pages) and the /dev/dashboard preview.
 */
export function CrtOverlay() {
  return <div aria-hidden className="crt-overlay" />;
}
