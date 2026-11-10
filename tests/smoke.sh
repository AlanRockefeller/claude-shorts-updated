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
  {"id": 2, "start": 24.0, "end": 33.0, "hook_line1": "Second clip", "hook_line2": ""}],
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

step "reframe"
"$PYTHON" "$ROOT/scripts/compute_reframe.py" --clips-dir "$WORK/clips" --content-type screen \
    --output "$WORK/reframe.json" >/dev/null
"$PYTHON" "$ROOT/scripts/contact_sheet.py" reframe --clips-dir "$WORK/clips" \
    --reframe "$WORK/reframe.json" --output-dir "$WORK/qa" >/dev/null
jq -e '.clips["clip_01.mp4"].layout == "framed"' "$WORK/reframe.json" >/dev/null

step "captions"
"$PYTHON" "$ROOT/scripts/captions.py" dump --segments "$WORK/snapped_segments.json" \
    --transcript "$WORK/transcript.json" --output "$WORK/caption_words.txt" >/dev/null
FIRST="$(jq '.segments[0].word_start' "$WORK/snapped_segments.json")"
echo "{\"1\": {\"emphasis\": [$FIRST]}}" > "$WORK/caption_edits.json"
"$PYTHON" "$ROOT/scripts/captions.py" apply --segments "$WORK/snapped_segments.json" \
    --transcript "$WORK/transcript.json" --edits "$WORK/caption_edits.json" \
    --output "$WORK/captions.json" >/dev/null
jq -e '.by_segment["1"][0].emphasis == true and (.by_segment["2"] | length > 5)' \
    "$WORK/captions.json" >/dev/null

if [ "$RENDER" = true ]; then
    step "render (Remotion)"
    node "$ROOT/remotion/render.mjs" --segments "$WORK/snapped_segments.json" \
        --reframe "$WORK/reframe.json" --captions "$WORK/captions.json" \
        --clips-dir "$WORK/clips" --output-dir "$WORK/render" --only 1 | tail -1 \
        | jq -e '.segments_failed == 0' >/dev/null
    "$PYTHON" "$ROOT/scripts/contact_sheet.py" renders --dir "$WORK/render" \
        --output-dir "$WORK/qa" >/dev/null

    step "export + validate"
    bash "$ROOT/scripts/export.sh" --input-dir "$WORK/render" --platform youtube \
        --output-dir "$OUT" | jq -e '.failed == 0' >/dev/null
    bash "$ROOT/scripts/validate.sh" --output-dir "$OUT" | jq -e '.pass' >/dev/null
fi

echo "smoke test passed"
