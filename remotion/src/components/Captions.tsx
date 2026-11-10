import type { Caption, CaptionStyleType, LayoutType } from "../types";
import { captionPlacement, SAFE } from "../styles/layout";
import { BoldCaptions } from "./BoldCaptions";
import { BounceCaptions } from "./BounceCaptions";
import { CleanCaptions } from "./CleanCaptions";

interface CaptionsProps {
  captions: Caption[];
  style: CaptionStyleType;
  layout: LayoutType;
}

/**
 * Positions the caption block inside the platform safe zone for the current
 * layout, then routes to the selected caption style.
 */
export const Captions: React.FC<CaptionsProps> = ({ captions, style, layout }) => {
  if (!captions || captions.length === 0) return null;

  const Style =
    style === "bounce" ? BounceCaptions : style === "clean" ? CleanCaptions : BoldCaptions;

  return (
    <div
      style={{
        position: "absolute",
        left: SAFE.left + 20,
        right: SAFE.buttonColumn,
        display: "flex",
        justifyContent: "center",
        ...captionPlacement(layout),
      }}
    >
      <Style captions={captions} />
    </div>
  );
};
