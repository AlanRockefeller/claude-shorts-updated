# Changelog

## v2.0.0

Modernisation pass. Work files and command lines changed; re-run `bash install.sh`.

### Fixed
- **Fresh installs crashed in content detection and reframing.** MediaPipe 1.x removed the `mp.solutions` API. Face detection now uses OpenCV's YuNet model (bundled, 232 KB); MediaPipe is no longer a dependency.
- **Installed skill had no Remotion dependencies.** `install.sh` copied the project without `node_modules` and nothing installed them there, so preflight failed. `install.sh` now runs setup in the installed location.
- **Transcripts were too large for Claude to read**, and the caption clean-up step asked Claude to rewrite the entire word-level JSON. There is now a compact `transcript.txt` reading view, and captions are edited through a small per-segment edit list (`captions.py`).
- **Stale files leaked between runs.** Every video gets its own work directory; clips and renders are cleared before being rebuilt; exports are skipped only when newer than their render.
- **Boundary snapping:** silence detection decoded the whole video (with a 120 s timeout that long videos hit) and could start a clip in the middle of a long pause; over-long clips were truncated mid-word. Now only a few seconds around each cut are analysed, cuts sit just before the first sound, and length limits are applied at sentence ends.
- **Podcast crops landed between the two speakers** (average of both face positions). Speakers are now clustered; far-apart speakers get a stacked split-screen.
- **Hook text sat under the platform header** (40 px from the top). Hook and captions now respect a shared safe-zone layout.
- `export.sh` hid FFmpeg errors and died if NVENC was listed but unusable; it now logs errors and falls back to libx264. `detect_gpu.sh` no longer aborts when FFmpeg has no NVENC encoders.
- Shell scripts no longer need `bc` (not installed by default on Arch) or `rsync`, and build JSON with `jq` so paths with quotes or spaces are safe.
- Clean caption style highlighted the active word by making it *dimmer*; it is now the brightest word.

### Changed
- Default Whisper model is `large-v3-turbo` (much faster than `large-v3` for a small accuracy cost; `--model large-v3` is still available). New options: `--language`, `--initial-prompt`, `--batch-size`. Transcripts are cached per video.
- PyTorch is no longer installed (it was only used to detect CUDA). GPU transcription loads the CUDA runtime libraries CTranslate2 needs (pip wheels or system libraries; `scripts/cuda_libs.py` reports what is required), with automatic CPU fallback.
- Work files default to `~/.cache/claude-shorts/` instead of `/tmp` (tmpfs on Arch/Omarchy). Finished shorts go to `./shorts/<video-name>/`.
- Remotion 4.0.422 → 4.0.542, pinned to exact versions. Renders are tagged BT.709, follow the source frame rate (up to 60 fps), and use a higher-quality intermediate.
- Export uses two-pass loudness normalisation (measured, then linear) instead of single-pass dynamic mode.
- Clips up to 180 s are allowed (all three platforms now accept 3-minute shorts); the default candidate length is 20-60 s.
- SKILL.md uses `${CLAUDE_SKILL_DIR}` and `$ARGUMENTS`; `/shorts <file or URL>` works directly.
- Node.js 20+ (18 is end-of-life). Package-manager hints are distro-aware (pacman/apt/dnf/brew).

### Added
- Visual QA: contact sheets of the source, the computed crops, and the rendered shorts for Claude to inspect (`contact_sheet.py`).
- Face tracking that pans to follow a moving speaker; split-screen podcast layout; blurred backdrop for screen recordings.
- Caption review by Claude: fix mis-heard words, delete fillers, mark emphasis words (rendered in an accent colour).
- Video URL input via `yt-dlp` (optional).
- Upload copy (title, description, hashtags) written next to each short.
- `extract_clips.sh`, `render.mjs --only/--fps/--background/--concurrency`, `SHORTS_CHROME` to use an installed browser.
- Tests: pytest unit tests, a synthetic end-to-end smoke test, and a CI workflow.

## v1.1.0

- Renamed caption styles from creator names to generic: Bold, Bounce, Clean
- Renamed skill command from `/claude-shorts` to `/shorts`
- Audio loudnorm normalization in export pipeline
- Skip existing exports instead of overwriting
- Use segment ID for clip/output filenames instead of array index
- Render retry logic (1 automatic retry before skipping)
- Snap boundaries tolerance fix for edge-case word alignment
- Batch frame extraction for content detection
- Configurable temp directory via `$SHORTS_TMP`

## v1.0.0 — Initial Release

- 10-step interactive pipeline for longform-to-shortform video creation
- 3 caption styles: Bold, Bounce, Clean
- Remotion v4 rendering with spring animations and word-level karaoke highlighting
- GPU-accelerated transcription via faster-whisper
- Audio-aware boundary snapping (never cuts mid-word or mid-sentence)
- Cursor tracking for screen recordings
- Face tracking for talking-head content
- Platform-optimized export for YouTube Shorts, TikTok, and Instagram Reels
- NVENC GPU encoding with automatic CPU fallback
