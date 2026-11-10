# claude-shorts

![claude-shorts header](claude-shorts-header.jpeg)

Interactive longform-to-shortform video creator powered by [Claude Code](https://code.claude.com/docs). Extracts viral-ready vertical clips from long videos using Claude as the intelligent orchestrator with Remotion-rendered premium animated captions.

> This is an updated fork of [AgriciDaniel/claude-shorts](https://github.com/AgriciDaniel/claude-shorts). See [CHANGELOG.md](CHANGELOG.md) for what changed in v2.

## How It Works

Claude Code guides you through an interactive pipeline:

1. **Preflight** - Validates the input (file or URL), tools and disk space; allocates a per-video work directory
2. **Transcribe** - faster-whisper with word-level timestamps (GPU if available, cached per video)
3. **Look** - Claude views a contact sheet of the video and picks the layout: talking-head, podcast, or screen recording
4. **Analyze** - Claude reads a compact timestamped transcript and scores candidate segments
5. **Present** - Candidates in a table with scores, hooks, and rationale
6. **Approve** - You pick segments, adjust timecodes, choose caption style and platform
7. **Snap Boundaries** - Cut points move to sentence boundaries and real silences
8. **Prepare** - Clips are cut, Claude reviews the caption text, long pauses and fillers are jump-cut out (optional), and the result is reframed
9. **Render** - Remotion renders 1080x1920 video with animated captions; Claude checks frames of the result
10. **Export** - Platform-optimized encoding, validation, and upload copy (title, description, hashtags)

## Features

- **Claude-powered segment scoring** - 5-dimension rubric (hook strength, coherence, emotion, value density, payoff). No keyword heuristics: Claude reads the whole transcript.
- **Visual checks** - Claude looks at contact sheets of the source, the computed crops, and the rendered shorts, so a crop that misses the speaker is caught before you see it.
- **Smart reframing** - Face tracking that follows a moving speaker, stacked split-screen for two-person podcasts, and cursor-following zoom with a blurred backdrop for screen recordings.
- **Caption review** - Claude fixes mis-heard names and jargon, drops fillers, and marks key words for emphasis, without ever shifting caption timing.
- **3 caption styles** - Bold (ALL CAPS, yellow highlights), Bounce (bouncy spring, rotating colors), Clean (minimal fade-in). Text stays inside the platforms' UI-safe zones.
- **Jump cuts** - Optionally removes long pauses, "um"s and the fillers Claude flags, from the video itself. Pauses are measured in the real audio, cuts are click-free, and captions are re-timed to match.
- **Audio-aware boundary snapping** - Never cuts mid-word or mid-sentence.
- **Proper loudness** - Two-pass normalisation to -14 LUFS.
- **Linux and macOS** - CUDA for transcription and NVENC for export when an NVIDIA GPU is present; everything also runs on CPU, including on Macs.

## Prerequisites

- **FFmpeg**, **jq**
- **Python 3.10+**
- **Node.js 20+**
- **Claude Code**
- **NVIDIA GPU** optional (CUDA transcription + NVENC encoding)
- **yt-dlp** optional (start from a video URL)

Arch / Omarchy:

```bash
sudo pacman -S --needed ffmpeg jq python nodejs npm yt-dlp
# Omarchy users who manage Node with mise can skip nodejs/npm:  mise use -g node@lts
```

Debian / Ubuntu: `sudo apt install ffmpeg jq python3 python3-venv nodejs npm`

macOS (with [Homebrew](https://brew.sh)):

```bash
brew install ffmpeg jq node python yt-dlp
```

On macOS everything runs on the CPU: there is no CUDA, and transcription uses CTranslate2's Apple Accelerate backend. That is comfortable on Apple Silicon; on older Intel Macs use `--model small` for long videos. The scripts are written for the bash 3.2 and BSD tools that ship with macOS, and CI runs the pipeline on a macOS runner.

## Installation

```bash
git clone https://github.com/AlanRockefeller/claude-shorts-updated.git
cd claude-shorts-updated
bash install.sh
```

`install.sh` copies the skill to `~/.claude/skills/shorts/` and runs `setup.sh` there, which:

- Creates a Python venv at `~/.shorts-skill/` (or reuses `~/.video-skill/` if it exists; uses `uv` when installed)
- Installs `faster-whisper`, `numpy` and `opencv-python-headless`. No PyTorch is needed.
- On NVIDIA systems, installs the CUDA runtime wheels that CTranslate2 needs (unless system CUDA libraries are already loadable)
- Runs `npm ci` for the Remotion project and pre-downloads Remotion's headless browser

For development, `bash install.sh --link` symlinks your checkout instead of copying, so edits take effect immediately.

To remove: `bash uninstall.sh` (add `--all` to also delete the venv and cached work files).

### Windows

Use [WSL 2](https://learn.microsoft.com/en-us/windows/wsl/install) and follow the Linux steps inside it.

## Usage

In Claude Code:

```
/shorts ~/Videos/my-talk.mp4
/shorts https://www.youtube.com/watch?v=...     (needs yt-dlp; only for content you may reuse)
```

### Example Interaction

```
You: /shorts ~/Videos/my-talk.mp4

Claude: [Transcribes, looks at the video, scores segments]

| # | Time          | Dur  | Score | Hook                           |
|---|---------------|------|-------|--------------------------------|
| 1 | 04:22 - 05:01 | 39s  | 87    | "Nobody talks about this..."  |
| 2 | 12:45 - 13:28 | 43s  | 82    | "Here's the exact framework." |
| 3 | 08:11 - 08:52 | 41s  | 79    | "I tested this for 6 months." |

Claude: Which segments? Caption style? Platform?

You: 1 and 3, bounce style, youtube

Claude: [Snaps boundaries, reframes, reviews captions, renders, checks frames, exports]

Output: shorts/my-talk/short_01_yt.mp4, short_03_yt.mp4 (+ short_01.md, short_03.md with titles and hashtags)
```

### Where files go

| What | Where | Override |
|------|-------|----------|
| Finished shorts | `./shorts/<video-name>/` | pass an output dir to the skill |
| Work files (transcript cache, clips, renders) | `~/.cache/claude-shorts/<video>-<hash>/` | `SHORTS_TMP` |
| Python venv | `~/.shorts-skill/` | `SHORTS_VENV` |

Work files default to `~/.cache` rather than `/tmp` because `/tmp` is a RAM-backed tmpfs on Arch-based systems.

## Project Structure

```
claude-shorts/
├── SKILL.md                    # The interactive pipeline (Claude Code skill)
├── install.sh / setup.sh       # Install skill + dependencies
├── scripts/
│   ├── preflight.sh            # Input/tool validation, work directory allocation
│   ├── detect_gpu.sh           # CUDA / NVENC capability report
│   ├── transcribe.py           # faster-whisper → transcript.json + transcript.txt
│   ├── detect_content.py       # Heuristic content type (face stats)
│   ├── contact_sheet.py        # Frame grids for Claude's visual checks
│   ├── snap_boundaries.py      # Sentence- and silence-aware cut points
│   ├── extract_clips.sh        # Stream-copy clip extraction
│   ├── jumpcut.py              # Cut long pauses and fillers out of the clips
│   ├── compute_reframe.py      # Face tracking, split-screen, cursor tracking
│   ├── captions.py             # Per-segment caption tracks + Claude's edits
│   ├── export.sh               # Platform encodes, two-pass loudnorm
│   ├── validate.sh             # Output checks
│   ├── facedet.py              # YuNet face detector wrapper
│   ├── cuda_libs.py            # Finds/loads the CUDA libraries CTranslate2 needs
│   └── shorts_common.py, lib.sh
├── models/                     # YuNet face detection model (232 KB, MIT)
├── remotion/
│   ├── render.mjs              # Bundle-once-render-many orchestrator
│   └── src/                    # Composition, layouts, caption styles
├── references/                 # Scoring rubric, caption/platform specs, Remotion notes
└── tests/                      # pytest unit tests + synthetic end-to-end smoke test
```

## Caption Styles

| Style | Font | Animation | Best For |
|-------|------|-----------|----------|
| **Bold** | Montserrat Bold | Pop-in spring, yellow active word | Business, education |
| **Bounce** | Bangers | Bouncy scale 70-120-100%, rotating colors | Entertainment, energy |
| **Clean** | Inter Bold | Fade-in opacity, white + shadow | Professional, interviews |

## Layouts

| Content Type | Layout | Notes |
|--------------|--------|-------|
| **Talking-head** | Full-frame 9:16 crop on the speaker | Static when they sit still, smooth pan when they move |
| **Podcast** | Both speakers in one crop, or stacked split-screen | Split is used when they are too far apart for one crop (`--no-split` to disable) |
| **Screen recording** | Zoomed inset over a blurred backdrop | Pans to follow the cursor; `--zoom` sets how much of the screen is shown |

## Platform Export Specs

| Platform | Codec | Bitrate | Audio |
|----------|-------|---------|-------|
| **YouTube Shorts** | H.264 High 4.2 | 12 Mbps | AAC 192k |
| **TikTok** | H.264 | CRF 18, max 10 Mbps | AAC 128k |
| **Instagram Reels** | H.264 High 4.2 | 4.5 Mbps (max 5 Mbps) | AAC 128k |

All three accept clips up to 3 minutes; the default candidate length is 20-60 seconds.

## Testing

```bash
source ~/.shorts-skill/bin/activate && pip install pytest
pytest tests/                 # unit tests (no GPU, model or FFmpeg needed)
bash tests/smoke.sh           # synthetic end-to-end run, everything but the render
bash tests/smoke.sh --render  # ...including Remotion render, export and validation
```

The smoke test generates its own video and transcript, so it does not need a Whisper model.

## Dependencies

### Python (installed via `setup.sh`)
- [faster-whisper](https://github.com/SYSTRAN/faster-whisper) - Whisper on CTranslate2 (CUDA or CPU)
- [opencv-python-headless](https://opencv.org/) - YuNet face detection, cursor tracking, contact sheets
- [numpy](https://numpy.org/)

### Node.js (installed via `setup.sh`)
- [Remotion v4](https://remotion.dev/) - React-based video rendering. Free for individuals and small teams; larger companies need a [company licence](https://www.remotion.dev/license).
- [@remotion/captions](https://remotion.dev/docs/captions) - TikTok-style word-level captions
- [React 19](https://react.dev/), [Zod](https://zod.dev/)

### Bundled assets
- Caption fonts from Google Fonts under the [SIL Open Font License](remotion/public/fonts/OFL.txt): Montserrat Bold, Bangers, Inter Bold
- [YuNet](https://github.com/opencv/opencv_zoo/tree/main/models/face_detection_yunet) face detection model, [MIT](models/LICENSE-yunet.txt)

## How Segment Scoring Works

Claude scores each candidate on 5 weighted dimensions:

| Dimension | Weight | What Claude Looks For |
|-----------|--------|----------------------|
| Hook Strength | 0.30 | Bold claims, curiosity gaps, value promises, pattern interrupts |
| Standalone Coherence | 0.25 | Makes complete sense without any context from the rest of the video |
| Emotional Intensity | 0.20 | Strong opinions, surprise reveals, humor, passion |
| Value Density | 0.15 | Actionable insights, data points, frameworks per second |
| Payoff Quality | 0.10 | Satisfying conclusion - punchline, reveal, call-to-action |

Final score = weighted sum, scale 0-100. Minimum threshold: 60.

## License

[MIT](LICENSE)

---

## Original Author

Built by [Agrici Daniel](https://agricidaniel.com/about) - AI Workflow Architect.

- [Blog](https://agricidaniel.com/blog) - Deep dives on AI marketing automation
- [AI Marketing Hub](https://www.skool.com/ai-marketing-hub) - Free community, 2,800+ members
- [YouTube](https://www.youtube.com/@AgriciDaniel) - Tutorials and demos
- [All open-source tools](https://github.com/AgriciDaniel)
