#!/usr/bin/env bash
# Install dependencies for claude-shorts (Python venv + Remotion).
# Usage: bash setup.sh
#
# Environment:
#   SHORTS_VENV   Use/create the venv at this path (default: ~/.shorts-skill,
#                 or ~/.video-skill if the claude-video skill already made one)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/lib.sh
source "$SCRIPT_DIR/scripts/lib.sh"

echo "=== claude-shorts dependency setup ==="
echo ""

# --- System dependencies -------------------------------------------------------

MISSING=()
command -v ffmpeg &>/dev/null || MISSING+=("ffmpeg")
command -v jq &>/dev/null || MISSING+=("jq")
command -v python3 &>/dev/null || MISSING+=("python")
command -v node &>/dev/null || MISSING+=("nodejs")
command -v npm &>/dev/null || MISSING+=("npm")

if [ ${#MISSING[@]} -gt 0 ]; then
    echo "[System] Missing: ${MISSING[*]}"
    echo "[System] Install with: $(shorts_install_hint "${MISSING[@]}")"
    echo "         (Node can also come from a version manager, e.g. 'mise use -g node@lts')"
    exit 1
fi
echo "[System] ffmpeg, jq, python3, node present"

PY_OK="$(python3 -c 'import sys; print(int(sys.version_info >= (3, 10)))')"
if [ "$PY_OK" != "1" ]; then
    echo "[Python] Python 3.10+ is required (found $(python3 --version))"
    exit 1
fi

# --- Python virtual environment --------------------------------------------------

if [ -n "${SHORTS_VENV:-}" ]; then
    VENV="$SHORTS_VENV"
elif [ -d "$HOME/.video-skill" ]; then
    VENV="$HOME/.video-skill"
    echo "[Python] Reusing shared venv at ~/.video-skill"
else
    VENV="$HOME/.shorts-skill"
fi

HAVE_UV=false
command -v uv &>/dev/null && HAVE_UV=true

if [ ! -x "$VENV/bin/python3" ]; then
    echo "[Python] Creating venv at $VENV..."
    if [ "$HAVE_UV" = true ]; then
        uv venv --quiet "$VENV"
    else
        python3 -m venv "$VENV"
    fi
fi

pip_install() {
    if [ "$HAVE_UV" = true ]; then
        uv pip install --quiet --python "$VENV/bin/python3" "$@"
    else
        "$VENV/bin/python3" -m pip install --quiet "$@"
    fi
}

echo "[Python] Installing packages (faster-whisper, numpy, opencv)..."
if "$VENV/bin/python3" -c "import cv2; assert hasattr(cv2, 'FaceDetectorYN')" 2>/dev/null; then
    # An OpenCV build is already present (e.g. shared venv); don't install a second one.
    pip_install "faster-whisper>=1.1.0" "numpy>=1.26"
else
    pip_install -r "$SCRIPT_DIR/requirements.txt"
fi

# GPU transcription needs the CUDA runtime libraries that CTranslate2 loads
# (cuBLAS, plus cuDNN for some builds). PyTorch is NOT required.
if command -v nvidia-smi &>/dev/null && nvidia-smi &>/dev/null; then
    CUDA_PIP="$("$VENV/bin/python3" "$SCRIPT_DIR/scripts/cuda_libs.py" --pip 2>/dev/null || true)"
    if [ -z "$CUDA_PIP" ]; then
        echo "[Python] NVIDIA GPU detected — CUDA runtime libraries already available"
    else
        echo "[Python] NVIDIA GPU detected — installing CUDA runtime wheels: $CUDA_PIP"
        # shellcheck disable=SC2086
        pip_install $CUDA_PIP \
            || echo "[Python] WARNING: could not install CUDA wheels; transcription will use the CPU"
    fi
else
    echo "[Python] No NVIDIA GPU detected — transcription will run on the CPU"
fi

"$VENV/bin/python3" - <<'PY'
import ctranslate2, cv2, faster_whisper
print(f"[Python] faster-whisper {faster_whisper.__version__}, OpenCV {cv2.__version__}, "
      f"CUDA devices visible: {ctranslate2.get_cuda_device_count()}")
PY
echo "[Python] Venv ready: $VENV"
echo ""

# --- Node.js / Remotion ----------------------------------------------------------

if [ ! -f "$SCRIPT_DIR/remotion/package.json" ]; then
    echo "[Node] ERROR: remotion/package.json not found next to setup.sh"
    exit 1
fi

echo "[Node] Installing Remotion dependencies..."
(cd "$SCRIPT_DIR/remotion" && { npm ci --no-audit --no-fund --loglevel=error || npm install --no-audit --no-fund --loglevel=error; })

echo "[Node] Making sure Remotion has a browser (Chrome Headless Shell)..."
if ! (cd "$SCRIPT_DIR/remotion" && npx --no-install remotion browser ensure >/dev/null 2>&1); then
    echo "[Node] NOTE: could not pre-download the browser. Remotion will retry on first render."
    echo "       To use an installed Chrome/Chromium instead: export SHORTS_CHROME=\$(command -v chromium)"
fi
echo "[Node] Remotion ready"
echo ""

echo "=== Setup complete ==="
echo ""
echo "Python venv: $VENV"
echo "Remotion:    $SCRIPT_DIR/remotion/"
echo "Work files:  $(shorts_tmp_root)  (override with SHORTS_TMP)"
if ! command -v yt-dlp &>/dev/null; then
    echo "Optional:    yt-dlp, to start from a video URL — $(shorts_install_hint yt-dlp)"
fi
echo ""
if [ "${SHORTS_FROM_INSTALL:-}" != "1" ]; then
    echo "Next: bash install.sh   (then use /shorts in Claude Code)"
fi
