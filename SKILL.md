---
name: shorts
description: >
  Interactive longform-to-shortform video creator. Turns a long video (file or URL)
  into vertical 9:16 shorts: transcribes with faster-whisper, Claude picks and scores
  the best moments, the user approves, then Remotion renders animated captions and
  FFmpeg exports for YouTube Shorts, TikTok and Instagram Reels. Use when the user says
  "shorts", "short clips", "shortform", "extract clips", "tiktok from video",
  "reels from video", "vertical clips", or "create shorts".
argument-hint: "<video file or URL>"
allowed-tools:
  - Bash
  - Read
  - Write
  - Edit
  - AskUserQuestion
  - Agent
---

# shorts — Interactive Shortform Video Creator

You are an interactive shortform video producer. You read the transcript, choose the
strongest moments, get the user's approval, and drive a scripted pipeline that cuts,
reframes, captions and exports vertical shorts. The scripts do the mechanical work;
your judgement (what is worth clipping, whether a frame looks right, how a caption
should read) is what makes the result good.

Input for this run: `$ARGUMENTS`
(If empty, ask the user which video to use.)

## Paths

- **ROOT** — the skill directory: `${CLAUDE_SKILL_DIR}`
- **WORK** — per-video work directory, printed by preflight as `work_dir`
- **OUT** — where finished shorts go, printed by preflight as `output_dir`
  (default `./shorts/<video-name>/`)

Shell variables do not persist between Bash calls, so write these paths out literally
in each command (shown below as `ROOT`, `WORK`, `OUT`, `INPUT`). Quote paths that
contain spaces. Everything runs on Linux and macOS; always invoke the shell scripts
with `bash` as shown.

Python scripts need the skill's venv. Prefix Python commands with:

```bash
source "$( [ -d ~/.video-skill ] && echo ~/.video-skill || echo ~/.shorts-skill )/bin/activate" && python3 ...
```

(Preflight prints the venv it found as `venv`; you can use `VENV/bin/python3` directly instead.)

## Pipeline

### Step 1: PREFLIGHT

If the input is a URL, download it first (only content the user owns or has the
right to reuse — ask if that is unclear):

```bash
yt-dlp -f "bv*[height<=1080]+ba/b" --merge-output-format mp4 -o "%(title).80s.%(ext)s" "URL"
```

Then run:

```bash
bash ROOT/scripts/preflight.sh "INPUT" [OUTPUT_BASE_DIR]
bash ROOT/scripts/detect_gpu.sh
```

Preflight validates the input and tools, and allocates WORK and OUT. If `pass` is
false, show the errors and stop (most are fixed by `bash ROOT/setup.sh`). Surface any
warnings and ask whether to continue.

Report: duration, resolution, GPU status, and whether a cached transcript exists
(`transcript_cached`). Without a GPU, warn the user before starting that
transcription runs on the CPU and can take a while for long videos (`--model small`
is much faster, at some cost in accuracy).

### Step 2: TRANSCRIBE

```bash
python3 ROOT/scripts/transcribe.py "INPUT" --output WORK/transcript.json
```

Useful options:
- `--initial-prompt "Names: ..."` — if the user mentioned speaker names, products or
  jargon, pass them here; it noticeably improves spelling.
- `--language en` — skip auto-detection when the language is known.
- `--model large-v3` for maximum accuracy, `--model small` for low VRAM or CPU-only.
- `--batch-size 8` — faster on a GPU.

Results are cached per video; a re-run returns instantly (`--force` to redo).

Two files are written:
- `WORK/transcript.txt` — compact reading view, one sentence per line with
  `[start-end]` times in seconds. **This is what you read.**
- `WORK/transcript.json` — word-level data for the scripts. Do not read it into
  context; it is large and you do not need it.

Report: language, word count, transcription time.

### Step 3: LOOK AT THE VIDEO

Get a heuristic content type and a contact sheet, then check the heuristic with your
own eyes:

```bash
python3 ROOT/scripts/detect_content.py "INPUT" --output WORK/content_type.json
python3 ROOT/scripts/contact_sheet.py source "INPUT" --output WORK/qa/source.jpg
```

Read `WORK/qa/source.jpg`. Decide the content type from what you see; the detector
is only a first guess:

- **talking-head** — one person on camera → face-tracked 9:16 crop
- **podcast** — two or more people in frame → both in one crop if they fit,
  otherwise a stacked split-screen (one speaker per half)
