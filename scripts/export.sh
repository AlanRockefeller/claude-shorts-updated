#!/usr/bin/env bash
# Platform-specific FFmpeg encoding for rendered shorts.
#
# Usage: bash scripts/export.sh --input-dir DIR --output-dir DIR
#            [--platform youtube|tiktok|instagram|all] [--force] [--cpu]
#
# - Audio is loudness-normalised to -14 LUFS with two-pass loudnorm (measured
#   once per short, then applied linearly, so dynamics are not pumped).
# - Uses NVENC when it actually works, and falls back to libx264 if a GPU
#   encode fails.
# - An existing export is kept only if it is newer than its render; use --force
#   to re-encode everything.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/lib.sh
source "$SCRIPT_DIR/lib.sh"

INPUT_DIR=""
PLATFORM="all"
OUTPUT_DIR="./shorts"
FORCE="false"
USE_GPU="auto"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --input-dir) INPUT_DIR="$2"; shift 2 ;;
        --platform) PLATFORM="$2"; shift 2 ;;
        --output-dir) OUTPUT_DIR="$2"; shift 2 ;;
        --force) FORCE="true"; shift ;;
        --cpu) USE_GPU="no"; shift ;;
        *) echo "Unknown option: $1" >&2; exit 1 ;;
    esac
done

usage='export.sh --input-dir DIR [--platform youtube|tiktok|instagram|all] [--output-dir DIR] [--force] [--cpu]'
if [ -z "$INPUT_DIR" ]; then
    jq -n --arg u "$usage" '{error: ("Usage: " + $u)}'; exit 1
fi
if [ ! -d "$INPUT_DIR" ]; then
    jq -n --arg d "$INPUT_DIR" '{error: ("Input directory not found: " + $d)}'; exit 1
fi

case "$PLATFORM" in
    all) PLATFORMS=(youtube tiktok instagram) ;;
    youtube|tiktok|instagram) PLATFORMS=("$PLATFORM") ;;
    *) jq -n --arg p "$PLATFORM" '{error: ("Unknown platform: " + $p)}'; exit 1 ;;
esac

mkdir -p "$OUTPUT_DIR"
LOG_FILE="$OUTPUT_DIR/.export.log"
: > "$LOG_FILE"

HAS_NVENC="false"
if [ "$USE_GPU" = "auto" ] && shorts_nvenc_works; then
    HAS_NVENC="true"
fi

LOUDNORM_TARGET="I=-14:TP=-1:LRA=11"

# Pass 1: measure loudness. Prints the loudnorm filter string to use.
loudnorm_filter() {
    local input="$1" stats
    stats="$(ffmpeg -nostdin -hide_banner -nostats -i "$input" -vn \
        -af "loudnorm=${LOUDNORM_TARGET}:print_format=json" -f null - 2>&1 \
        | awk '/^[{]/{capture=1} capture{print} /^[}]/{capture=0}' \
        | jq -c 'select(.input_i != null)' 2>/dev/null | tail -1)"

    local filter=""
    if [ -n "$stats" ]; then
        filter="$(jq -r --arg target "$LOUDNORM_TARGET" '
            select((.input_i | test("inf")) | not)
            | "loudnorm=\($target):measured_I=\(.input_i):measured_TP=\(.input_tp):measured_LRA=\(.input_lra):measured_thresh=\(.input_thresh):offset=\(.target_offset):linear=true"
        ' <<<"$stats" 2>/dev/null)"
    fi
    # Fall back to single-pass if measurement failed (or the clip is silent).
    echo "${filter:-loudnorm=${LOUDNORM_TARGET}}"
}

