import { useCurrentFrame, useVideoConfig, spring, interpolate } from "remotion";
import { SAFE } from "../styles/layout";

interface HookOverlayProps {
  line1: string;
  line2: string;
}

/**
 * Hook text overlay shown in the first 3.5 seconds, placed below the
 * platform header area so it is not covered by app UI.
 *
 * Line 1: Large white text (main hook)
 * Line 2: Smaller cyan text (subtitle/context)
 *
 * Animation: Spring pop-in at 0.2s, fade-out at 3.0s
 */
export const HookOverlay: React.FC<HookOverlayProps> = ({ line1, line2 }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const currentTimeSec = frame / fps;

  if (currentTimeSec < 0.2 || currentTimeSec > 3.5) return null;

  const scale = spring({
    frame: Math.max(0, frame - Math.floor(0.2 * fps)),
    fps,
    config: { mass: 1, damping: 14, stiffness: 200 },
  });

  const opacity = interpolate(currentTimeSec, [3.0, 3.5], [1, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  return (
    <div
      style={{
        position: "absolute",
        top: SAFE.top,
        left: SAFE.left,
        right: SAFE.right,
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        gap: 10,
        opacity,
        transform: `scale(${scale})`,
      }}
    >
      {line1 && (
        <div
          style={{
            fontFamily: "'Montserrat', sans-serif",
            fontWeight: 800,
            fontSize: 58,
            color: "white",
            textShadow:
              "3px 3px 0 black, -3px -3px 0 black, 3px -3px 0 black, -3px 3px 0 black, 0 6px 16px rgba(0,0,0,0.6)",
            textAlign: "center",
            lineHeight: 1.15,
          }}
        >
          {line1}
        </div>
      )}
      {line2 && (
        <div
          style={{
            fontFamily: "'Inter', sans-serif",
            fontWeight: 700,
            fontSize: 36,
            color: "#35CFFF",
            textShadow: "2px 2px 0 black, -2px -2px 0 black, 2px -2px 0 black, -2px 2px 0 black",
            textAlign: "center",
            lineHeight: 1.25,
          }}
        >
          {line2}
        </div>
      )}
    </div>
  );
};
