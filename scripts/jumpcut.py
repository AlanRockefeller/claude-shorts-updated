#!/usr/bin/env python3
"""Jump cuts: remove long pauses and filler words from the extracted clips.

For every approved segment this script

1. finds real silences in the clip's audio (FFmpeg silencedetect, with a
   threshold relative to how loud the clip is),
2. collects the words that are being dropped: hesitation sounds (um, uh, ...)
   and any word listed under "delete" in the caption edits file,
3. plans the cuts: pauses longer than --max-pause are shortened to
   --keep-pause, dropped words are removed together with the silence around
   them. Audio that belongs to a word that stays is never cut,
4. re-encodes the clip without the cut ranges (frame-aligned, with a few
   milliseconds of audio fade at each join so cuts do not click), and
5. records the kept ranges in the segments file so captions.py can re-time the
   captions and render.mjs knows the new duration.

Run it after extract_clips.sh and after writing caption edits, before
captions.py apply and compute_reframe.py:

    python3 jumpcut.py --segments WORK/snapped_segments.json \
        --transcript WORK/transcript.json --clips-dir WORK/clips \
        [--edits WORK/caption_edits.json]

Options: --no-fillers (pauses only), --no-silence (fillers only),
--max-pause 0.5, --keep-pause 0.2, --dry-run (plan only), --restore (undo).

The untouched clips are kept in CLIPS_DIR/uncut/, so the script can be re-run
with different settings.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from shorts_common import fail, read_json, video_info, write_json  # noqa: E402

AUTO_FILLERS = {"um", "umm", "uh", "uhh", "uhm", "er", "erm", "hmm", "mm", "mhm", "ah"}

MERGE_GAP = 0.12     # removable ranges closer than this are treated as one
MIN_CUT = 0.12       # don't bother with cuts shorter than this
MIN_KEEP = 0.10      # drop kept slivers shorter than this
HEAD_ROOM = 0.08     # silence kept before the first sound of the clip
TAIL_ROOM = 0.30     # silence kept after the last sound of the clip
FADE = 0.008         # audio fade at each join, seconds


def normalise(text):
    return re.sub(r"[^\w']+", "", text.lower())


# --- Planning (pure functions) ---------------------------------------------------

def subtract(ranges, blockers):
    """Remove `blockers` from `ranges` (both lists of (start, end))."""
    result = []
    for start, end in ranges:
        pieces = [(start, end)]
        for b_start, b_end in blockers:
            next_pieces = []
            for p_start, p_end in pieces:
                if b_end <= p_start or b_start >= p_end:
                    next_pieces.append((p_start, p_end))
                    continue
                if b_start > p_start:
                    next_pieces.append((p_start, b_start))
                if b_end < p_end:
                    next_pieces.append((b_end, p_end))
            pieces = next_pieces
        result.extend(pieces)
    return [(s, e) for s, e in result if e - s > 1e-6]


def plan_cuts(duration, silences, dropped_words, kept_words,
              max_pause=0.5, keep_pause=0.2):
    """Work out which parts of a clip to keep.

    All times are clip-local seconds.
      silences       [(start, end)] measured from the audio
      dropped_words  [(start, end)] words being removed (fillers, deleted words)
      kept_words     [(start, end)] words that stay: never cut into these

    Returns (keep_ranges, stats) where keep_ranges is a list of [start, end].
    """
    # Never cut audio that belongs to a word that stays, whatever the level meter says.
    silences = subtract(silences, kept_words)
    dropped = subtract(dropped_words, kept_words)

    items = sorted([(s, e, "silence") for s, e in silences] + [(s, e, "word") for s, e in dropped])

    groups = []
    for start, end, kind in items:
        start, end = max(0.0, start), min(duration, end)
        if end <= start:
            continue
        if groups and start - groups[-1]["end"] <= MERGE_GAP:
            group = groups[-1]
            if end > group["end"]:
                group["end"], group["right"] = end, kind
            group["words"] += kind == "word"
        else:
            groups.append({"start": start, "end": end, "left": kind, "right": kind,
                           "words": int(kind == "word")})

    cuts, pauses, fillers = [], 0, 0
    for group in groups:
        left, right = group["start"], group["end"]
        if not group["words"] and right - left <= max_pause:
            continue  # an ordinary short pause: leave it alone

        at_start = left <= 0.02
        at_end = right >= duration - 0.02
        # Keep half the target pause on each side that is real silence. A side
        # that ends on a dropped word gets no margin (it would be the word itself).
        margin_left = keep_pause / 2 if group["left"] == "silence" else 0.0
        margin_right = keep_pause / 2 if group["right"] == "silence" else 0.0
        if at_start:
            margin_left = 0.0
            margin_right = HEAD_ROOM if group["right"] == "silence" else 0.0
        if at_end:
            margin_right = 0.0
            margin_left = TAIL_ROOM if group["left"] == "silence" else 0.0

        cut_start, cut_end = left + margin_left, right - margin_right
        if cut_end - cut_start < MIN_CUT:
            continue
        cuts.append((cut_start, cut_end))
        fillers += group["words"]
        pauses += int(group["words"] == 0)

    # Complement of the cuts. These are exact audio times; cut_clip() rounds the
    # video to whole frames without letting the rounding accumulate.
    keep, cursor = [], 0.0
    for cut_start, cut_end in cuts:
        if cut_start - cursor >= MIN_KEEP:
            keep.append([round(cursor, 3), round(cut_start, 3)])
        cursor = max(cursor, cut_end)
    if duration - cursor >= MIN_KEEP:
        keep.append([round(cursor, 3), round(duration, 3)])
    merged = keep

    new_duration = sum(end - start for start, end in merged)
    stats = {
        "original_duration": round(duration, 3),
        "new_duration": round(new_duration, 3),
        "removed_sec": round(duration - new_duration, 3),
        "pauses_cut": pauses,
        "filler_words_cut": fillers,
        "ranges": len(merged),
    }
    return merged, stats


# --- Audio analysis ------------------------------------------------------------------

def mean_volume(path):
    """Mean volume of the audio track in dBFS, or None."""
    result = subprocess.run(
        ["ffmpeg", "-nostdin", "-hide_banner", "-nostats", "-i", path, "-vn",
         "-af", "volumedetect", "-f", "null", "-"],
        capture_output=True, text=True)
    match = re.search(r"mean_volume:\s*(-?[\d.]+) dB", result.stderr)
    return float(match.group(1)) if match else None


def detect_silences(path, min_duration=0.25):
    """Silences in the clip, with the threshold set relative to its loudness
    so quiet recordings are not treated as one long silence."""
    level = mean_volume(path)
    threshold = -35.0 if level is None else max(-55.0, min(-30.0, level - 16.0))
    result = subprocess.run(
        ["ffmpeg", "-nostdin", "-hide_banner", "-nostats", "-i", path, "-vn",
         "-af", f"silencedetect=noise={threshold:.1f}dB:d={min_duration}", "-f", "null", "-"],
        capture_output=True, text=True)

    silences, current = [], None
    for line in result.stderr.splitlines():
        if "silence_start:" in line:
            try:
                current = max(0.0, float(line.split("silence_start:")[1].split()[0]))
            except (IndexError, ValueError):
                current = None
        elif "silence_end:" in line and current is not None:
            try:
                silences.append((current, float(line.split("silence_end:")[1].split()[0])))
            except (IndexError, ValueError):
                pass
            current = None
    if current is not None:
        info = video_info(path)
        if info:
            silences.append((current, info["duration"]))
    return silences, threshold


# --- Cutting ---------------------------------------------------------------------------

def video_frame_ranges(keep, fps):
    """Frame ranges [first, last) matching the audio `keep` ranges.

    Audio is cut at exact times; video can only cut on frame boundaries. Each
    range gets however many frames bring the running video length closest to
    the running audio length, so rounding never accumulates into A/V drift.
    """
    ranges, audio_total, frames_total = [], 0.0, 0
    for start, end in keep:
        audio_total += end - start
        count = max(1, round(audio_total * fps) - frames_total)
        first = round(start * fps)
        ranges.append((first, first + count))
        frames_total += count
    return ranges


def cut_clip(src, dst, keep, fps):
    """Re-encode `src` keeping only the `keep` ranges. Returns error text or None."""
    n = len(keep)
    frames = video_frame_ranges(keep, fps)
    parts = [f"[0:v:0]fps={fps:.6g},split={n}" + "".join(f"[v{i}]" for i in range(n)),
             f"[0:a:0]asplit={n}" + "".join(f"[a{i}]" for i in range(n))]
    video_inputs, audio_inputs = "", ""
    for i, (start, end) in enumerate(keep):
        length = end - start
        fades = []
        if i > 0:
            fades.append(f"afade=t=in:st=0:d={FADE}")
        if i < n - 1:
            fades.append(f"afade=t=out:st={max(0.0, length - FADE):.4f}:d={FADE}")
        audio_chain = ",".join([f"atrim=start={start:.4f}:end={end:.4f}", "asetpts=PTS-STARTPTS"]
                               + fades)
        first, last = frames[i]
        parts.append(f"[v{i}]trim=start_frame={first}:end_frame={last},setpts=PTS-STARTPTS[vk{i}]")
        parts.append(f"[a{i}]{audio_chain}[ak{i}]")
        video_inputs += f"[vk{i}]"
        audio_inputs += f"[ak{i}]"
    # Video and audio are joined separately so each stream stays gapless.
    parts.append(f"{video_inputs}concat=n={n}:v=1:a=0[v]")
    parts.append(f"{audio_inputs}concat=n={n}:v=0:a=1[a]")

    tmp = dst + ".partial.mp4"
    result = subprocess.run(
        ["ffmpeg", "-nostdin", "-y", "-hide_banner", "-nostats", "-loglevel", "error",
         "-i", src, "-filter_complex", ";".join(parts), "-map", "[v]", "-map", "[a]",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "14", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-b:a", "256k", "-movflags", "+faststart", tmp],
        capture_output=True, text=True)
    if result.returncode != 0:
        if os.path.exists(tmp):
            os.unlink(tmp)
        return result.stderr[-600:] or "ffmpeg failed"
    os.replace(tmp, dst)
    return None


def clip_name(seg, index):
    return f"clip_{int(seg.get('id', index + 1)):02d}.mp4"


def restore(segments_data, clips_dir, segments_path):
    uncut_dir = os.path.join(clips_dir, "uncut")
    restored = 0
    for index, seg in enumerate(segments_data["segments"]):
        name = clip_name(seg, index)
        original = os.path.join(uncut_dir, name)
        if os.path.isfile(original):
            os.replace(original, os.path.join(clips_dir, name))
            restored += 1
        for key in ("keep", "render_duration", "jumpcut"):
            seg.pop(key, None)
    write_json(segments_path, segments_data, indent=2)
    print(json.dumps({"action": "jumpcut_restore", "clips_restored": restored}))


def main():
    parser = argparse.ArgumentParser(description="Cut long pauses and filler words out of clips")
    parser.add_argument("--segments", required=True, help="snapped_segments.json (updated in place)")
    parser.add_argument("--transcript", required=True)
    parser.add_argument("--clips-dir", required=True)
    parser.add_argument("--edits", help="caption_edits.json: words under 'delete' are cut too")
    parser.add_argument("--max-pause", type=float, default=0.5,
                        help="Pauses longer than this are shortened (default 0.5 s)")
    parser.add_argument("--keep-pause", type=float, default=0.2,
                        help="Length a shortened pause is left at (default 0.2 s)")
    parser.add_argument("--no-silence", action="store_true", help="Do not shorten pauses")
    parser.add_argument("--no-fillers", action="store_true", help="Do not cut filler/deleted words")
    parser.add_argument("--dry-run", action="store_true", help="Report the plan, change nothing")
    parser.add_argument("--restore", action="store_true", help="Undo: put the uncut clips back")
    args = parser.parse_args()

    if args.keep_pause > args.max_pause:
        fail("--keep-pause must not be longer than --max-pause")

    segments_data = read_json(args.segments)
    if args.restore:
        restore(segments_data, args.clips_dir, args.segments)
        return

    words = read_json(args.transcript).get("words")
    if not words:
        fail("Transcript has no words[] — re-run transcribe.py")
    edits = read_json(args.edits) if args.edits else {}

    uncut_dir = os.path.join(args.clips_dir, "uncut")
    report, failed = [], 0

    for index, seg in enumerate(segments_data["segments"]):
        name = clip_name(seg, index)
        clip_path = os.path.join(args.clips_dir, name)
        original = os.path.join(uncut_dir, name)
        source = original if os.path.isfile(original) else clip_path
        if not os.path.isfile(source):
            fail(f"Clip not found: {clip_path} — run extract_clips.sh first")

        info = video_info(source)
        if not info or not info["duration"]:
            fail(f"Could not read clip: {source}")
        duration, fps = info["duration"], info["fps"] or 30.0

        if "word_start" in seg and "word_end" in seg:
            seg_words = [w for w in words if seg["word_start"] <= w["i"] <= seg["word_end"]]
        else:
            seg_words = [w for w in words
                         if w["start"] >= seg["start"] - 0.05 and w["end"] <= seg["end"] + 0.05]

        deleted = {int(i) for i in edits.get(str(seg.get("id")), {}).get("delete", [])}
        dropped, kept = [], []
        for w in seg_words:
            local = (max(0.0, w["start"] - seg["start"]), min(duration, w["end"] - seg["start"]))
            if local[1] <= local[0]:
                continue
            is_dropped = w["i"] in deleted or normalise(w["text"]) in AUTO_FILLERS
            if is_dropped and not args.no_fillers:
                dropped.append(local)
            else:
                # Whisper often stretches the word before a pause across the
                # pause itself, so only protect a plausible spoken length.
                spoken = 0.25 + 0.08 * len(normalise(w["text"]))
                kept.append((local[0], min(local[1], local[0] + spoken)))

        silences, threshold = ([], None) if args.no_silence else detect_silences(source)
        keep, stats = plan_cuts(duration, silences, dropped, kept,
                                max_pause=args.max_pause, keep_pause=args.keep_pause)
        entry = {"id": seg.get("id"), "clip": name, **stats}
        if threshold is not None:
            entry["silence_threshold_db"] = round(threshold, 1)

        has_cuts = stats["removed_sec"] >= MIN_CUT and len(keep) >= 1
        if args.dry_run:
            entry["status"] = "would cut" if has_cuts else "nothing to cut"
            report.append(entry)
            continue

        if not has_cuts:
            if source == original:  # undo an earlier cut that no longer applies
                os.replace(original, clip_path)
            for key in ("keep", "render_duration", "jumpcut"):
                seg.pop(key, None)
            entry["status"] = "nothing to cut"
            report.append(entry)
            continue

        os.makedirs(uncut_dir, exist_ok=True)
        if source != original:
            shutil.move(clip_path, original)
        error = cut_clip(original, clip_path, keep, fps)
        actual = (video_info(clip_path) or {}).get("duration", 0) if error is None else 0
        if error is None and abs(actual - stats["new_duration"]) > 0.25:
            error = f"cut clip is {actual:.2f}s, expected {stats['new_duration']:.2f}s"
        if error is not None:
            shutil.copyfile(original, clip_path)  # leave a usable, uncut clip behind
            for key in ("keep", "render_duration", "jumpcut"):
                seg.pop(key, None)
            entry.update({"status": "failed", "error": error})
            failed += 1
        else:
            seg["keep"] = keep
            seg["render_duration"] = round(actual, 3)
            seg["jumpcut"] = stats
            entry.update({"status": "cut", "new_duration": round(actual, 3)})
        report.append(entry)

    if not args.dry_run:
        write_json(args.segments, segments_data, indent=2)

    total_removed = round(sum(e["removed_sec"] for e in report if e["status"] in ("cut", "would cut")), 2)
    print(json.dumps({"action": "jumpcut", "dry_run": args.dry_run, "total_removed_sec": total_removed,
                      "failed": failed, "segments": report}, indent=2))
    if failed:
        sys.exit(2)


if __name__ == "__main__":
    main()
