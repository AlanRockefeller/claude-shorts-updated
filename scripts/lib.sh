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

# Install hint for the local package manager. Takes generic names
# (ffmpeg jq python nodejs npm yt-dlp) and maps them where distros differ.
shorts_install_hint() {
    local manager="" pkg out=""
    if command -v pacman &>/dev/null; then manager="pacman"
    elif command -v apt-get &>/dev/null; then manager="apt"
    elif command -v dnf &>/dev/null; then manager="dnf"
    elif command -v brew &>/dev/null; then manager="brew"
    fi
    for pkg in "$@"; do
        case "$manager:$pkg" in
            brew:nodejs) pkg="node" ;;
            brew:npm) continue ;;                      # ships with node
            apt:python) pkg="python3 python3-venv" ;;
            dnf:python) pkg="python3" ;;
        esac
        out="$out $pkg"
    done
    case "$manager" in
        pacman) echo "sudo pacman -S --needed$out" ;;
        apt) echo "sudo apt install$out" ;;
        dnf) echo "sudo dnf install$out" ;;
        brew) echo "brew install$out" ;;
        *) echo "install:$out (macOS: get Homebrew from https://brew.sh)" ;;
    esac
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

# File modification time, seconds since epoch (GNU and BSD stat).
shorts_file_mtime() {
    stat -c%Y "$1" 2>/dev/null || stat -f%m "$1" 2>/dev/null || echo 0
}

# Absolute path of an existing file (macOS before 13 has no realpath).
shorts_abspath() {
    if command -v realpath &>/dev/null; then
        realpath "$1"
    else
        (cd "$(dirname "$1")" && printf '%s/%s\n' "$(pwd -P)" "$(basename "$1")")
    fi
}

# Short stable hash of a string (sha1sum on Linux, shasum on macOS).
shorts_hash8() {
    if command -v sha1sum &>/dev/null; then
        printf '%s' "$1" | sha1sum | cut -c1-8
    elif command -v shasum &>/dev/null; then
        printf '%s' "$1" | shasum | cut -c1-8
    else
        printf '%s' "$1" | cksum | cut -d' ' -f1
    fi
}

# Saved preferences (caption style, platform, jump cuts, music, b-roll folder...).
shorts_profile_path() {
    echo "${SHORTS_PROFILE:-${XDG_CONFIG_HOME:-$HOME/.config}/claude-shorts/profile.md}"
}
