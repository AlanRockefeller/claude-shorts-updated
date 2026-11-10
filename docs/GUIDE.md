# User Guide

How to use the optional features and tune the results. You normally just talk to Claude (`/shorts my-video.mp4`) and answer its questions; the commands shown here are what it runs for you, and what you can ask it to change.

Paths: `ROOT` is the skill directory (`~/.claude/skills/shorts`), `WORK` is the per-video work directory that preflight prints.

## A typical run

1. `/shorts ~/Videos/talk.mp4` (or a URL, if `yt-dlp` is installed)
2. Claude transcribes, looks at the video, and shows a table of candidate clips.
3. You pick clips and answer a few questions: caption style, platform, jump cuts, and (if you use them) music and b-roll.
4. Claude cuts, captions, reframes, renders, checks frames of the result, and exports to `./shorts/<video-name>/`.

Things you can say at any point: "start clip 2 three seconds earlier", "re-analyze, I want more technical moments", "use the clean style instead", "make clip 3 a split-screen", "undo the jump cuts on clip 1".

## Jump cuts

Removes long pauses and filler words from the video itself.

- **What gets cut:** pauses longer than 0.5 s (shortened to 0.2 s), "um/uh" sounds, and any filler or false start Claude marks during caption review.
- **What never gets cut:** audio belonging to a word that stays.
- **When to say no:** screen recordings and demos (silent stretches usually show something happening) and videos with background music already in them (each cut is audible in the music).

| You want | Ask for / flag |
|----------|----------------|
| Only tighten pauses, keep every word | "pauses only" → `--no-fillers` |
| A more relaxed feel | `--max-pause 0.8 --keep-pause 0.35` |
| Punchier | `--max-pause 0.35 --keep-pause 0.12` |
| See what would be removed first | `--dry-run` |
| Undo | `--restore` |

```bash
python3 ROOT/scripts/jumpcut.py --segments WORK/snapped_segments.json \
    --transcript WORK/transcript.json --clips-dir WORK/clips --edits WORK/caption_edits.json
```

At each jump cut the picture alternates a slight zoom ("punch-in"), so the cut looks like a camera change. Turn it off with `render.mjs --no-punch-in`, or change the amount with `--punch-zoom 1.12`.

## Two-person videos

When both people fit in one vertical crop, they are framed together. When they are too far apart, there are three layouts:

| Layout | What you get | Flag |
|--------|--------------|------|
| **switch** | One full-height crop that cuts to whoever is talking | `--podcast-layout switch` |
| **split** | Both people stacked, one per half, captions across the middle | `--podcast-layout split` |
| **single** | Stays on the main speaker | `--podcast-layout single` |

The default (`auto`) uses **switch** when it can tell who is speaking and **split** otherwise. Speaker detection watches mouth movement, so it struggles when a face is small, turned away, covered by a microphone, or when both people talk over each other. If the cutting looks wrong or restless, ask for split-screen.

## B-roll

Cutaway clips or images shown full-frame while the speaker keeps talking.

1. Put your footage and images in a folder (mp4, mov, webm, mkv, jpg, png, webp). Descriptive file names help.
2. Tell Claude the folder when it asks about b-roll.
3. Claude looks at thumbnails of every file, then places cutaways on the words they illustrate.

Good b-roll shows exactly what is being said: the product when it is named, the chart when the number is quoted. Claude is told to skip b-roll rather than use a loose match, to keep you on screen for the hook and the payoff, and to cover at most about a third of a short. Cutaways run 1.2–8 s each.

To adjust: "no b-roll on clip 2", "use the dashboard clip when I mention the report", "start that cutaway later". The plan lives in `WORK/broll_plan.json` if you want to edit it by hand.

Only use footage you have the right to publish. There is no built-in stock-footage search.

## Music

A background track under the speech.

```bash
bash ROOT/scripts/add_music.sh --input-dir WORK/render --music ~/Music/bed.mp3 --volume -20
```

- The track loops if it is too short and fades out over the last 1.5 s.
- It is turned down automatically whenever someone speaks ("ducking").
- `--volume` is in dB: `-20` is a quiet bed, `-14` is prominent.
- `--start 30` begins 30 s into the track; `--only 2,5` applies it to some shorts; `--remove` takes it off.

Only use music you have the rights to. Platforms mute or block videos with unlicensed tracks; their own in-app music libraries are the safe alternative, added at upload time.

## Captions

Three styles: **bold** (all caps, yellow highlight on the spoken word), **bounce** (big, colourful, one or two words at a time), **clean** (sentence case, subtle).

During caption review Claude fixes mis-heard names and jargon, removes fillers, and marks a few key words per sentence for an accent colour. You can steer this: "spell it OmniRoute", "less emphasis", "don't remove 'like', it's how I talk". Give names and jargon up front ("the guest is Priya Raman, the product is OmniRoute") and they will be transcribed correctly in the first place.

## Style profile

Your usual choices are saved in `~/.config/claude-shorts/profile.md` and read at the start of every run, so Claude asks fewer questions. It is a plain text file you can edit:

```markdown
# claude-shorts profile
caption_style: bold
platform: all
jump_cuts: pauses and fillers
clip_length: 20-60s
podcast_layout: auto
music: /home/me/Music/bed.mp3 (volume -20)
broll_dir: /home/me/Videos/broll

## Notes
- Hooks: plain statements, no questions, no emoji.
- Never clip the sponsor read.
- Spell the product "OmniRoute", one word.
```

Claude offers to create or update it at the end of a run. Say "ignore my profile this time" to start fresh.

## Speed and quality

| Situation | What to do |
|-----------|------------|
| No NVIDIA GPU, long video | `--model small` for transcription (faster, less accurate) |
| Best possible transcript | `--model large-v3` |
| 60 fps source, slow renders | `render.mjs --fps 30` halves render time |
| Re-render one clip | `render.mjs --only 3` |
| Second pass on the same video | Instant: the transcript is cached in the work directory |

## Where things are

| What | Where |
|------|-------|
| Finished shorts and upload copy (`short_NN.md`) | `./shorts/<video-name>/` |
| Work files, transcript cache | `~/.cache/claude-shorts/<video>-<hash>/` (`SHORTS_TMP`) |
| Style profile | `~/.config/claude-shorts/profile.md` (`SHORTS_PROFILE`) |
| Python environment | `~/.shorts-skill/` (`SHORTS_VENV`) |

More flags and fixes for common failures: [references/options-and-troubleshooting.md](../references/options-and-troubleshooting.md).
