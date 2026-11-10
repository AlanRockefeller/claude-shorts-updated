import { AbsoluteFill, Img, OffthreadVideo, Sequence, interpolate, useCurrentFrame, useVideoConfig } from "remotion";
import type { Broll } from "../types";

const FADE_FRAMES = 4;

const BrollItem: React.FC<{ item: Broll; frames: number }> = ({ item, frames }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  // Quick fade in and out so the cutaway does not pop.
  const opacity = interpolate(
    frame,
    [0, FADE_FRAMES, Math.max(FADE_FRAMES + 1, frames - FADE_FRAMES), Math.max(FADE_FRAMES + 2, frames)],
    [0, 1, 1, 0],
    { extrapolateLeft: "clamp", extrapolateRight: "clamp" }
  );
  const fill: React.CSSProperties = { width: "100%", height: "100%", objectFit: "cover" };

  return (
    <AbsoluteFill style={{ backgroundColor: "black", opacity }}>
      {item.kind === "video" ? (
        <OffthreadVideo
          src={item.src}
          muted
          trimBefore={Math.round((item.sourceStart ?? 0) * fps)}
          style={fill}
        />
      ) : (
        // Slow push-in keeps a still image from looking frozen.
        <Img
          src={item.src}
          style={{ ...fill, transform: `scale(${1 + 0.08 * (frame / Math.max(1, frames))})` }}
        />
      )}
    </AbsoluteFill>
  );
};

/**
 * B-roll cutaways: full-frame video or stills shown over the speaker for a
 * few seconds while their audio continues. Captions render on top.
 */
export const BrollLayer: React.FC<{ items: Broll[] }> = ({ items }) => {
  const { fps } = useVideoConfig();
  return (
    <>
      {items.map((item, i) => {
        const frames = Math.max(1, Math.round(item.duration * fps));
        return (
          <Sequence key={i} from={Math.round(item.start * fps)} durationInFrames={frames} layout="none">
            <BrollItem item={item} frames={frames} />
          </Sequence>
        );
      })}
    </>
  );
};
