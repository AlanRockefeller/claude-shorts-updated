#!/usr/bin/env bash
# Lay a music bed under rendered shorts, ducked whenever someone speaks.
#
# Usage: bash scripts/add_music.sh --input-dir WORK/render --music TRACK
#            [--volume -20] [--duck 8] [--start 0] [--only 2,5] [--remove]
#
#   --volume  music level in dB relative to its original level (default -20)
#   --duck    how hard speech pushes the music down, as a compression ratio
#             (default 8; 1 = no ducking)
#   --start   where in the track to begin, seconds
#   --remove  take the music off again (restores the original renders)
#
# The track loops if it is shorter than the short and fades out at the end.
# Video is copied untouched; originals are kept in INPUT_DIR/nomusic/ so the
# step can be repeated with other settings. Run it after rendering and before
# export.sh (which then normalises the loudness of the final mix).
set -uo pipefail

INPUT_DIR=""; MUSIC=""; VOLUME="-20"; DUCK="8"; START="0"; ONLY=""; REMOVE="false"
while [[ $# -gt 0 ]]; do
    case "$1" in
        --input-dir) INPUT_DIR="$2"; shift 2 ;;
        --music) MUSIC="$2"; shift 2 ;;
        --volume) VOLUME="$2"; shift 2 ;;
        --duck) DUCK="$2"; shift 2 ;;
        --start) START="$2"; shift 2 ;;
        --only) ONLY="$2"; shift 2 ;;
        --remove) REMOVE="true"; shift ;;
        *) echo "Unknown option: $1" >&2; exit 1 ;;
    esac
done

if [ -z "$INPUT_DIR" ] || [ ! -d "$INPUT_DIR" ]; then
    jq -n '{error:"Usage: add_music.sh --input-dir DIR --music FILE [--volume -20] [--duck 8] [--start 0] [--only 2,5] [--remove]"}'; exit 1
fi
ORIG_DIR="$INPUT_DIR/nomusic"
MIX_TAG="claude-shorts-music-bed"   # marks files this script produced

if [ "$REMOVE" = "true" ]; then
    RESTORED=0
    for original in "$ORIG_DIR"/short_*.mp4; do
        [ -f "$original" ] || continue
        mv -f "$original" "$INPUT_DIR/$(basename "$original")"
        RESTORED=$((RESTORED + 1))
    done
    jq -n --argjson n "$RESTORED" '{action:"remove_music", restored:$n}'
    exit 0
fi

if [ -z "$MUSIC" ] || [ ! -f "$MUSIC" ]; then
    jq -n --arg m "$MUSIC" '{error:("Music file not found: " + $m)}'; exit 1
fi
if ! ffprobe -v error -select_streams a:0 -show_entries stream=codec_type -of csv=p=0 "$MUSIC" 2>/dev/null | grep -q audio; then
    jq -n --arg m "$MUSIC" '{error:("No audio stream in music file: " + $m)}'; exit 1
fi

mkdir -p "$ORIG_DIR"
RESULTS='[]'
FAILED=0

for current in "$INPUT_DIR"/short_*.mp4; do
    [ -f "$current" ] || continue
    name="$(basename "$current")"
    case "$name" in *.partial.mp4) continue ;; esac
    num="${name#short_}"; num="${num%.mp4}"
    if [ -n "$ONLY" ]; then
        case ",$ONLY," in *",$num,"*|*",$((10#$num)),"*) ;; *) continue ;; esac
    fi

    # Always mix from the music-free original so re-runs do not stack music.
    # Our own output carries a metadata tag; anything without it is a fresh
    # render and becomes the new original.
    original="$ORIG_DIR/$name"
    tag="$(ffprobe -v error -show_entries format_tags=comment -of csv=p=0 "$current" 2>/dev/null)"
    if [ "$tag" != "$MIX_TAG" ]; then
        cp -f "$current" "$original"
    elif [ ! -f "$original" ]; then
        RESULTS="$(jq -c --arg file "$name" '. + [{file:$file, status:"failed", error:"already has music and the music-free original is missing; re-render it"}]' <<<"$RESULTS")"
        FAILED=$((FAILED + 1))
        continue
    fi

    duration="$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$original" 2>/dev/null)"
    fade_start="$(awk -v d="$duration" 'BEGIN { s = d - 1.5; if (s < 0) s = 0; printf "%.3f", s }')"

    # Speech (0:a) is split: one copy is the sidechain that ducks the music,
    # the other is mixed with the ducked music. amix must not renormalise.
    filter="[1:a]volume=${VOLUME}dB,aformat=channel_layouts=stereo,aresample=48000,afade=t=in:d=0.5,afade=t=out:st=${fade_start}:d=1.5[bed];"
    filter="${filter}[0:a]aformat=channel_layouts=stereo,aresample=48000,asplit=2[voice][key];"
    filter="${filter}[bed][key]sidechaincompress=threshold=0.02:ratio=${DUCK}:attack=15:release=350[ducked];"
    filter="${filter}[voice][ducked]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[mix]"

    tmp="$INPUT_DIR/${name%.mp4}.partial.mp4"
    status="ok"
    if ffmpeg -nostdin -y -hide_banner -nostats -loglevel error \
            -i "$original" -stream_loop -1 -ss "$START" -i "$MUSIC" \
            -filter_complex "$filter" -map 0:v:0 -map "[mix]" \
            -c:v copy -c:a aac -b:a 320k -t "$duration" -metadata comment="$MIX_TAG" \
            -movflags +faststart "$tmp" 2>"$INPUT_DIR/.music.log"; then
        mv -f "$tmp" "$current"
    else
        rm -f "$tmp"; cp -f "$original" "$current"
        status="failed"; FAILED=$((FAILED + 1))
    fi
    RESULTS="$(jq -c --arg file "$name" --arg status "$status" '. + [{file:$file, status:$status}]' <<<"$RESULTS")"
done

jq -n --arg music "$MUSIC" --arg volume "$VOLUME" --arg duck "$DUCK" --argjson files "$RESULTS" \
    --argjson failed "$FAILED" --arg log "$INPUT_DIR/.music.log" \
    '{action:"add_music", music:$music, volume_db:($volume|tonumber), duck_ratio:($duck|tonumber),
      files:$files, failed:$failed} + (if $failed > 0 then {log:$log} else {} end)'
[ "$FAILED" -eq 0 ]
