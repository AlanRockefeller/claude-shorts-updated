#!/usr/bin/env bash
# Post-export validation for shorts.
# Usage: bash scripts/validate.sh --output-dir DIR [--max-duration SECONDS]
# Output: JSON with pass/fail per file, issues[], and summary
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/lib.sh
source "$SCRIPT_DIR/lib.sh"

OUTPUT_DIR=""
MIN_DURATION=3
# 180 s is accepted by YouTube Shorts, Instagram Reels and TikTok alike.
MAX_DURATION=180

while [[ $# -gt 0 ]]; do
    case "$1" in
        --output-dir) OUTPUT_DIR="$2"; shift 2 ;;
        --max-duration) MAX_DURATION="$2"; shift 2 ;;
        *) echo "Unknown option: $1" >&2; exit 1 ;;
    esac
done

if [ -z "$OUTPUT_DIR" ]; then
    jq -n '{pass:false, error:"Usage: validate.sh --output-dir DIR [--max-duration SECONDS]"}'; exit 1
fi
if [ ! -d "$OUTPUT_DIR" ]; then
    jq -n --arg d "$OUTPUT_DIR" '{pass:false, error:("Output directory not found: " + $d)}'; exit 1
fi

# Upload size limits (bytes)
LIMIT_YT=$((256 * 1024 * 1024))
LIMIT_TT=$((287 * 1024 * 1024))
LIMIT_IG=$((250 * 1024 * 1024))

RESULTS='[]'
TOTAL=0
FAILED=0

for file in "$OUTPUT_DIR"/short_*.mp4; do
    [ -f "$file" ] || continue
    case "$file" in *.partial.mp4) continue ;; esac
    TOTAL=$((TOTAL + 1))
    ISSUES=()
    name="$(basename "$file")"

    probe="$(ffprobe -v error -print_format json -show_format -show_streams "$file" 2>/dev/null)" || probe='{}'
    jq -e 'type == "object"' >/dev/null 2>&1 <<<"$probe" || probe='{}'
    read -r width height vcodec acodec duration < <(jq -r '
        ([.streams[]? | select(.codec_type=="video")][0] // {}) as $v
        | ([.streams[]? | select(.codec_type=="audio")][0] // {}) as $a
        | "\($v.width // 0) \($v.height // 0) \($v.codec_name // "none") \($a.codec_name // "none") \(.format.duration // 0)"
    ' <<<"$probe")

    if [ "$vcodec" = "none" ]; then
        ISSUES+=("File is not playable or has no video stream")
    else
        if [ "$width" != "1080" ] || [ "$height" != "1920" ]; then
            ISSUES+=("Resolution is ${width}x${height}, expected 1080x1920")
        fi
        if [ "$vcodec" != "h264" ]; then
            ISSUES+=("Video codec is ${vcodec}, expected h264")
        fi
    fi

    if [ "$acodec" = "none" ]; then
        ISSUES+=("No audio track found")
    else
        mean_vol="$(ffmpeg -nostdin -hide_banner -nostats -i "$file" -vn -af volumedetect -f null - 2>&1 \
            | sed -n 's/.*mean_volume: \(-\{0,1\}[0-9.]*\) dB.*/\1/p' | head -1)"
        if [ -n "$mean_vol" ] && awk -v v="$mean_vol" 'BEGIN {exit !(v <= -60)}'; then
            ISSUES+=("Audio appears silent (mean volume: ${mean_vol} dB)")
        fi
    fi

    if awk -v d="$duration" -v lo="$MIN_DURATION" -v hi="$MAX_DURATION" 'BEGIN {exit !(d < lo || d > hi + 0.5)}'; then
        ISSUES+=("Duration $(awk -v d="$duration" 'BEGIN {printf "%.1f", d}')s is outside ${MIN_DURATION}-${MAX_DURATION}s")
    fi

    file_size="$(shorts_file_size "$file")"
    platform="unknown"; limit=0
    case "$name" in
        *_yt.mp4) platform="youtube"; limit=$LIMIT_YT ;;
        *_tt.mp4) platform="tiktok"; limit=$LIMIT_TT ;;
        *_ig.mp4) platform="instagram"; limit=$LIMIT_IG ;;
    esac
    if [ "$limit" -gt 0 ] && [ "$file_size" -gt "$limit" ]; then
        ISSUES+=("File size $((file_size / 1048576))MB exceeds ${platform} limit of $((limit / 1048576))MB")
    fi

    if [ ${#ISSUES[@]} -eq 0 ]; then
        issues_json='[]'
    else
        FAILED=$((FAILED + 1))
        issues_json="$(printf '%s\n' "${ISSUES[@]}" | jq -R . | jq -s .)"
    fi

    RESULTS="$(jq -c --arg file "$name" --arg platform "$platform" --arg dur "$duration" \
        --arg size "$file_size" --argjson issues "$issues_json" \
        '. + [{file: $file, platform: $platform, pass: ($issues | length == 0),
               duration_sec: ($dur | tonumber | . * 10 | round / 10),
               size_mb: ($size | tonumber / 1048576 | . * 10 | round / 10),
               issues: $issues}]' <<<"$RESULTS")"
done

if [ "$TOTAL" -eq 0 ]; then
    jq -n '{pass:false, error:"No short_*.mp4 files found in output directory"}'; exit 1
fi

jq -n --argjson total "$TOTAL" --argjson failed "$FAILED" --argjson files "$RESULTS" \
    '{action: "validate", pass: ($failed == 0), total: $total, passed: ($total - $failed),
      failed: $failed, files: $files}'

[ "$FAILED" -eq 0 ]
