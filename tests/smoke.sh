#!/usr/bin/env bash
# End-to-end smoke test on a synthetic video. No GPU or Whisper model needed:
# the fixture ships its own word-level transcript.
#
#   bash tests/smoke.sh            # everything except the Remotion render
#   bash tests/smoke.sh --render   # also render with Remotion, export and validate
#
# Uses the skill venv unless PYTHON is set.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=scripts/lib.sh
source "$ROOT/scripts/lib.sh"

RENDER=false
[ "${1:-}" = "--render" ] && RENDER=true

PYTHON="${PYTHON:-}"
if [ -z "$PYTHON" ]; then
    VENV="$(shorts_venv)"
    PYTHON="${VENV:+$VENV/bin/python3}"
    PYTHON="${PYTHON:-python3}"
fi

TMP="$(mktemp -d "${TMPDIR:-/tmp}/shorts-smoke.XXXXXX")"
trap 'rm -rf "$TMP"' EXIT
export SHORTS_TMP="$TMP/cache"

step() { echo "--- $*"; }

step "fixture"
"$PYTHON" "$ROOT/tests/make_fixture.py" "$TMP/src" --duration 40 >/dev/null
INPUT="$TMP/src/source.mp4"

step "preflight"
PRE="$(bash "$ROOT/scripts/preflight.sh" "$INPUT" "$TMP/out" || true)"
WORK="$(jq -r .work_dir <<<"$PRE")"
OUT="$(jq -r .output_dir <<<"$PRE")"
[ -d "$WORK" ] || { echo "preflight did not create a work dir: $PRE"; exit 1; }
cp "$TMP/src/transcript.json" "$TMP/src/transcript.txt" "$WORK/"

step "content type + contact sheet"
"$PYTHON" "$ROOT/scripts/detect_content.py" "$INPUT" --output "$WORK/content_type.json" >/dev/null
"$PYTHON" "$ROOT/scripts/contact_sheet.py" source "$INPUT" --output "$WORK/qa/source.jpg" >/dev/null
[ "$(jq -r .content_type "$WORK/content_type.json")" = "screen" ]

cat > "$WORK/approved_segments.json" <<'JSON'
{"segments": [
  {"id": 1, "start": 4.5, "end": 12.0, "hook_line1": "Smoke test hook", "hook_line2": "Line two"},
  {"id": 2, "start": 17.2, "end": 28.0, "hook_line1": "Second clip", "hook_line2": ""}],
 "style": "bold", "platform": "all", "content_type": "screen"}
JSON

step "snap boundaries"
"$PYTHON" "$ROOT/scripts/snap_boundaries.py" --segments "$WORK/approved_segments.json" \
    --transcript "$WORK/transcript.json" --input-video "$INPUT" \
    --output "$WORK/snapped_segments.json" >/dev/null
# Clip 1 must start on the sentence that begins at 4.2 s and end after a full sentence.
jq -e '.segments[0] | (.start > 4.0 and .start <= 4.2) and (.end >= 13.7 and .end < 14.2)' \
    "$WORK/snapped_segments.json" >/dev/null

step "extract clips"
bash "$ROOT/scripts/extract_clips.sh" --input "$INPUT" --segments "$WORK/snapped_segments.json" \
    --clips-dir "$WORK/clips" >/dev/null
[ -f "$WORK/clips/clip_01.mp4" ] && [ -f "$WORK/clips/clip_02.mp4" ]

step "caption review"
"$PYTHON" "$ROOT/scripts/captions.py" dump --segments "$WORK/snapped_segments.json" \
    --transcript "$WORK/transcript.json" --output "$WORK/caption_words.txt" >/dev/null
FIRST="$(jq '.segments[0].word_start' "$WORK/snapped_segments.json")"
echo "{\"1\": {\"emphasis\": [$FIRST]}}" > "$WORK/caption_edits.json"

step "jump cuts"
# Clip 1 contains an "um"; clip 2 spans a 3.3 s silence in the fixture audio.
"$PYTHON" "$ROOT/scripts/jumpcut.py" --segments "$WORK/snapped_segments.json" \
    --transcript "$WORK/transcript.json" --clips-dir "$WORK/clips" \
    --edits "$WORK/caption_edits.json" > "$WORK/jumpcut.json"
jq -e '.failed == 0' "$WORK/jumpcut.json" >/dev/null
jq -e '.segments[0].jumpcut.filler_words_cut == 1
       and .segments[1].jumpcut.pauses_cut == 1
       and (.segments[1].render_duration < .segments[1].duration - 2.5)' \
    "$WORK/snapped_segments.json" >/dev/null
# The cut clip really is shorter, and video and audio still have the same length.
ffprobe -v error -show_entries stream=duration -of json "$WORK/clips/clip_02.mp4" \
    | jq -e --argjson want "$(jq '.segments[1].render_duration' "$WORK/snapped_segments.json")" \
        '[.streams[].duration | tonumber] | (max - min < 0.05) and ((.[0] - $want) | fabs < 0.1)' >/dev/null
[ -f "$WORK/clips/uncut/clip_02.mp4" ]

step "captions"
"$PYTHON" "$ROOT/scripts/captions.py" apply --segments "$WORK/snapped_segments.json" \
    --transcript "$WORK/transcript.json" --edits "$WORK/caption_edits.json" \
    --output "$WORK/captions.json" >/dev/null
