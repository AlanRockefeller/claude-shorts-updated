import { spring } from "remotion";
import { useCurrentCaptionPage } from "../hooks/useCaptionPages";
import { BOUNCE_THEME } from "../styles/theme";
import type { Caption } from "../types";

/**
 * Bounce-style captions: Bouncy scale spring, rotating bright colors,
 * 1-2 words per page for maximum impact.
 *
 * Font: Bangers (Google Fonts, OFL)
 * Words per page: 1-2 (600ms combine window)
 * Animation: Scale spring 70% → 120% → 100% {mass:1, damping:8}
 */
export const BounceCaptions: React.FC<{ captions: Caption[] }> = ({ captions }) => {
  const { page, pageIndex, pageFrame, emphasised, fps } = useCurrentCaptionPage(captions, 600);
  if (!page) return null;

  // Bouncy scale spring: starts at 0.7, overshoots, settles at 1.0
  const rawScale = spring({
    frame: pageFrame,
    fps,
    config: { mass: 1, damping: 8, stiffness: 180 },
  });
  const hasEmphasis = page.tokens.some((t) => emphasised.has(t.fromMs));
  const scale = (0.7 + rawScale * 0.3) * (hasEmphasis ? 1.15 : 1);

  const color = BOUNCE_THEME.rotatingColors[pageIndex % BOUNCE_THEME.rotatingColors.length];
  const outline = BOUNCE_THEME.shadowColor;

  return (
    <span
      style={{
        fontFamily: "'Bangers', cursive",
        fontSize: 84,
        color,
        textShadow: `4px 4px 0 ${outline}, -4px -4px 0 ${outline}, 4px -4px 0 ${outline}, -4px 4px 0 ${outline}`,
        textAlign: "center",
        lineHeight: 1.05,
        textTransform: "uppercase",
        transform: `scale(${scale})`,
      }}
    >
      {page.text.trim()}
    </span>
  );
};
