#!/usr/bin/env bash
# Cut each snapped segment out of the source video (stream copy: near-instant
# and lossless). Old clips in the target directory are removed first so a
# previous run can never leak into this one.
#
# Usage: bash scripts/extract_clips.sh --input VIDEO --segments snapped_segments.json --clips-dir DIR
set -uo pipefail

INPUT=""; SEGMENTS=""; CLIPS_DIR=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --input) INPUT="$2"; shift 2 ;;
        --segments) SEGMENTS="$2"; shift 2 ;;
        --clips-dir) CLIPS_DIR="$2"; shift 2 ;;
        *) echo "Unknown option: $1" >&2; exit 1 ;;
    esac
done

if [ -z "$INPUT" ] || [ -z "$SEGMENTS" ] || [ -z "$CLIPS_DIR" ]; then
    jq -n '{error:"Usage: extract_clips.sh --input VIDEO --segments FILE --clips-dir DIR"}'; exit 1
fi
if [ ! -f "$INPUT" ] || [ ! -f "$SEGMENTS" ]; then
    jq -n '{error:"Input video or segments file not found"}'; exit 1
fi

mkdir -p "$CLIPS_DIR"
rm -f "$CLIPS_DIR"/clip_*.mp4

RESULTS='[]'
FAILED=0
while IFS=$'\t' read -r id start end; do
    name="$(printf 'clip_%02d.mp4' "$id")"
    out="$CLIPS_DIR/$name"
    status="ok"
    # Input seeking + stream copy starts on the preceding keyframe; the MP4 edit
    # list hides the pre-roll, so playback still begins at the requested time.
    if ! ffmpeg -nostdin -y -hide_banner -nostats -loglevel error -ss "$start" -to "$end" -i "$INPUT" \
            -map 0:v:0 -map 0:a:0 -c copy "$out" 2>/dev/null; then
        # Codecs that cannot be stream-copied into MP4: re-encode at high quality.
        if ! ffmpeg -nostdin -y -hide_banner -nostats -loglevel error -ss "$start" -to "$end" -i "$INPUT" \
                -map 0:v:0 -map 0:a:0 -c:v libx264 -preset veryfast -crf 14 -pix_fmt yuv420p \
                -c:a aac -b:a 256k "$out" 2>/dev/null; then
            status="failed"; FAILED=$((FAILED + 1)); rm -f "$out"
        else
            status="re-encoded"
        fi
    fi
    RESULTS="$(jq -c --arg clip "$name" --arg status "$status" --argjson id "$id" \
        --argjson start "$start" --argjson end "$end" \
        '. + [{id: $id, clip: $clip, start: $start, end: $end, status: $status}]' <<<"$RESULTS")"
done < <(jq -r '.segments | to_entries[] | [(.value.id // (.key + 1)), .value.start, .value.end] | @tsv' "$SEGMENTS")

jq -n --arg dir "$CLIPS_DIR" --argjson clips "$RESULTS" --argjson failed "$FAILED" \
    '{action: "extract_clips", clips_dir: $dir, extracted: (($clips | length) - $failed), failed: $failed, clips: $clips}'
[ "$FAILED" -eq 0 ]
