import { interpolate } from "remotion";
import { useCurrentCaptionPage } from "../hooks/useCaptionPages";
import { CLEAN_THEME } from "../styles/theme";
import type { Caption } from "../types";

/**
 * Clean-style captions: Minimal fade-in, white text with subtle shadow,
 * 3-5 words per page for readability.
 *
 * Font: Inter Bold
 * Words per page: 3-5 (1500ms combine window)
 * Animation: Fade-in opacity 0 → 1 over 6 frames (at 30 fps)
 */
export const CleanCaptions: React.FC<{ captions: Caption[] }> = ({ captions }) => {
  const { page, pageFrame, currentTimeMs, emphasised, fps } = useCurrentCaptionPage(
    captions,
    1500
  );
  if (!page) return null;

  const opacity = interpolate(pageFrame, [0, fps / 5], [0, 1], {
    extrapolateRight: "clamp",
  });

  return (
    <div
      style={{
        display: "flex",
        justifyContent: "center",
        alignItems: "center",
        flexWrap: "wrap",
        columnGap: 14,
        opacity,
      }}
    >
      {page.tokens.map((token, i) => {
        const isActive = currentTimeMs >= token.fromMs && currentTimeMs < token.toMs;
        const color = emphasised.has(token.fromMs)
          ? CLEAN_THEME.emphasisColor
          : isActive
            ? CLEAN_THEME.activeColor
            : CLEAN_THEME.textColor;

        return (
          <span
            key={`${page.startMs}-${i}`}
            style={{
              fontFamily: "'Inter', sans-serif",
              fontWeight: 700,
              fontSize: 56,
              color,
              textShadow: `0 2px 10px ${CLEAN_THEME.shadowColor}, 0 0 3px ${CLEAN_THEME.shadowColor}`,
              lineHeight: 1.3,
              textAlign: "center",
            }}
          >
            {token.text.trim()}
          </span>
        );
      })}
    </div>
  );
};