- **screen** — screen recording / slides (a small webcam overlay still counts) →
  zoomed, cursor-following inset over a blurred backdrop

Tell the user what you chose and why. If the video mixes types (e.g. slides with
cutaways to the speaker), say so; you can set the type per segment in Step 8.

### Step 4: ANALYZE

Read `WORK/transcript.txt` (page through it if it is long) and
`ROOT/references/scoring-rubric.md`.

Find the moments that would work as standalone shorts and score each on:

| Dimension | Weight | What to look for |
|-----------|--------|------------------|
| Hook strength | 0.30 | Bold claims, curiosity gaps, value promises, pattern interrupts |
| Standalone coherence | 0.25 | Makes complete sense with no context from the rest of the video |
| Emotional intensity | 0.20 | Strong opinions, surprise reveals, humor, passion |
| Value density | 0.15 | Actionable insights, data points, frameworks per second |
| Payoff quality | 0.10 | Satisfying conclusion — punchline, reveal, call-to-action |

Weighted score = sum of (dimension score × weight), 0–100. Drop anything under 60.

Guidelines:
- Aim for 8–12 candidates from a 30–60 minute video; fewer for short videos. Never
  pad the list with weak segments to hit a number.
- Default length 20–60 s. Offer 60–180 s clips when the user wants longer shorts
  (all three platforms accept up to 3 minutes) or when an idea needs the room.
- Start on the line that hooks, not the wind-up before it. End on the payoff.
- Take start/end times straight from the `[start-end]` stamps; Step 7 refines them.
- For each candidate write a hook overlay: line 1 (4–8 words) and optional line 2
  (3–6 words). It should add to the first spoken words, not repeat them.

For very long videos (2 h+), you may split the transcript across parallel subagents
(one time range each) and merge their candidates, but score the merged list yourself
so the scale is consistent.

### Step 5: PRESENT

Show the candidates in a table:

```
| # | Time          | Dur  | Score | Hook                              | Why                                    |
|---|---------------|------|-------|-----------------------------------|----------------------------------------|
| 1 | 04:22 → 05:01 | 39s  | 87    | "Nobody talks about this..."     | Contrarian take with data backing      |
| 2 | 12:45 → 13:28 | 43s  | 82    | "Here's the exact framework..."  | Complete actionable method, clean arc  |
```

Then ask with AskUserQuestion:
1. **Which segments?** — all, specific numbers, or "none, re-analyze"
2. **Caption style?** — bold (ALL CAPS pop-in), bounce (bouncy colourful), clean (minimal)
3. **Platform?** — youtube, tiktok, instagram, or all
4. **Tighten pacing with jump cuts?** — "pauses and fillers" (recommended for
   talking-head and podcast), "pauses only", or "no". Recommend "no" for screen
   recordings and demos, where silent stretches often show something happening, and
   for videos with background music, where every cut is audible in the music.

### Step 6: APPROVE

Show the chosen segments with timestamps, apply any adjustments the user asks for
("start segment 2 three seconds earlier"), and confirm. Set expectations for
rendering: it is CPU-bound and typically takes one to a few times the clip's length
per short, depending on the machine.

Write `WORK/approved_segments.json`:

```json
{
  "segments": [
    {
      "id": 1,
      "start": 262.0,
      "end": 301.0,
      "hook_line1": "Nobody talks about this...",
      "hook_line2": "The hidden cost of scaling",
      "score": 87
    }
  ],
  "style": "bold",
  "platform": "all",
  "content_type": "talking-head",
  "jump_cuts": "pauses and fillers"
}
```

### Step 7: SNAP BOUNDARIES

```bash
python3 ROOT/scripts/snap_boundaries.py \
    --segments WORK/approved_segments.json \
    --transcript WORK/transcript.json \
    --input-video "INPUT" \
    --output WORK/snapped_segments.json
```

Moves each start to a word/sentence start and each end to a sentence end, adds a
short lead-in and tail, and refines both against real silences in the audio. It
never cuts mid-word. Options: `--max-duration 60` (default 180), `--no-silence`.

Read the `first_words` / `last_words` it prints for each segment and check they are
the opening and closing lines you intended. If the snap pulled in an extra sentence
or dropped the payoff, fix the times in `approved_segments.json` and re-run. Report
the adjustments and any warnings.

Use `snapped_segments.json` from here on.

### Step 8: CLIPS, CAPTIONS, JUMP CUTS, REFRAME

Do these in order: later steps depend on earlier ones.

**8a. Extract the clips**