# Every caption fits inside the shortened clip, and the "um" is gone from clip 1.
jq -e --slurpfile seg "$WORK/snapped_segments.json" '
    .by_segment["1"][0].emphasis == true
    and (.by_segment["2"] | length > 5)
    and ([.by_segment["2"][].endMs] | max) <= ($seg[0].segments[1].render_duration * 1000 + 1)
    and ([.by_segment["1"][].text | ascii_downcase | gsub(" "; "")] | index("um") == null)' \
    "$WORK/captions.json" >/dev/null

step "reframe"
"$PYTHON" "$ROOT/scripts/compute_reframe.py" --clips-dir "$WORK/clips" --content-type screen \
    --output "$WORK/reframe.json" >/dev/null
"$PYTHON" "$ROOT/scripts/contact_sheet.py" reframe --clips-dir "$WORK/clips" \
    --reframe "$WORK/reframe.json" --output-dir "$WORK/qa" >/dev/null
jq -e '.clips["clip_01.mp4"].layout == "framed"' "$WORK/reframe.json" >/dev/null

step "b-roll plan"
mkdir -p "$TMP/broll"
ffmpeg -nostdin -y -v error -f lavfi -i "color=c=0x2060c0:s=1280x720:d=1" -frames:v 1 "$TMP/broll/blue-card.png"
"$PYTHON" "$ROOT/scripts/broll.py" catalog --dir "$TMP/broll" --output-dir "$WORK/qa" \
    | jq -e '.count == 1 and (.images | length == 1)' >/dev/null
ANCHOR="$(jq '.segments[1].word_start + 12' "$WORK/snapped_segments.json")"
echo "{\"2\": [{\"file\": \"blue-card.png\", \"from_word\": $ANCHOR, \"to_word\": $((ANCHOR + 5))}]}" \
    > "$WORK/broll_plan.json"
"$PYTHON" "$ROOT/scripts/broll.py" apply --plan "$WORK/broll_plan.json" --dir "$TMP/broll" \
    --segments "$WORK/snapped_segments.json" --transcript "$WORK/transcript.json" \
    --output "$WORK/broll.json" | jq -e '(.problems | length == 0) and .segments[0].cutaways == 1' >/dev/null

if [ "$RENDER" = true ]; then
    step "render (Remotion)"
    node "$ROOT/remotion/render.mjs" --segments "$WORK/snapped_segments.json" \
        --reframe "$WORK/reframe.json" --captions "$WORK/captions.json" \
        --clips-dir "$WORK/clips" --output-dir "$WORK/render" --broll "$WORK/broll.json" \
        --only 2 | tail -1 | jq -e '.segments_failed == 0' >/dev/null
    # The cutaway is really in the picture: mid-cutaway the frame is the blue card.
    BROLL_AT="$(jq '.by_segment["2"][0] | .start + .duration / 2' "$WORK/broll.json")"
    ffmpeg -nostdin -v error -ss "$BROLL_AT" -i "$WORK/render/short_02.mp4" -frames:v 1 \
        -vf "crop=200:200:440:300,scale=1:1" -f rawvideo -pix_fmt rgb24 - | od -An -tu1 \
        | awk '{ exit !($3 > $1 + 60 && $3 > 120) }'
    "$PYTHON" "$ROOT/scripts/contact_sheet.py" renders --dir "$WORK/render" \
        --output-dir "$WORK/qa" >/dev/null

    step "music bed"
    ffmpeg -nostdin -y -v error -f lavfi -i "sine=f=880:d=4" "$TMP/music.wav"
    bash "$ROOT/scripts/add_music.sh" --input-dir "$WORK/render" --music "$TMP/music.wav" \
        --volume -12 | jq -e '.failed == 0 and (.files | length == 1)' >/dev/null
    [ -f "$WORK/render/nomusic/short_02.mp4" ]
    # Running it again must mix from the music-free original, not stack music,
    # and --remove must bring back exactly that original.
    cp "$WORK/render/nomusic/short_02.mp4" "$TMP/original.mp4"
    bash "$ROOT/scripts/add_music.sh" --input-dir "$WORK/render" --music "$TMP/music.wav" \
        --volume -18 | jq -e '.failed == 0' >/dev/null
    cmp -s "$TMP/original.mp4" "$WORK/render/nomusic/short_02.mp4"
    bash "$ROOT/scripts/add_music.sh" --input-dir "$WORK/render" --remove >/dev/null
    cmp -s "$TMP/original.mp4" "$WORK/render/short_02.mp4"
    bash "$ROOT/scripts/add_music.sh" --input-dir "$WORK/render" --music "$TMP/music.wav" \
        --volume -12 | jq -e '.failed == 0' >/dev/null
    # Video untouched, audio still the same length as the picture.
    ffprobe -v error -show_entries stream=duration -of json "$WORK/render/short_02.mp4" \
        | jq -e '[.streams[].duration | tonumber] | (max - min < 0.1)' >/dev/null

    step "export + validate"
    bash "$ROOT/scripts/export.sh" --input-dir "$WORK/render" --platform youtube \
        --output-dir "$OUT" | jq -e '.failed == 0' >/dev/null
    bash "$ROOT/scripts/validate.sh" --output-dir "$OUT" | jq -e '.pass' >/dev/null
    # The exported short has the jump-cut length, not the original one.
    ffprobe -v error -show_entries format=duration -of csv=p=0 "$OUT/short_02_yt.mp4" \
        | awk -v want="$(jq '.segments[1].render_duration' "$WORK/snapped_segments.json")" \
            '{ d = $1 - want; if (d < 0) d = -d; exit !(d < 0.2) }'
fi

echo "smoke test passed ($(uname -s), bash ${BASH_VERSION})"
