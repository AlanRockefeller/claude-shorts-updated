#!/usr/bin/env bash
# Install the shorts skill into ~/.claude/skills/shorts and set up its
# dependencies there.
#
# Usage:
#   bash install.sh           Copy the skill and install dependencies
#   bash install.sh --link    Symlink this checkout instead (for development:
#                             edits take effect immediately)
#   bash install.sh --no-setup   Copy/link only; skip dependency setup
set -euo pipefail

# Physical path: the script may be reached through an existing install symlink.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
SKILLS_DIR="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/skills"
SKILL_DIR="$SKILLS_DIR/shorts"

MODE="copy"
RUN_SETUP="true"
for arg in "$@"; do
    case "$arg" in
        --link) MODE="link" ;;
        --no-setup) RUN_SETUP="false" ;;
        -h|--help) sed -n '2,10p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "Unknown option: $arg" >&2; exit 1 ;;
    esac
done

echo "=== Installing shorts skill ==="
mkdir -p "$SKILLS_DIR"

if [ ! -L "$SKILL_DIR" ] && [ -d "$SKILL_DIR" ] && [ "$SCRIPT_DIR" -ef "$SKILL_DIR" ]; then
    # The checkout itself lives at the install location: replacing the
    # destination would delete the source.
    echo "This checkout is already at $SKILL_DIR; nothing to copy or link"
elif [ "$MODE" = "link" ]; then
    if [ -e "$SKILL_DIR" ] && [ ! -L "$SKILL_DIR" ]; then
        echo "Replacing existing copy at $SKILL_DIR with a symlink"
        rm -rf "$SKILL_DIR"
    fi
    ln -sfn "$SCRIPT_DIR" "$SKILL_DIR"
    echo "Linked: $SKILL_DIR -> $SCRIPT_DIR"
else
    if [ -L "$SKILL_DIR" ]; then
        rm -f "$SKILL_DIR"
    fi
    mkdir -p "$SKILL_DIR"
    # Replace code directories wholesale so files removed upstream don't linger,
    # but keep remotion/node_modules to make re-installs fast.
    rm -rf "$SKILL_DIR/scripts" "$SKILL_DIR/references" "$SKILL_DIR/models" \
           "$SKILL_DIR/remotion/src" "$SKILL_DIR/remotion/public"

    cp "$SCRIPT_DIR/SKILL.md" "$SCRIPT_DIR/setup.sh" "$SCRIPT_DIR/requirements.txt" \
       "$SCRIPT_DIR/LICENSE" "$SKILL_DIR/"
    cp -R "$SCRIPT_DIR/scripts" "$SCRIPT_DIR/references" "$SCRIPT_DIR/models" "$SKILL_DIR/"
    rm -rf "$SKILL_DIR/scripts/__pycache__"
    chmod +x "$SKILL_DIR"/scripts/*.sh "$SKILL_DIR"/scripts/*.py 2>/dev/null || true

    tar -C "$SCRIPT_DIR" --exclude='node_modules' --exclude='.remotion' -cf - remotion \
        | tar -C "$SKILL_DIR" -xf -
    echo "Copied to: $SKILL_DIR"
fi

if [ "$RUN_SETUP" = "true" ]; then
    echo ""
    # Dependencies must live next to the installed skill (remotion/node_modules),
    # so run setup from the installed location.
    SHORTS_FROM_INSTALL=1 bash "$SKILL_DIR/setup.sh"
fi

echo ""
echo "Installed. Use /shorts <video file or URL> in Claude Code."
