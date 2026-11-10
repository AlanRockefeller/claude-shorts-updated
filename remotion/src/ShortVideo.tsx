import { AbsoluteFill } from "remotion";
import { VideoFrame } from "./components/VideoFrame";
import { Captions } from "./components/Captions";
import { HookOverlay } from "./components/HookOverlay";
import { ProgressBar } from "./components/ProgressBar";
import { fontFaceCSS } from "./styles/fonts";
import type { ShortVideoProps } from "./types";

export const ShortVideo: React.FC<ShortVideoProps> = ({
  clipSrc,
  sourceWidth,
  sourceHeight,
  layout,
  crop,
  cropKeyframes,
  panels,
  background,
  captions,
  captionStyle,
  hookLine1,
  hookLine2,
  showProgressBar,
}) => {
  return (
    <AbsoluteFill style={{ backgroundColor: "black" }}>
      {/* Load custom fonts for captions and hook overlay */}
      <style dangerouslySetInnerHTML={{ __html: fontFaceCSS }} />

      {/* Reframed video (cropped and scaled into the 1080x1920 frame) */}
      <VideoFrame
        clipSrc={clipSrc}
        sourceWidth={sourceWidth}
        sourceHeight={sourceHeight}
        layout={layout}
        crop={crop}
        cropKeyframes={cropKeyframes}
        panels={panels}
        background={background}
      />

      {/* Animated captions */}
      <Captions captions={captions} style={captionStyle} layout={layout} />

      {/* Hook text overlay (first 3.5 seconds) */}
      {(hookLine1 || hookLine2) && (
        <HookOverlay line1={hookLine1 ?? ""} line2={hookLine2 ?? ""} />
      )}

      {/* Progress bar at bottom */}
      {showProgressBar && <ProgressBar />}
    </AbsoluteFill>
  );
};
