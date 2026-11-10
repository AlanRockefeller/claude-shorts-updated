import { z } from "zod";

export const CaptionSchema = z.object({
  text: z.string(),
  startMs: z.number(),
  endMs: z.number(),
  /** Key word picked during caption review; rendered in the accent colour. */
  emphasis: z.boolean().optional(),
});

export type Caption = z.infer<typeof CaptionSchema>;

export const CropSchema = z.object({
  x: z.number(),
  y: z.number(),
  w: z.number(),
  h: z.number(),
});

export type Crop = z.infer<typeof CropSchema>;

export const CropKeyframeSchema = z.object({
  t: z.number(),
  x: z.number(),
});

export type CropKeyframe = z.infer<typeof CropKeyframeSchema>;

export const PanelSchema = z.object({ crop: CropSchema });
export type Panel = z.infer<typeof PanelSchema>;

export const CaptionStyle = z.enum(["bold", "bounce", "clean"]);
export type CaptionStyleType = z.infer<typeof CaptionStyle>;

/**
 * fill   - one crop covers the whole 9:16 frame (talking head)
 * framed - zoomed content inset on a background (screen recordings)
 * split  - two stacked crops, one per speaker (podcasts)
 */
export const Layout = z.enum(["fill", "framed", "split"]);
export type LayoutType = z.infer<typeof Layout>;

export const Background = z.enum(["blur", "black"]);
export type BackgroundType = z.infer<typeof Background>;

export const ShortVideoPropsSchema = z.object({
  clipSrc: z.string(),
  sourceWidth: z.number(),
  sourceHeight: z.number(),
  layout: Layout.optional().default("fill"),
  crop: CropSchema,
  cropKeyframes: z.array(CropKeyframeSchema).optional().default([]),
  panels: z.array(PanelSchema).optional().default([]),
  background: Background.optional().default("blur"),
  captions: z.array(CaptionSchema),
  captionStyle: CaptionStyle,
  hookLine1: z.string().optional().default(""),
  hookLine2: z.string().optional().default(""),
  showProgressBar: z.boolean().optional().default(true),
  durationInSeconds: z.number(),
  fps: z.number().optional().default(30),
});

export type ShortVideoProps = z.infer<typeof ShortVideoPropsSchema>;
