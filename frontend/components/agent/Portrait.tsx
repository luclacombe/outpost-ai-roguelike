"use client";

import { useMemo } from "react";
import { createAvatar } from "@dicebear/core";
// DiceBear v9 per-style packages export { create, meta, schema };
// the namespace object satisfies the `Style<O>` shape that createAvatar wants.
import * as pixelArt from "@dicebear/pixel-art";
import * as thumbs from "@dicebear/thumbs";

import { cn } from "@/lib/utils";

export type Setting = "arctic" | "orbital" | "desert";

type Props = {
  seed: string;
  setting: Setting;
  size?: number;
  /** Use the simpler "thumbs" style. Suited for small (≤32px) roster rows. */
  variant?: "portrait" | "thumb";
  className?: string;
  alt?: string;
};

/**
 * Per-setting tone overrides. Hex strings *without* leading "#"
 * — DiceBear's option schema requires the bare value.
 */
const SETTING_OVERRIDES: Record<Setting, { clothingColor: string[] }> = {
  arctic: { clothingColor: ["3c4f5c", "2a3a4a", "5c6c7c"] },
  orbital: { clothingColor: ["4a4a5c", "3c3c4a", "5c4a6c"] },
  desert: { clothingColor: ["8c6a4a", "7a5a3a", "6c4a3a"] },
};

const PORTRAIT_BG = ["1a1a18"];

export function Portrait({
  seed,
  setting,
  size = 64,
  variant = "portrait",
  className,
  alt = "",
}: Props) {
  const dataUri = useMemo(() => {
    // Each style has its own Options union; createAvatar's generic narrows on
    // the literal style argument, so branch instead of using a ternary that
    // would collapse to a wider union TS can't satisfy.
    const svg =
      variant === "portrait"
        ? createAvatar(pixelArt, {
            seed,
            size,
            backgroundColor: PORTRAIT_BG,
            ...SETTING_OVERRIDES[setting],
          }).toString()
        : createAvatar(thumbs, {
            seed,
            size,
            backgroundColor: PORTRAIT_BG,
          }).toString();

    return `data:image/svg+xml;utf8,${encodeURIComponent(svg)}`;
  }, [seed, setting, size, variant]);

  return (
    /* eslint-disable-next-line @next/next/no-img-element */
    <img
      src={dataUri}
      alt={alt}
      width={size}
      height={size}
      className={cn("pixel block", className)}
    />
  );
}
