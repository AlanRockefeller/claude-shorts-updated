import { AbsoluteFill, OffthreadVideo, useCurrentFrame, useVideoConfig, interpolate } from "remotion";
import { FRAMED_TOP, HEIGHT, WIDTH } from "../styles/layout";
import type { BackgroundType, Crop, CropKeyframe, LayoutType, Panel } from "../types";

/** Zoom towards the upper-middle of the frame, where faces usually are. */
const PUNCH_ORIGIN = "50% 38%";

interface VideoFrameProps {
  clipSrc: string;
  sourceWidth: number;
  sourceHeight: number;
  layout: LayoutType;
  crop: Crop;
  cropKeyframes?: CropKeyframe[];
  panels?: Panel[];
  background: BackgroundType;
  cropMode?: "pan" | "cut";
  punchIns?: number[];
  punchZoom?: number;
}

interface CroppedVideoProps {
  clipSrc: string;
  sourceWidth: number;
  sourceHeight: number;
  crop: Crop;
  /** Size of the window the crop is shown in. */
  width: number;
  height: number;
  top: number;
  muted: boolean;
}

/**
 * Shows one crop of the source video in a window. The source is scaled so the
 * crop covers the window, then shifted so the crop sits at the window origin.
 */
const CroppedVideo: React.FC<CroppedVideoProps> = ({
  clipSrc,
  sourceWidth,
  sourceHeight,
  crop,
  width,
  height,
  top,
  muted,
}) => {
  const scale = Math.max(width / crop.w, height / crop.h);
  // Centre the crop if it is slightly larger than the window after scaling.
  const offsetX = (crop.w * scale - width) / 2;
  const offsetY = (crop.h * scale - height) / 2;

  return (
    <div style={{ position: "absolute", top, left: 0, width, height, overflow: "hidden" }}>
      <OffthreadVideo
        src={clipSrc}
        muted={muted}
        style={{
          position: "absolute",
          width: sourceWidth * scale,
          height: sourceHeight * scale,
          maxWidth: "none",
          transform: `translate(${-crop.x * scale - offsetX}px, ${-crop.y * scale - offsetY}px)`,
        }}
      />
    </div>
  );
};

/**
 * Renders the reframed source video.
 *
 * - fill:   one crop covers the whole frame; crop.x may pan via keyframes
 * - framed: zoomed crop inset near the top, over a blurred or black backdrop
 * - split:  two stacked crops (one per speaker)
 */
export const VideoFrame: React.FC<VideoFrameProps> = ({
  clipSrc,
  sourceWidth,
  sourceHeight,
  layout,
  crop,
  cropKeyframes,
  panels,
  background,
  cropMode = "pan",
  punchIns = [],
  punchZoom = 1.08,
}) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  if (!clipSrc) return null;

  const common = { clipSrc, sourceWidth, sourceHeight };
  const now = frame / fps;

  // Alternate between normal and punched-in framing at each marked cut.
  const punched = punchIns.filter((t) => t <= now + 1e-6).length % 2 === 1;
  const punchStyle: React.CSSProperties = {
    transform: `scale(${punched ? punchZoom : 1})`,
    transformOrigin: PUNCH_ORIGIN,
  };

  if (layout === "split" && panels && panels.length >= 2) {
    const panelHeight = HEIGHT / 2;
    return (
      <AbsoluteFill>
        <AbsoluteFill style={punchStyle}>
          <CroppedVideo {...common} crop={panels[0].crop} width={WIDTH} height={panelHeight} top={0} muted={false} />
          <CroppedVideo {...common} crop={panels[1].crop} width={WIDTH} height={panelHeight} top={panelHeight} muted />
        </AbsoluteFill>
        <div
          style={{
            position: "absolute",
            top: panelHeight - 3,
            left: 0,
            width: WIDTH,
            height: 6,
            backgroundColor: "black",
          }}
        />
      </AbsoluteFill>
    );
  }

  // Moving crop: glide (face or cursor tracking) or jump (speaker switching)
  let cropX = crop.x;
  if (cropKeyframes && cropKeyframes.length >= 2) {
    if (cropMode === "cut") {
      for (const key of cropKeyframes) {
        if (key.t <= now + 1e-6) cropX = key.x;
      }
    } else {
      cropX = interpolate(
        now,
        cropKeyframes.map((k) => k.t),
        cropKeyframes.map((k) => k.x),
        { extrapolateLeft: "clamp", extrapolateRight: "clamp" }
      );
    }
  }
  const activeCrop = { ...crop, x: cropX };

  if (layout === "framed") {
    const insetHeight = Math.min(Math.round((crop.h * WIDTH) / crop.w), HEIGHT - FRAMED_TOP);
    const cover = Math.max(WIDTH / sourceWidth, HEIGHT / sourceHeight) * 1.15;
    return (
      <AbsoluteFill>
        {background === "blur" && (
          <OffthreadVideo
            src={clipSrc}
            muted
            style={{
              position: "absolute",
              width: sourceWidth * cover,
              height: sourceHeight * cover,
              maxWidth: "none",
              left: (WIDTH - sourceWidth * cover) / 2,
              top: (HEIGHT - sourceHeight * cover) / 2,
              filter: "blur(36px) brightness(0.45)",
            }}
          />
        )}
        <CroppedVideo {...common} crop={activeCrop} width={WIDTH} height={insetHeight} top={FRAMED_TOP} muted={false} />
      </AbsoluteFill>
    );
  }

  return (
    <AbsoluteFill style={punchStyle}>
      <CroppedVideo {...common} crop={activeCrop} width={WIDTH} height={HEIGHT} top={0} muted={false} />
    </AbsoluteFill>
  );
};
