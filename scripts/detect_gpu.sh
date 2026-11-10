#!/usr/bin/env bash
# Report GPU capabilities relevant to the pipeline:
#   - CUDA transcription (faster-whisper / CTranslate2)
#   - NVENC export encoding (FFmpeg)
# Usage: bash scripts/detect_gpu.sh
# Output: JSON. Always exits 0; a missing GPU is not an error.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/lib.sh
source "$SCRIPT_DIR/lib.sh"

GPU_NAME=""
DRIVER=""
VRAM_MB=0
if command -v nvidia-smi &>/dev/null; then
    GPU_NAME="$(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -1 | xargs || true)"
    DRIVER="$(nvidia-smi --query-gpu=driver_version --format=csv,noheader 2>/dev/null | head -1 | xargs || true)"
    VRAM_MB="$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits 2>/dev/null | head -1 | xargs || true)"
fi
[[ "$VRAM_MB" =~ ^[0-9]+$ ]] || VRAM_MB=0

ENCODERS=""
if command -v ffmpeg &>/dev/null; then
    ENCODERS="$(ffmpeg -hide_banner -encoders 2>/dev/null | grep -oE '(h264|hevc|av1)_nvenc' | sort -u | paste -sd ' ' - || true)"
fi

NVENC_WORKS=false
if [ -n "$GPU_NAME" ] && shorts_nvenc_works; then
    NVENC_WORKS=true
fi

# Can CTranslate2 see a CUDA device from the skill's venv?
CUDA_DEVICES=0
VENV="$(shorts_venv)"
if [ -n "$VENV" ]; then
    CUDA_DEVICES="$("$VENV/bin/python3" -c 'import ctranslate2; print(ctranslate2.get_cuda_device_count())' 2>/dev/null || echo 0)"
fi
[[ "$CUDA_DEVICES" =~ ^[0-9]+$ ]] || CUDA_DEVICES=0

jq -n --arg name "$GPU_NAME" --arg driver "$DRIVER" --argjson vram "$VRAM_MB" \
    --arg encoders "$ENCODERS" --argjson nvenc "$NVENC_WORKS" --argjson cuda "$CUDA_DEVICES" '
    {
      gpu_detected: ($name != ""),
      gpu_name: (if $name != "" then $name else null end),
      driver_version: (if $driver != "" then $driver else null end),
      vram_mb: (if $vram > 0 then $vram else null end),
      cuda_transcription: ($cuda > 0),
      nvenc_available: $nvenc,
      nvenc_encoders: ($encoders | split(" ") | map(select(. != ""))),
      recommendation: (
        if $name == "" then "No NVIDIA GPU: transcription and encoding run on the CPU (use --model small for long videos)"
        elif $cuda == 0 then "GPU found but not usable for transcription from the skill venv: re-run setup.sh"
        elif $vram > 0 and $vram < 5000 then "Low VRAM: use --model small or --compute-type int8_float16"
        else "GPU transcription available" end)
    }'
