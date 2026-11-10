#!/usr/bin/env bash
# Shared helpers for the shorts shell scripts. Source this file; do not run it.

# Directory for intermediate files. Defaults to the XDG cache dir rather than
# /tmp, which is a RAM-backed tmpfs on Arch-based systems (Omarchy) and others.
shorts_tmp_root() {
    echo "${SHORTS_TMP:-${XDG_CACHE_HOME:-$HOME/.cache}/claude-shorts}"
}

# Python venv used by the pipeline (shared with claude-video if present).
shorts_venv() {
    if [ -n "${SHORTS_VENV:-}" ] && [ -x "$SHORTS_VENV/bin/python3" ]; then
        echo "$SHORTS_VENV"
    elif [ -x "$HOME/.video-skill/bin/python3" ]; then
        echo "$HOME/.video-skill"
    elif [ -x "$HOME/.shorts-skill/bin/python3" ]; then
        echo "$HOME/.shorts-skill"
    fi
}

# "sudo pacman -S --needed ffmpeg jq"-style hint for the local package manager.
shorts_install_hint() {
    if command -v pacman &>/dev/null; then
        echo "sudo pacman -S --needed $*"
    elif command -v apt-get &>/dev/null; then
        echo "sudo apt install $*"
    elif command -v dnf &>/dev/null; then
        echo "sudo dnf install $*"
    elif command -v brew &>/dev/null; then
        echo "brew install $*"
    else
        echo "install: $*"
    fi
}

# Is the H.264 NVENC encoder actually usable (not merely compiled in)?
shorts_nvenc_works() {
    command -v nvidia-smi &>/dev/null || return 1
    ffmpeg -hide_banner -encoders 2>/dev/null | grep -q "h264_nvenc" || return 1
    ffmpeg -v error -f lavfi -i "color=black:s=256x256:r=30:d=0.2" \
        -c:v h264_nvenc -f null - &>/dev/null
}

# File size in bytes (GNU and BSD stat).
shorts_file_size() {
    stat -c%s "$1" 2>/dev/null || stat -f%z "$1" 2>/dev/null || echo 0
}
