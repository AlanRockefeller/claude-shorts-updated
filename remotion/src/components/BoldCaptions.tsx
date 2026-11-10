import { spring } from "remotion";
import { useCurrentCaptionPage } from "../hooks/useCaptionPages";
import { BOLD_THEME } from "../styles/theme";
import type { Caption } from "../types";

/**
 * Bold-style captions: ALL CAPS, pop-in spring animation,
 * yellow highlight on the currently active word.
 *
 * Font: Montserrat Bold
 * Words per page: 2-3 (800ms combine window)
 * Animation: Pop-in spring {mass:1, damping:12, stiffness:200}
 */
export const BoldCaptions: React.FC<{ captions: Caption[] }> = ({ captions }) => {
  const { page, pageFrame, currentTimeMs, emphasised, fps } = useCurrentCaptionPage(
    captions,
    800
  );
  if (!page) return null;

  const scale = spring({
    frame: pageFrame,
    fps,
    config: { mass: 1, damping: 12, stiffness: 200 },
  });
  const outline = BOLD_THEME.shadowColor;

  return (
    <div
      style={{
        display: "flex",
        justifyContent: "center",
        alignItems: "center",
        flexWrap: "wrap",
        columnGap: 18,
        rowGap: 4,
        transform: `scale(${scale})`,
      }}
    >
      {page.tokens.map((token, i) => {
        const isActive = currentTimeMs >= token.fromMs && currentTimeMs < token.toMs;
        const color = isActive
          ? BOLD_THEME.activeColor
          : emphasised.has(token.fromMs)
            ? BOLD_THEME.emphasisColor
            : BOLD_THEME.textColor;

        return (
          <span
            key={`${page.startMs}-${i}`}
            style={{
              fontFamily: "'Montserrat', sans-serif",
              fontWeight: 800,
              fontSize: 72,
              textTransform: "uppercase",
              color,
              textShadow: `3px 3px 0 ${outline}, -3px -3px 0 ${outline}, 3px -3px 0 ${outline}, -3px 3px 0 ${outline}, 0 6px 14px rgba(0,0,0,0.55)`,
              lineHeight: 1.15,
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