# encode <input> <output> <platform> <audio_filter>
encode() {
    local input="$1" output="$2" platform="$3" afilter="$4"
    local -a rate audio extra_cpu extra_gpu
    case "$platform" in
        youtube)
            rate=(-b:v 12M -maxrate 14M -bufsize 24M -profile:v high -level 4.2)
            audio=(-c:a aac -b:a 192k -ar 48000)
            extra_cpu=(); extra_gpu=() ;;
        tiktok)
            rate=(-maxrate 10M -bufsize 20M)
            audio=(-c:a aac -b:a 128k -ar 44100)
            extra_cpu=(-crf 18); extra_gpu=(-rc vbr -cq 19 -b:v 0) ;;
        instagram)
            rate=(-b:v 4500k -maxrate 5000k -bufsize 10M -profile:v high -level 4.2)
            audio=(-c:a aac -b:a 128k -ar 44100)
            extra_cpu=(); extra_gpu=() ;;
    esac
    local -a common=(-af "$afilter" "${audio[@]}" -pix_fmt yuv420p
                     -colorspace bt709 -color_primaries bt709 -color_trc bt709
                     -movflags +faststart)
    local tmp="${output%.mp4}.partial.mp4"

    if [ "$HAS_NVENC" = "true" ]; then
        if ffmpeg -nostdin -y -hide_banner -nostats -loglevel error -i "$input" \
            -c:v h264_nvenc -preset p5 -tune hq ${extra_gpu[@]+"${extra_gpu[@]}"} "${rate[@]}" \
            "${common[@]}" "$tmp" 2>>"$LOG_FILE"; then
            mv -f "$tmp" "$output"; ENCODER_USED="h264_nvenc"; return 0
        fi
        echo "[export] NVENC failed for $input ($platform); retrying on CPU" >>"$LOG_FILE"
    fi

    if ffmpeg -nostdin -y -hide_banner -nostats -loglevel error -i "$input" \
        -c:v libx264 -preset slow ${extra_cpu[@]+"${extra_cpu[@]}"} "${rate[@]}" \
        "${common[@]}" "$tmp" 2>>"$LOG_FILE"; then
        mv -f "$tmp" "$output"; ENCODER_USED="libx264"; return 0
    fi
    rm -f "$tmp"
    return 1
}

RESULTS='[]'
COUNT=0
FAILED=0
ENCODER_USED=""

for input_file in "$INPUT_DIR"/short_*.mp4; do
    [ -f "$input_file" ] || continue
    COUNT=$((COUNT + 1))
    num="$(basename "$input_file" .mp4)"; num="${num#short_}"
    afilter=""

    for plat in "${PLATFORMS[@]}"; do
        case "$plat" in
            youtube) suffix="_yt" ;;
            tiktok) suffix="_tt" ;;
            instagram) suffix="_ig" ;;
        esac
        output_file="$OUTPUT_DIR/short_${num}${suffix}.mp4"
        status="exported"; encoder=""

        if [ -f "$output_file" ] && [ "$FORCE" != "true" ] && [ "$output_file" -nt "$input_file" ]; then
            status="skipped"
        else
            [ -n "$afilter" ] || afilter="$(loudnorm_filter "$input_file")"
            if encode "$input_file" "$output_file" "$plat" "$afilter"; then
                encoder="$ENCODER_USED"
            else
                status="failed"; FAILED=$((FAILED + 1))
            fi
        fi

        size_mb=0; duration=0
        if [ -f "$output_file" ] && [ "$status" != "failed" ]; then
            size_mb="$(awk -v b="$(shorts_file_size "$output_file")" 'BEGIN {printf "%.1f", b / 1048576}')"
            duration="$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$output_file" 2>/dev/null | awk '{printf "%.1f", $1}')"
        fi
        RESULTS="$(jq -c --arg file "$output_file" --arg platform "$plat" --arg status "$status" \
            --arg encoder "$encoder" --argjson size "${size_mb:-0}" --argjson dur "${duration:-0}" \
            '. + [{file: $file, platform: $platform, status: $status, duration_sec: $dur, size_mb: $size}
                  + (if $encoder != "" then {encoder: $encoder} else {} end)]' <<<"$RESULTS")"
    done
done

if [ "$COUNT" -eq 0 ]; then
    jq -n --arg d "$INPUT_DIR" '{error: ("No short_*.mp4 files found in " + $d)}'; exit 1
fi

jq -n --arg platform "$PLATFORM" --argjson nvenc "$HAS_NVENC" --argjson count "$COUNT" \
    --argjson failed "$FAILED" --arg out "$OUTPUT_DIR" --arg log "$LOG_FILE" --argjson files "$RESULTS" \
    '{action: "export", platform: $platform, nvenc: $nvenc, shorts: $count, failed: $failed,
      output_dir: $out, files: $files}
     + (if $failed > 0 then {log: $log} else {} end)'

[ "$FAILED" -eq 0 ]
