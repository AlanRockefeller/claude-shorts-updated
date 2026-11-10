#!/usr/bin/env bash
# Remove the shorts skill. Pass --all to also delete its venv and cached work files.
set -euo pipefail

SKILL_DIR="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/skills/shorts"
CACHE_DIR="${SHORTS_TMP:-${XDG_CACHE_HOME:-$HOME/.cache}/claude-shorts}"

echo "Uninstalling claude-shorts..."
if [ -L "$SKILL_DIR" ]; then
    rm -f "$SKILL_DIR"
else
    rm -rf "$SKILL_DIR"
fi
echo "  Removed skill: $SKILL_DIR"

if [ "${1:-}" = "--all" ]; then
    rm -rf "$CACHE_DIR" "$HOME/.shorts-skill"
    echo "  Removed work files: $CACHE_DIR"
    echo "  Removed venv: $HOME/.shorts-skill"
else
    echo "  Kept venv (~/.shorts-skill) and work files ($CACHE_DIR); use --all to remove them"
fi
echo "Restart Claude Code to complete removal."