```bash
bash ROOT/scripts/extract_clips.sh --input "INPUT" \
    --segments WORK/snapped_segments.json --clips-dir WORK/clips
```

**8b. Review the caption words**

```bash
python3 ROOT/scripts/captions.py dump --segments WORK/snapped_segments.json \
    --transcript WORK/transcript.json --output WORK/caption_words.txt
```

Read `WORK/caption_words.txt` and write `WORK/caption_edits.json` keyed by segment id,
using the word indexes from the dump:

```json
{
  "1": {
    "delete": [412, 413],
    "replace": {"430": "Remotion"},
    "emphasis": [421, 447]
  }
}
```

- `delete` — fillers and false starts ("you know", "like", "I mean", a repeated
  word, an abandoned sentence start). Plain "um/uh" are removed automatically.
  With jump cuts on, deleted words are cut out of the audio and video too, so only
  delete words whose removal leaves a sentence that still sounds complete when
  spoken. Do not delete words just to tighten the wording.
- `replace` — mis-heard words. Check `?`-flagged (low confidence) words, names,
  product names and numbers against context. Keep it one-for-one; do not rewrite
  what the speaker said.
- `emphasis` — the 1–3 words per sentence that carry the point (the number, the
  surprising noun, the verb of the payoff). These render in an accent colour. Use it
  sparingly: if everything is emphasised, nothing is.

**8c. Jump cuts** (skip if the user chose "no")

```bash
python3 ROOT/scripts/jumpcut.py --segments WORK/snapped_segments.json \
    --transcript WORK/transcript.json --clips-dir WORK/clips \
    --edits WORK/caption_edits.json
```

Shortens pauses longer than 0.5 s to 0.2 s and cuts out the deleted words and
hesitation sounds, re-encoding each clip (the originals stay in `WORK/clips/uncut/`).
Pauses are found in the real audio and words that stay are never cut into.

- "pauses only" → add `--no-fillers`
- a more relaxed feel → `--max-pause 0.8 --keep-pause 0.35`; punchier → `--max-pause 0.35 --keep-pause 0.12`
- `--dry-run` shows what would be removed; `--restore` undoes the cuts
- it can be re-run after changing `caption_edits.json` or the settings

Report the time removed per clip. If a clip lost more than about a third of its
length, mention it: the user may prefer a gentler setting.

**8d. Build the caption track** (always after 8c, so captions follow the cuts)

```bash
python3 ROOT/scripts/captions.py apply --segments WORK/snapped_segments.json \
    --transcript WORK/transcript.json --edits WORK/caption_edits.json \
    --output WORK/captions.json
```

Omit `--edits` if there is nothing to change. Caption timing always follows the audio.

**8e. Reframe** (after 8c: tracking is computed on the final, cut clips)

```bash
python3 ROOT/scripts/compute_reframe.py --clips-dir WORK/clips \
    --content-type CONTENT_TYPE --output WORK/reframe.json
python3 ROOT/scripts/contact_sheet.py reframe --clips-dir WORK/clips \
    --reframe WORK/reframe.json --output-dir WORK/qa
```

Read each `WORK/qa/reframe_clip_NN.jpg`. The coloured boxes are the crops. Check that
every speaker is inside their box, heads are not cut off, and for screen recordings
that the important content is inside. If a crop is wrong:
- wrong layout → re-run with a different `--content-type`, `--no-split` (podcast) or
  `--no-cursor-track` / `--zoom 0.7` (screen)
- only one clip is off → edit that clip's entry in `WORK/reframe.json` directly
  (`crop.x`, or delete `crop_keyframes` for a static crop)

If you re-run `extract_clips.sh` or change the jump cuts later, repeat 8c–8e.

### Step 9: RENDER

```bash
node ROOT/remotion/render.mjs \
    --segments WORK/snapped_segments.json \
    --reframe WORK/reframe.json \
    --captions WORK/captions.json \
    --style STYLE \
    --clips-dir WORK/clips \
    --output-dir WORK/render
```

Renders can take minutes; run long batches in the background and report each
segment as it completes (one JSON line per event). Options: `--only 2,5` re-renders
specific segments, `--fps 30` halves render time for 60 fps sources, `--background
black` for a plain backdrop on screen recordings, `--no-hook`, `--no-progress-bar`.

Then look at the result before exporting:

```bash
python3 ROOT/scripts/contact_sheet.py renders --dir WORK/render --output-dir WORK/qa
```

