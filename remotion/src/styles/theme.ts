/**
 * Color palettes and style constants for each caption preset.
 */

export const BOLD_THEME = {
  textColor: "#FFFFFF",
  activeColor: "#FFD700", // Yellow highlight on the word being spoken
  emphasisColor: "#3DFF7A", // Key words picked during caption review
  shadowColor: "#000000",
};

export const BOUNCE_THEME = {
  textColor: "#FFFFFF",
  shadowColor: "#000000",
  // Rotating bright colors per page
  rotatingColors: [
    "#00FFFF", // Cyan
    "#FF00FF", // Magenta
    "#00FF00", // Lime
    "#FFFF00", // Yellow
    "#FF6600", // Orange
    "#FF0066", // Hot pink
  ],
};

export const CLEAN_THEME = {
  textColor: "rgba(255, 255, 255, 0.72)", // Words not currently spoken
  activeColor: "#FFFFFF",
  emphasisColor: "#FFD84D",
  shadowColor: "rgba(0, 0, 0, 0.7)",
};
