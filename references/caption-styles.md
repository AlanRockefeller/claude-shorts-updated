# Caption Styles — Visual Specifications

Three premium caption presets for different content tones. Each uses `@remotion/captions` createTikTokStyleCaptions() for word-level timing.

## Bold Style

Best for: Business, education, motivation, "guru" content.

| Property | Value |
|----------|-------|
| Font | Montserrat Bold (800 weight) |
| Font Size | 72px |
| Text Transform | UPPERCASE |
| Words Per Page | 2-3 |
| Combine Window | 800ms |
| Text Color | White (#FFFFFF) |
| Active Word | Yellow (#FFD700) |
| Emphasis Word | Green (#3DFF7A) |
| Text Shadow | 3px solid black outline (all 4 directions) |
| Position | Bottom: 440px from bottom edge (see Caption Positioning) |
| Animation | Pop-in spring: {mass:1, damping:12, stiffness:200} |
| Timing | Page appears at first word, disappears after last word |

**Visual reference**: All caps, word-by-word yellow highlight, bold pop-in entrance.

**Spring config explained**:
- `damping: 12` — moderate bounce, settles in ~0.4s
- `stiffness: 200` — snappy entrance
- Scale goes from 0 → slight overshoot (~1.05) → 1.0

## Bounce Style

Best for: Entertainment, reactions, challenges, high-energy content.

| Property | Value |
|----------|-------|
| Font | Bangers (Google Fonts, OFL) |
| Font Size | 84px |
| Text Transform | UPPERCASE |
| Words Per Page | 1-2 |
| Combine Window | 600ms |
| Text Color | Rotating (see below) |
| Text Shadow | 4px solid black outline (all 4 directions) |
| Position | Bottom: 440px from bottom edge (see Caption Positioning) |
| Animation | Bouncy scale: 70% → ~120% → 100% |
| Spring Config | {mass:1, damping:8, stiffness:180} |

**Color rotation** (per page, cycling):
1. Cyan (#00FFFF)
2. Magenta (#FF00FF)
3. Lime (#00FF00)
4. Yellow (#FFFF00)
5. Orange (#FF6600)
6. Hot Pink (#FF0066)

**Spring config explained**:
- `damping: 8` — very bouncy, overshoots significantly
- Scale: starts at 0.7, springs up to ~1.2, settles at 1.0
- Creates the "explosive word pop" effect

## Clean Style

Best for: Professional, interviews, calm content, podcasts.

| Property | Value |
|----------|-------|
| Font | Inter Bold (700 weight) |
| Font Size | 56px |
| Text Transform | None (preserves original case) |
| Words Per Page | 3-5 |
| Combine Window | 1500ms |
| Text Color | White at 72% opacity |
| Active Word | Full white (#FFFFFF) |
| Emphasis Word | Warm yellow (#FFD84D) |
| Text Shadow | Soft dark glow, rgba(0,0,0,0.7) |
| Position | Bottom: 440px from bottom edge (see Caption Positioning) |
| Animation | Fade-in opacity 0→1 over 6 frames (200ms at 30fps) |

**Visual reference**: Minimal, readable, doesn't distract from the speaker. The word being spoken is slightly brighter than the rest; no bold contrast.

## Font Files

All fonts must be placed in `remotion/public/fonts/`:
- `Montserrat-Bold.ttf` — from Google Fonts (OFL license)
- `Bangers-Regular.ttf` — from Google Fonts (OFL license)
- `Inter-Bold.ttf` — from Google Fonts (OFL license)

Download from: https://fonts.google.com/

## Emphasis Words

During caption review Claude can mark key words (`"emphasis": [word indexes]` in
`caption_edits.json`). They render in the style's emphasis colour (Bold, Clean) or
slightly larger (Bounce). The currently spoken word keeps its active highlight.

## Caption Positioning

Positions come from `remotion/src/styles/layout.ts`, which encodes the platform
UI-safe zones (see `platform-specs.md`):

| Layout | Caption position |
|--------|------------------|
| fill (talking head) | 440px above the bottom edge |
| framed (screen recording) | 440px above the bottom edge, below the inset video |
| split (podcast) | Across the seam between the two speaker panels |

Captions are also kept clear of the right-hand button column (110px).

## Integration with Hook Overlay

The hook overlay starts 220px from the top, below the platform header area, and is
shown for the first 3.5s only. Captions appear in the lower part of the frame, so
the two never overlap.

When both are visible (0.2s - 3.5s), the visual hierarchy is:
1. Hook text (top, large, attention-grabbing)
2. Video content (center)
3. Captions (lower third, word-level)
4. Progress bar (very bottom, subtle)