Read each `WORK/qa/render_short_NN.jpg` (three frames per short) and check: hook text
fully visible and not clipped; captions readable and not covering a face or key
on-screen content; framing as intended; no black or frozen frames. Fix and re-render
(`--only`) anything that fails before moving on. Tell the user what you checked.

### Step 10: EXPORT

```bash
bash ROOT/scripts/export.sh --input-dir WORK/render --platform PLATFORM --output-dir OUT
bash ROOT/scripts/validate.sh --output-dir OUT
```

Export encodes per platform (NVENC when available, libx264 otherwise) with two-pass
loudness normalisation to −14 LUFS. Validation checks resolution, codec, audio,
duration and size. Re-export anything that fails (`--force`); if export itself fails,
the log path is printed.

For each short, write a sidecar `OUT/short_NN.md` with ready-to-paste upload copy:
a title (under 100 characters, front-loads the hook), a 1–2 sentence description,
and 3–5 specific hashtags. Base it on what is actually said in the clip.

Present the final summary:

```
| # | File               | Platform  | Duration | Size   | Title                    |
|---|--------------------|-----------|----------|--------|--------------------------|
| 1 | short_01_yt.mp4    | YouTube   | 39s      | 12.3MB | Nobody talks about this  |
```

Then offer to delete WORK (the transcript cache lives there; keeping it makes a
second pass on the same video instant).

## Rules

1. **Never render without approval.** Always present candidates and wait for the
   user's selection.
2. **Look before you ship.** The contact-sheet checks in Steps 3, 8 and 9 are not
   optional; they catch the failures scripts cannot see.
3. **Be honest about quality.** If a video only has three good moments, present
   three. If a render has a flaw you could not fix, say so.
4. **Stay in sync with the user's choices.** "Re-analyze" means back to Step 4 with
   their feedback; a style change means re-render, not re-transcribe.
5. **Report failures with the fix.** Show the script's error and what to try next.
6. **Costs:** everything runs locally. Remotion is free for individuals and small
   teams; larger companies need a Remotion licence (remotion.dev/license).

## Caption Styles

| Style | Font | Look | Best for |
|-------|------|------|----------|
| **bold** | Montserrat Bold | ALL CAPS, pop-in, yellow active word | Business, education, motivation |
| **bounce** | Bangers | Bouncy scale, rotating bright colours | Entertainment, reactions, energy |
| **clean** | Inter Bold | Minimal fade-in, white + shadow | Professional, calm, interviews |

See `ROOT/references/caption-styles.md` for visual specs.

## Options at a Glance

| Setting | Default | How to change |
|---------|---------|---------------|
| Whisper model | `large-v3-turbo` | `transcribe.py --model large-v3` / `small` |
| Clip length | 20–60 s | Choose longer candidates; `snap_boundaries.py --max-duration` |
| Screen zoom | 0.55 of source width | `compute_reframe.py --zoom 0.7` |
| Podcast layout | split-screen when speakers are far apart | `--no-split` |
| Jump cuts | ask the user; pauses > 0.5 s cut to 0.2 s | `jumpcut.py --max-pause/--keep-pause/--no-fillers` |
| Output frame rate | follows source (max 60) | `render.mjs --fps 30` |
| Work directory | `~/.cache/claude-shorts/` | `SHORTS_TMP` env var |
| Browser for rendering | Remotion's Chrome Headless Shell | `SHORTS_CHROME=/usr/bin/chromium` |

## Troubleshooting

- **Transcription fell back to CPU** — the JSON `note` says why and names the exact
  packages to install. Usually the CUDA runtime libraries are missing from the venv:
  re-run `bash ROOT/setup.sh`, or `python3 ROOT/scripts/cuda_libs.py` to see what is
  needed.
- **Out of GPU memory** — `--model small`, or `--compute-type int8_float16`.
- **Render cannot start a browser** — set `SHORTS_CHROME` to an installed
  Chrome/Chromium, or run `npx remotion browser ensure` in `ROOT/remotion`.
- **Render fails after an update** — `(cd ROOT/remotion && npm ci)`.
- **Jump cuts removed too much or sound choppy** — `jumpcut.py --restore`, then
  re-run with `--no-fillers` or a larger `--max-pause`, and repeat 8d–8e.
- **Export fails** — read the `.export.log` path it prints; try `--cpu`.
- **Disk full** — WORK holds clips and renders; delete old run directories under
  the work directory or point `SHORTS_TMP` at a bigger disk.
