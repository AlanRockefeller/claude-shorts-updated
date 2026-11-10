# Options and Troubleshooting

`ROOT` is the skill directory, `WORK` the per-video work directory from preflight.

## Options at a Glance

| Setting | Default | How to change |
|---------|---------|---------------|
| Whisper model | `large-v3-turbo` | `transcribe.py --model large-v3` / `small` |
| Clip length | 20–60 s | Choose longer candidates; `snap_boundaries.py --max-duration` |
| Screen zoom | 0.55 of source width | `compute_reframe.py --zoom 0.7` |
| Podcast layout (far apart) | cut to the active speaker, else split | `compute_reframe.py --podcast-layout switch\|split\|single` |
| Punch-in at jump cuts | on for people on camera, 8% | `render.mjs --no-punch-in` / `--punch-zoom 1.12` |
| Music | none | `add_music.sh --music FILE --volume -20` |
| B-roll | none | `broll.py` + `render.mjs --broll` |
| Style profile | `~/.config/claude-shorts/profile.md` | `SHORTS_PROFILE` env var |
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
