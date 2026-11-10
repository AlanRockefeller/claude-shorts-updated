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
command -v node &>/dev/null || MISSING+=("nodejs")
command -v npm &>/dev/null || MISSING+=("npm")

HAVE_UV=false
command -v uv &>/dev/null && HAVE_UV=true

# Pick a Python >= 3.10. The `python3` that ships with macOS (3.9) is too old,
# so also look for versioned interpreters (Homebrew installs python3.12 etc.).
PYTHON_BIN=""
for candidate in python3 python3.13 python3.12 python3.11 python3.10; do
    if command -v "$candidate" &>/dev/null \
        && "$candidate" -c 'import sys; sys.exit(sys.version_info < (3, 10))' 2>/dev/null; then
        PYTHON_BIN="$(command -v "$candidate")"
        break
    fi
done
# uv can download a suitable Python by itself, so it is enough on its own.
if [ -z "$PYTHON_BIN" ] && [ "$HAVE_UV" = false ]; then
    MISSING+=("python")
fi

if [ ${#MISSING[@]} -gt 0 ]; then
    echo "[System] Missing: ${MISSING[*]} (Python must be 3.10 or newer)"
    echo "[System] Install with: $(shorts_install_hint "${MISSING[@]}")"
    exit 1
fi
echo "[System] ffmpeg, jq, node present; Python: ${PYTHON_BIN:-provided by uv}"

# --- Python virtual environment --------------------------------------------------

if [ -n "${SHORTS_VENV:-}" ]; then
    VENV="$SHORTS_VENV"
elif [ -d "$HOME/.video-skill" ]; then
    VENV="$HOME/.video-skill"
    echo "[Python] Reusing shared venv at ~/.video-skill"
else
    VENV="$HOME/.shorts-skill"
fi

if [ ! -x "$VENV/bin/python3" ]; then
    echo "[Python] Creating venv at $VENV..."
    if [ "$HAVE_UV" = true ] && [ -n "$PYTHON_BIN" ]; then
        uv venv --quiet --python "$PYTHON_BIN" "$VENV"
    elif [ "$HAVE_UV" = true ]; then
        uv venv --quiet --python 3.12 "$VENV"
    else
        "$PYTHON_BIN" -m venv "$VENV"
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
elif [ "$(uname -s)" = "Darwin" ]; then
    echo "[Python] macOS — transcription runs on the CPU (Apple Accelerate); no CUDA needed"
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
