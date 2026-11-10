import type { LayoutType } from "../types";

/**
 * Canvas and platform safe zones (1080x1920).
 *
 * TikTok, YouTube Shorts and Instagram Reels all draw UI over the video:
 * a header at the top, the caption/description at the bottom and a column of
 * buttons on the lower right. Text placed outside these margins gets covered.
 * See references/platform-specs.md for the per-platform measurements.
 */
export const WIDTH = 1080;
export const HEIGHT = 1920;

export const SAFE = {
  top: 220,
  bottom: 440,
  left: 60,
  right: 60,
  /** Extra right-hand margin in the lower half, where the action buttons sit. */
  buttonColumn: 110,
};

/** Top edge of the inset video in the framed (screen recording) layout. */
export const FRAMED_TOP = 300;

/** Where captions sit for each layout. */
export const captionPlacement = (
  layout: LayoutType
): { top?: number; bottom?: number } => {
  if (layout === "split") {
    // Across the seam between the two speaker panels.
    return { top: HEIGHT / 2 - 90 };
  }
  return { bottom: SAFE.bottom };
};
