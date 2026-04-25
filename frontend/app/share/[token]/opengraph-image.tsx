import { ImageResponse } from "next/og";

import { API_BASE_URL, type PostMortem } from "@/lib/api/client";

export const runtime = "nodejs";
export const alt = "Outpost — shared run";
// Twitter card 'summary_large_image', Discord, Bluesky — all unfurl 1200×630
// most consistently. SPEC §11.3 calls for a "compact square" but cross-
// platform unfurl behavior with square images is uneven (Twitter crops to
// fit; Bluesky may stretch). Going wide is the cross-platform safe choice;
// the 12 tile strip + opening/closing lines fit cleanly here.
export const size = { width: 1200, height: 630 };
export const contentType = "image/png";

const PALETTE = {
  bg: "#1a1a18",
  surface: "#252521",
  border: "#3d3d36",
  text: "#e8e4d6",
  textDim: "#968f7d",
  accentArctic: "#4a6c8c",
  accentOrbital: "#6c4a8c",
  accentDesert: "#8c4a4a",
  ok: "#7ea05a",
  warning: "#c89a4a",
  danger: "#a74a4a",
} as const;

const FLAVOR_COLOR: Record<string, string> = {
  DOMINANT: PALETTE.text,
  MORAL: PALETTE.ok,
  WILDCARD: PALETTE.warning,
};

const END_STATE_COLOR: Record<string, string> = {
  ENDURED: PALETTE.ok,
  COLLAPSED: PALETTE.warning,
  OUSTED: PALETTE.warning,
  KILLED: PALETTE.danger,
};

function settingAccent(setting_id: string): string {
  if (setting_id.startsWith("orbital")) return PALETTE.accentOrbital;
  if (setting_id.startsWith("desert")) return PALETTE.accentDesert;
  return PALETTE.accentArctic;
}

async function loadShare(token: string): Promise<PostMortem | null> {
  // Dev-only: `/share/dev` and `/share/dev-rich` hydrate from the static
  // fixture so the OG image can be iterated offline without running the
  // backend. Stripped from production builds via NODE_ENV gate.
  if (process.env.NODE_ENV !== "production") {
    if (token === "dev") {
      const f = await import("@/lib/dev/postMortemFixture.json");
      return (f.default ?? f) as unknown as PostMortem;
    }
    if (token === "dev-rich") {
      const f = await import("@/lib/dev/postMortemFixtureRich.json");
      return (f.default ?? f) as unknown as PostMortem;
    }
  }
  const res = await fetch(`${API_BASE_URL}/game/share/${token}`, {
    cache: "no-store",
  });
  if (!res.ok) return null;
  return (await res.json()) as PostMortem;
}

/**
 * Per-run social share card.
 *
 * Layout, top to bottom:
 *   - brand line ("OUTPOST" + setting name)
 *   - end-state badge (large, color-coded)
 *   - 12 flavor tiles (one per turn proposal flavor — DOMINANT/MORAL/WILDCARD).
 *     Empty slots stay dim so 8-turn vs 12-turn runs are visibly different.
 *   - opening line (italic, dim)
 *   - closing line (italic, full color)
 *
 * No external fonts loaded — Satori's default is good enough for a 1200×630
 * card and avoids a per-fetch font load that slows the OG endpoint.
 */
