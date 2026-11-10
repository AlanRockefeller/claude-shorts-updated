import { useMemo } from "react";
import { createTikTokStyleCaptions } from "@remotion/captions";
import { useCurrentFrame, useVideoConfig } from "remotion";
import type { Caption } from "../types";

/**
 * Converts word-level captions into TikTok-style pages using
 * @remotion/captions createTikTokStyleCaptions().
 *
 * Each page groups N words together based on combineTokensWithinMilliseconds.
 * Returns pages with: text, startMs, durationMs, tokens[{text, fromMs, toMs}]
 */
export const useCaptionPages = (captions: Caption[], combineMs: number = 800) => {
  return useMemo(() => {
    if (!captions || captions.length === 0) {
      return { pages: [], emphasised: new Set<number>() };
    }

    const { pages } = createTikTokStyleCaptions({
      captions: captions.map((c) => ({
        text: c.text,
        startMs: c.startMs,
        endMs: c.endMs,
        timestampMs: null,
        confidence: null,
      })),
      combineTokensWithinMilliseconds: combineMs,
    });

    // Tokens keep their start time, so that is the key back to our metadata.
    const emphasised = new Set(captions.filter((c) => c.emphasis).map((c) => c.startMs));
    return { pages, emphasised };
  }, [captions, combineMs]);
};

/**
 * The caption page visible on the current frame, plus timing helpers shared by
 * every caption style.
 */
export const useCurrentCaptionPage = (captions: Caption[], combineMs: number) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const currentTimeMs = (frame / fps) * 1000;
  const { pages, emphasised } = useCaptionPages(captions, combineMs);

  const pageIndex = pages.findIndex(
    (p) => currentTimeMs >= p.startMs && currentTimeMs < p.startMs + p.durationMs
  );
  const page = pageIndex === -1 ? null : pages[pageIndex];
  const pageFrame = page ? frame - Math.floor((page.startMs / 1000) * fps) : 0;

  return { page, pageIndex, pageFrame, currentTimeMs, emphasised, fps };
};
