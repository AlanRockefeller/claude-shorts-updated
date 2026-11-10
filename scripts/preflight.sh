#!/usr/bin/env bash
# Pre-flight check before the shorts pipeline. Also allocates the per-video
# work directory, so stale files from an earlier run are never picked up.
#
# Usage: bash scripts/preflight.sh <input_file> [output_dir]
# Output: JSON with pass/fail, video info, work_dir, output_dir, warnings
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
# shellcheck source=scripts/lib.sh
source "$SCRIPT_DIR/lib.sh"

INPUT="${1:-}"
OUTPUT_BASE="${2:-./shorts}"

if ! command -v jq &>/dev/null; then
    echo "{\"pass\":false,\"errors\":[\"jq not found — $(shorts_install_hint jq)\"]}"
    exit 1
fi
if [ -z "$INPUT" ]; then
    jq -n '{pass:false, errors:["Usage: preflight.sh <input_file> [output_dir]"]}'
    exit 1
fi

WARNINGS=()
ERRORS=()

# --- Tools -------------------------------------------------------------------
HAVE_FFMPEG=true
for tool in ffmpeg ffprobe; do
    if ! command -v "$tool" &>/dev/null; then
        ERRORS+=("$tool not found — $(shorts_install_hint ffmpeg)")
        HAVE_FFMPEG=false
    fi
done

if ! command -v node &>/dev/null; then
    ERRORS+=("Node.js not found — $(shorts_install_hint nodejs npm) (or: mise use -g node@lts)")
else
    NODE_MAJOR="$(node -p 'process.versions.node.split(".")[0]' 2>/dev/null || echo 0)"
    if [ "$NODE_MAJOR" -lt 20 ]; then
        WARNINGS+=("Node.js $NODE_MAJOR is end-of-life; Node 20 or newer is recommended")
    fi
fi

VENV="$(shorts_venv)"
if [ -z "$VENV" ]; then
    ERRORS+=("Python venv not found — run: bash $ROOT_DIR/setup.sh")
else
    MISSING_PY="$("$VENV/bin/python3" - <<'PY' 2>/dev/null
import importlib.util
missing = [m for m in ("faster_whisper", "cv2", "numpy") if importlib.util.find_spec(m) is None]
print(" ".join(missing))
PY
)"
    if [ -n "$MISSING_PY" ]; then
        ERRORS+=("Missing Python packages in $VENV: $MISSING_PY — run: bash $ROOT_DIR/setup.sh")
    fi
fi

if [ ! -d "$ROOT_DIR/remotion/node_modules/remotion" ]; then
    ERRORS+=("Remotion dependencies not installed — run: (cd $ROOT_DIR/remotion && npm ci)")
fi
if [ ! -f "$ROOT_DIR/models/face_detection_yunet_2023mar.onnx" ]; then
    ERRORS+=("Face detection model missing from $ROOT_DIR/models — re-run install.sh")
fi

# --- Input -------------------------------------------------------------------
PROBE='{}'
if [ ! -f "$INPUT" ]; then
    ERRORS+=("Input file not found: $INPUT")
elif [ "$HAVE_FFMPEG" = true ]; then
    PROBE="$(ffprobe -v error -print_format json -show_format -show_streams "$INPUT" 2>/dev/null || echo '{}')"
    if [ "$(jq '[.streams[]? | select(.codec_type=="video" and (.disposition.attached_pic // 0) == 0)] | length' <<<"$PROBE")" -eq 0 ]; then
        ERRORS+=("Input has no video stream: $INPUT")
    fi
    if [ "$(jq '[.streams[]? | select(.codec_type=="audio")] | length' <<<"$PROBE")" -eq 0 ]; then
        ERRORS+=("Input has no audio stream — there is nothing to transcribe")
    fi
    TRANSFER="$(jq -r '[.streams[]? | select(.codec_type=="video")][0].color_transfer // ""' <<<"$PROBE")"
    if [ "$TRANSFER" = "smpte2084" ] || [ "$TRANSFER" = "arib-std-b67" ]; then
        WARNINGS+=("Source is HDR ($TRANSFER); colours may look washed out in the SDR output")
    fi
    DUR_INT="$(jq -r '(.format.duration // "0") | tonumber | floor' <<<"$PROBE")"
    if [ "$DUR_INT" -gt 0 ] && [ "$DUR_INT" -lt 30 ]; then
        WARNINGS+=("Video is only ${DUR_INT}s long — there may not be enough material for a short")
    fi