export default async function ShareOgImage({
  params,
}: {
  params: Promise<{ token: string }>;
}) {
  const { token } = await params;
  const data = await loadShare(token);
  if (!data) {
    return new ImageResponse(<FallbackCard />, size);
  }
  const accent = settingAccent(data.setting_id);
  const endStateColor = END_STATE_COLOR[data.end_state] ?? PALETTE.text;
  const tiles = buildTiles(data, 12);

  return new ImageResponse(
    (
      <div
        style={{
          width: "100%",
          height: "100%",
          display: "flex",
          flexDirection: "column",
          background: PALETTE.bg,
          color: PALETTE.text,
          padding: "60px 70px",
          fontFamily: "monospace",
        }}
      >
        {/* Brand strip */}
        <div
          style={{
            display: "flex",
            alignItems: "baseline",
            gap: 24,
            color: PALETTE.textDim,
            fontSize: 22,
            letterSpacing: 6,
            textTransform: "uppercase",
          }}
        >
          <span style={{ color: accent }}>OUTPOST</span>
          <span>·</span>
          <span>{data.setting_name}</span>
        </div>

        {/* End-state badge */}
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 24,
            marginTop: 28,
          }}
        >
          <span
            style={{
              width: 28,
              height: 28,
              background: endStateColor,
            }}
          />
          <span
            style={{
              fontSize: 80,
              letterSpacing: 4,
              color: endStateColor,
              textTransform: "uppercase",
            }}
          >
            {data.end_state}
          </span>
          <span
            style={{
              marginLeft: 24,
              fontSize: 28,
              color: PALETTE.textDim,
              letterSpacing: 4,
              textTransform: "uppercase",
            }}
          >
            d{String(data.total_turns).padStart(2, "0")}
          </span>
        </div>

        {/* 12 tiles */}
        <div
          style={{
            display: "flex",
            gap: 12,
            marginTop: 40,
          }}
        >
          {tiles.map((color, i) => (
            <span
              key={i}
              style={{
                width: 64,
                height: 64,
                background: color ?? PALETTE.surface,
                border: `2px solid ${color ? PALETTE.border : PALETTE.border}`,
              }}
            />
          ))}
        </div>

        {/* Opening + closing lines */}
        <div
          style={{
            display: "flex",
            flexDirection: "column",
            gap: 18,
            marginTop: "auto",
            maxWidth: 1060,
          }}
        >
          {data.opening_line ? (
            <p
              style={{
                fontSize: 26,
                color: PALETTE.textDim,
                fontStyle: "italic",
                lineHeight: 1.35,
                margin: 0,
              }}
            >
              “{truncate(data.opening_line, 140)}”
            </p>
          ) : null}
          {data.closing_line ? (
            <p
              style={{
                fontSize: 30,
                color: PALETTE.text,
                fontStyle: "italic",
                lineHeight: 1.35,
                margin: 0,
              }}
            >
              “{truncate(data.closing_line, 140)}”
            </p>
          ) : null}
        </div>
      </div>
    ),
    size,
  );
}

function FallbackCard() {
  return (
    <div
      style={{
        width: "100%",
        height: "100%",
        display: "flex",
        flexDirection: "column",
        background: PALETTE.bg,
        color: PALETTE.text,
        padding: "120px 90px",
        fontFamily: "monospace",
      }}
    >
      <span
        style={{
          fontSize: 28,
          letterSpacing: 6,
          color: PALETTE.accentArctic,
        }}
      >
        OUTPOST
      </span>
      <span
        style={{
          marginTop: 30,
          fontSize: 56,
          letterSpacing: 2,
          color: PALETTE.text,
        }}
      >
        A daily narrative roguelike.
      </span>
      <span
        style={{
          marginTop: 12,
          fontSize: 30,
          color: PALETTE.textDim,
        }}
      >
        Survive a run.
      </span>
    </div>
  );
}

function buildTiles(data: PostMortem, slots: number): (string | null)[] {
  const tiles: (string | null)[] = new Array(slots).fill(null);
  for (let i = 0; i < data.turns.length && i < slots; i++) {
    const turn = data.turns[i];
    const chosen = turn.proposals.find((p) => p.id === turn.chosen_proposal_id);
    tiles[i] = chosen ? (FLAVOR_COLOR[chosen.flavor] ?? PALETTE.text) : null;
  }
  return tiles;
}

function truncate(s: string, n: number): string {
  return s.length > n ? `${s.slice(0, n - 1).trimEnd()}…` : s;
}
