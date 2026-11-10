import { Composition } from "remotion";
import { ShortVideo } from "./ShortVideo";
import { HEIGHT, WIDTH } from "./styles/layout";
import { ShortVideoPropsSchema } from "./types";

export const Root: React.FC = () => {
  return (
    <>
      <Composition
        id="ShortVideo"
        component={ShortVideo}
        width={WIDTH}
        height={HEIGHT}
        fps={30}
        durationInFrames={30 * 30}
        schema={ShortVideoPropsSchema}
        defaultProps={{
          clipSrc: "",
          sourceWidth: 1920,
          sourceHeight: 1080,
          layout: "fill" as const,
          crop: { x: 656, y: 0, w: 608, h: 1080 },
          cropKeyframes: [],
          panels: [],
          background: "blur" as const,
          captions: [],
          captionStyle: "bold" as const,
          hookLine1: "",
          hookLine2: "",
          showProgressBar: true,
          durationInSeconds: 30,
          fps: 30,
        }}
        calculateMetadata={({ props }) => {
          const fps = props.fps ?? 30;
          return {
            durationInFrames: Math.max(1, Math.round(props.durationInSeconds * fps)),
            fps,
            width: WIDTH,
            height: HEIGHT,
          };
        }}
      />
    </>
  );
};