fi

# --- Work + output directories -------------------------------------------------
WORK_DIR=""
OUTPUT_DIR="$OUTPUT_BASE"
CACHED_TRANSCRIPT=false
if [ -f "$INPUT" ]; then
    ABS_INPUT="$(realpath "$INPUT")"
    SIZE="$(shorts_file_size "$ABS_INPUT")"
    MTIME="$(stat -c%Y "$ABS_INPUT" 2>/dev/null || stat -f%m "$ABS_INPUT" 2>/dev/null || echo 0)"
    HASH="$(printf '%s|%s|%s' "$ABS_INPUT" "$SIZE" "$MTIME" | sha1sum | cut -c1-8)"
    SLUG="$(basename "${ABS_INPUT%.*}" | tr '[:upper:]' '[:lower:]' | sed -E 's/[^a-z0-9]+/-/g; s/^-+|-+$//g' | cut -c1-40)"
    [ -n "$SLUG" ] || SLUG="video"

    WORK_DIR="$(shorts_tmp_root)/$SLUG-$HASH"
    OUTPUT_DIR="${OUTPUT_BASE%/}/$SLUG"

    if mkdir -p "$WORK_DIR/clips" "$WORK_DIR/render" "$WORK_DIR/qa" 2>/dev/null; then
        [ -f "$WORK_DIR/transcript.json" ] && CACHED_TRANSCRIPT=true
        # Rough need: the source's size again for clips, renders and exports.
        INPUT_KB=$((SIZE / 1024))
        NEEDED_KB=$((INPUT_KB / 2 + 512000))
        AVAIL_KB="$(df -Pk "$WORK_DIR" 2>/dev/null | awk 'NR==2 {print $4}')"
        if [ -n "$AVAIL_KB" ] && [ "$AVAIL_KB" -lt "$NEEDED_KB" ]; then
            WARNINGS+=("Low disk space for $WORK_DIR: $((AVAIL_KB / 1024)) MB free, ~$((NEEDED_KB / 1024)) MB recommended (set SHORTS_TMP to move it)")
        fi
    else
        ERRORS+=("Cannot create work directory: $WORK_DIR (set SHORTS_TMP to a writable location)")
    fi

    if [ -d "$OUTPUT_DIR" ]; then
        EXISTING="$(find "$OUTPUT_DIR" -maxdepth 1 -name 'short_*.mp4' 2>/dev/null | wc -l)"
        if [ "$EXISTING" -gt 0 ]; then
            WARNINGS+=("Output directory $OUTPUT_DIR already has $EXISTING short_*.mp4 files (same-named exports are replaced when re-rendered)")
        fi
    fi
fi

to_json_array() {
    if [ "$#" -eq 0 ]; then echo '[]'; else printf '%s\n' "$@" | jq -R . | jq -s .; fi
}

PASS=true
[ ${#ERRORS[@]} -gt 0 ] && PASS=false

jq -n \
    --argjson pass "$PASS" \
    --arg input "$INPUT" \
    --arg work_dir "$WORK_DIR" \
    --arg output_dir "$OUTPUT_DIR" \
    --arg venv "${VENV:-}" \
    --arg root "$ROOT_DIR" \
    --argjson cached "$CACHED_TRANSCRIPT" \
    --argjson has_ytdlp "$(command -v yt-dlp &>/dev/null && echo true || echo false)" \
    --argjson probe "$PROBE" \
    --argjson errors "$(to_json_array "${ERRORS[@]}")" \
    --argjson warnings "$(to_json_array "${WARNINGS[@]}")" \
    '($probe.streams // []) as $s
     | ([$s[] | select(.codec_type=="video")][0] // {}) as $v
     | ([$s[] | select(.codec_type=="audio")][0] // {}) as $a
     | {
        pass: $pass,
        input: $input,
        skill_root: $root,
        work_dir: $work_dir,
        output_dir: $output_dir,
        venv: $venv,
        transcript_cached: $cached,
        duration: (($probe.format.duration // null) | if . then (tonumber | . * 10 | round / 10) else null end),
        resolution: (if $v.width then "\($v.width)x\($v.height)" else null end),
        fps: ($v.avg_frame_rate // null),
        video_codec: ($v.codec_name // null),
        audio_codec: ($a.codec_name // null),
        yt_dlp_available: $has_ytdlp,
        errors: $errors,
        warnings: $warnings
      }'

[ "$PASS" = "true" ]
