# claude-shorts

Standalone interactive shortform video creator. Extracts viral-ready short clips from longform videos using an interactive Claude Code workflow with Remotion-rendered premium captions.

## Architecture

- **Claude Code** is the orchestrator — reads the transcript, scores segments, looks at contact sheets, reviews captions, talks to the user. The workflow lives in `SKILL.md`.
- **faster-whisper** (CTranslate2) transcribes with word-level timestamps. CUDA if available, CPU otherwise. No PyTorch.
- **OpenCV YuNet** detects faces for content classification and reframing (`models/face_detection_yunet_2023mar.onnx`).
- **Remotion** renders 1080x1920 vertical video with animated captions (single pass).
- **FFmpeg** cuts segments (stream copy) and does platform-specific export encoding.

## Key Commands

```bash
bash install.sh          # Copy skill to ~/.claude/skills/shorts/ and install its dependencies
bash install.sh --link   # Symlink this checkout instead (development)
bash setup.sh            # Dependencies only, for this checkout

pytest tests/                  # Unit tests
bash tests/smoke.sh --render   # Synthetic end-to-end run (no Whisper model needed)
(cd remotion && npm run typecheck)
```

Run the tests after changing anything under `scripts/` or `remotion/`.

## Data flow

All intermediate files live in a per-video work directory printed by `preflight.sh` (`WORK`):

```
transcript.json / transcript.txt   transcribe.py     (txt is the reading view for Claude)
approved_segments.json             written by Claude
snapped_segments.json              snap_boundaries.py (adds word_start / word_end)
clips/clip_NN.mp4                  extract_clips.sh
caption_edits.json                 written by Claude  (delete / replace / emphasis by word index)
clips/uncut/, segments[].keep      jumpcut.py         (optional: recuts clips, records kept ranges
                                                       and render_duration in snapped_segments.json)
captions.json                      captions.py apply  (clip-local ms, re-timed through `keep`)
reframe.json                       compute_reframe.py (layout: fill | framed | split; crop_mode pan | cut;
                                                       run on the cut clips)
broll_plan.json -> broll.json      written by Claude -> broll.py apply (optional; word-anchored cutaways)
render/short_NN.mp4                remotion/render.mjs (punch-ins from segments[].keep, --broll)
render/nomusic/                    add_music.sh        (optional; originals before the music bed)
qa/*.jpg                           contact_sheet.py
```

Conventions:
- Scripts print JSON on stdout and exit non-zero on failure.
- `transcript.json` is large; nothing should require Claude to read or rewrite it. Add a compact view or an edit-list instead.
- Layout constants and platform safe zones are in `remotion/src/styles/layout.ts`.
- All `remotion` / `@remotion/*` packages must be the same exact version (no `^`).
- Shell scripts must run on macOS's bash 3.2 with BSD userland as well as Linux: no `sha1sum`/`realpath`/`stat -c` without the fallbacks in `scripts/lib.sh`, no GNU-only flags, and expand possibly-empty arrays as `${arr[@]+"${arr[@]}"}` (bash 3.2 errors on `"${arr[@]}"` under `set -u`). CI runs the smoke test on macOS.

## Python Virtual Environment

Scripts use `~/.video-skill/` if it exists (shared with claude-video), otherwise `~/.shorts-skill/`. Override with `SHORTS_VENV`.

```bash
source ~/.shorts-skill/bin/activate
```

## Remotion

```bash
cd remotion && npm run preview       # Remotion Studio
node remotion/render.mjs --help      # Headless render (used by the pipeline)
```

`SHORTS_CHROME=/path/to/chromium` makes the renderer use an installed browser instead of Remotion's downloaded Chrome Headless Shell.

## Work Files

Intermediate files go to `$SHORTS_TMP` (default `~/.cache/claude-shorts/`, not `/tmp`, which is a RAM-backed tmpfs on Arch-based systems). Each source video gets its own subdirectory, which doubles as the transcript cache.

## Dependencies

- FFmpeg, jq (system)
- Python 3.10+ with faster-whisper, numpy, opencv-python-headless
- Node.js 20+ with remotion, @remotion/captions, zod
- NVIDIA GPU optional (CUDA runtime libs for transcription, see `scripts/cuda_libs.py`; NVENC for encoding)
