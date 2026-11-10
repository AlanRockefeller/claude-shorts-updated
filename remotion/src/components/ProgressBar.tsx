import { useCurrentFrame, useVideoConfig } from "remotion";
import { WIDTH } from "../styles/layout";

/**
 * Thin progress bar at the bottom of the video.
 * White bar that fills left-to-right over the video duration.
 */
export const ProgressBar: React.FC = () => {
  const frame = useCurrentFrame();
  const { durationInFrames } = useVideoConfig();
  const progress = Math.min(frame / Math.max(1, durationInFrames - 1), 1);

  return (
    <div
      style={{
        position: "absolute",
        bottom: 0,
        left: 0,
        width: WIDTH,
        height: 6,
        backgroundColor: "rgba(255, 255, 255, 0.2)",
      }}
    >
      <div style={{ width: WIDTH * progress, height: 6, backgroundColor: "white" }} />
    </div>
  );
};
