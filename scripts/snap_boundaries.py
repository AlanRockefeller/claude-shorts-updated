#!/usr/bin/env python3
"""Snap segment boundaries to natural audio cut points.

Takes proposed segment times plus the word-level transcript and adjusts each
start/end so a clip never begins or ends mid-word:

1. Start snaps to a word start, preferring a sentence start within 1.5 s.
2. End extends to the next sentence end (. ? !) within 3 s.
3. A short lead-in / tail is added, without bleeding into neighbouring words.
4. Optionally (default) FFmpeg silencedetect refines both cuts using the real
   audio around each boundary. Only a few seconds around each cut are decoded,
   so cost does not depend on the length of the source video.
5. Duration limits are enforced at sentence boundaries, never mid-word.

Usage:
    python3 snap_boundaries.py \
        --segments WORK/approved_segments.json \
        --transcript WORK/transcript.json \
        --input-video /path/to/source.mp4 \
        --output WORK/snapped_segments.json

Output: same structure as the input, with adjusted start/end plus
`word_start` / `word_end` (transcript word indexes covered by the clip).
"""
import argparse
import bisect
import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from shorts_common import fail, media_duration, read_json, write_json  # noqa: E402

SENTENCE_END = re.compile(r"[.?!…。？！][\"'”’)\]]*$")

LEAD_IN = 0.12        # seconds kept before the first word
TAIL = 0.35           # seconds kept after the last word
NEIGHBOUR_GAP = 0.02  # stay this far away from the adjacent (excluded) word


def load_words(transcript_path):
    data = read_json(transcript_path)
    words = data.get("words")
    if words is None:
        # v1 transcripts nested words inside whisper segments.
        words = []
        for seg in data.get("segments", []):
            for w in seg.get("words", []):
                words.append({"text": w["word"], "start": w["start"], "end": w["end"]})
    words = sorted(words, key=lambda w: w["start"])
    for i, w in enumerate(words):
        w.setdefault("i", i)
    return words


def ends_sentence(word):
    return bool(SENTENCE_END.search(word["text"].strip()))


def starts_sentence(words, idx):
    return idx == 0 or ends_sentence(words[idx - 1])


def pick_start_word(words, proposed, window=1.5):
    """Index of the word the clip should start on."""
    starts = [w["start"] for w in words]
    lo = bisect.bisect_left(starts, proposed - window)
    hi = bisect.bisect_right(starts, proposed + window)
    candidates = list(range(lo, hi))
    if not candidates:
        # Nothing nearby (e.g. a long silence): take the next word, else the last.
        idx = bisect.bisect_left(starts, proposed)
        return min(idx, len(words) - 1)

    sentence_starts = [i for i in candidates if starts_sentence(words, i)]
    pool = sentence_starts or candidates
    return min(pool, key=lambda i: abs(words[i]["start"] - proposed))


def pick_end_word(words, proposed, first_idx, window=3.0, tolerance=0.2):
    """Index of the last word of the clip."""
    ends = [w["end"] for w in words]
    last = bisect.bisect_right(ends, proposed + tolerance) - 1
    last = max(last, first_idx)

    if ends_sentence(words[last]):
        return last
    for i in range(last + 1, len(words)):
        if words[i]["start"] > proposed + window:
            break
        if ends_sentence(words[i]):
            return i
    return last


def fit_duration(words, first, last, min_dur, max_dur):
    """Move the end word so the clip fits [min_dur, max_dur], at sentence ends."""
    notes = []
    start = words[first]["start"]

    if words[last]["end"] - start > max_dur:
        limit = start + max_dur - TAIL
        candidate = None
        for i in range(last, first, -1):
            if words[i]["end"] < limit - 20.0:
                break  # don't give up more than 20 s hunting for a sentence end
            if words[i]["end"] <= limit:
                if candidate is None:
                    candidate = i  # fallback: last whole word that fits
                if ends_sentence(words[i]):
                    candidate = i
                    break
        if candidate is not None:
            last = candidate
            notes.append(f"shortened to fit {max_dur:.0f}s max")

    while words[last]["end"] - start < min_dur and last + 1 < len(words):
        last += 1
        while last + 1 < len(words) and not ends_sentence(words[last]):
            last += 1
        note = f"extended to reach {min_dur:.0f}s min"
        if note not in notes:
            notes.append(note)
        if words[last]["end"] - start > max_dur:
            break
    return last, notes


def detect_silences(video_path, start, end, min_duration=0.15, noise_threshold=-35):
    """Silence intervals (absolute seconds) inside [start, end] of the source."""
    start = max(0.0, start)
    if end <= start:
        return []
    cmd = [
        "ffmpeg", "-hide_banner", "-nostats", "-ss", f"{start:.3f}", "-t", f"{end - start:.3f}",
        "-i", video_path, "-vn", "-sn", "-dn",
        "-af", f"silencedetect=noise={noise_threshold}dB:d={min_duration}",
        "-f", "null", "-",
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except (subprocess.TimeoutExpired, OSError):
        return []

    silences = []
    current = None
    for line in result.stderr.splitlines():
        if "silence_start:" in line:
            try:
                current = float(line.split("silence_start:")[1].split()[0])
            except (IndexError, ValueError):
                current = None
        elif "silence_end:" in line and current is not None:
            try:
                finish = float(line.split("silence_end:")[1].split()[0])
            except (IndexError, ValueError):
                continue
            silences.append((start + max(0.0, current), start + finish))
            current = None
    if current is not None:  # silence ran to the end of the analysed window
        silences.append((start + max(0.0, current), end))
    return silences


def snap_segment(words, seg, video_duration, min_dur, max_dur, video_path=None):
    """Return (start, end, first_word_index, last_word_index, notes)."""
    first = pick_start_word(words, seg["start"])
    last = pick_end_word(words, seg["end"], first)
    last, notes = fit_duration(words, first, last, min_dur, max_dur)

    first_word, last_word = words[first], words[last]
    prev_end = words[first - 1]["end"] if first > 0 else 0.0
    next_start = words[last + 1]["start"] if last + 1 < len(words) else video_duration

    start = max(first_word["start"] - LEAD_IN, prev_end + NEIGHBOUR_GAP, 0.0)
    start = min(start, first_word["start"])
    end = min(last_word["end"] + TAIL, next_start - NEIGHBOUR_GAP, video_duration)
    end = max(end, last_word["end"] + 0.05)

    if video_path:
        # Start: if real silence ends right around the first word, cut just before
        # the actual sound onset (Whisper word starts can be late by ~100 ms).
        for s_start, s_end in detect_silences(video_path, first_word["start"] - 1.0,
                                              first_word["start"] + 0.3):
            if first_word["start"] - 0.6 <= s_end <= first_word["start"] + 0.2 \
                    and s_end - s_start >= 0.15:
                start = max(s_start, s_end - 0.08, 0.0)
        # End: if real silence begins right around the last word, keep a short
        # tail of it instead of trusting the (often early) Whisper word end.
        for s_start, s_end in detect_silences(video_path, last_word["end"] - 0.4,
                                              last_word["end"] + 1.5):
            if last_word["end"] - 0.3 <= s_start <= last_word["end"] + 0.6:
                end = max(min(s_start + TAIL, s_end - NEIGHBOUR_GAP), s_start + 0.05)
                break

    start = max(0.0, start)
    end = min(video_duration, end) if video_duration else end
    return round(start, 3), round(end, 3), first, last, notes


def main():
    parser = argparse.ArgumentParser(description="Snap segment boundaries to audio")
    parser.add_argument("--segments", required=True, help="Approved segments JSON")
    parser.add_argument("--transcript", required=True, help="Transcript JSON")
    parser.add_argument("--input-video", required=True, help="Source video")
    parser.add_argument("--output", required=True, help="Output snapped segments JSON")
    parser.add_argument("--no-silence", action="store_true",
                        help="Skip silence detection (word-boundary snapping only)")
    parser.add_argument("--min-duration", type=float, default=5.0)
    parser.add_argument("--max-duration", type=float, default=180.0,
                        help="Longest allowed clip in seconds (default 180: the common "
                             "limit across Shorts, Reels and TikTok)")
    args = parser.parse_args()

    words = load_words(args.transcript)
    if not words:
        fail("No word-level timestamps found in transcript")

    segments_data = read_json(args.segments)
    video_duration = media_duration(args.input_video)
    if not video_duration:
        fail(f"Could not read duration of {args.input_video}")

    adjustments, warnings = [], []
    for seg in segments_data["segments"]:
        old_start, old_end = float(seg["start"]), float(seg["end"])
        if old_end <= old_start:
            fail(f"Segment {seg.get('id', '?')} has end <= start")

        new_start, new_end, first, last, notes = snap_segment(
            words, {"start": old_start, "end": old_end}, video_duration,
            args.min_duration, args.max_duration,
            video_path=None if args.no_silence else args.input_video)

        seg.update({
            "start": new_start,
            "end": new_end,
            "duration": round(new_end - new_start, 3),
            "word_start": words[first]["i"],
            "word_end": words[last]["i"],
        })
        adjustments.append({
            "id": seg.get("id", "?"),
            "old": f"{old_start:.1f}-{old_end:.1f}",
            "new": f"{new_start:.3f}-{new_end:.3f}",
            "duration": seg["duration"],
            "delta_start": f"{(new_start - old_start) * 1000:+.0f}ms",
            "delta_end": f"{(new_end - old_end) * 1000:+.0f}ms",
            "first_words": "".join(w["text"] for w in words[first:first + 6]).strip(),
            "last_words": "".join(w["text"] for w in words[max(first, last - 5):last + 1]).strip(),
            "notes": notes,
        })
        if not ends_sentence(words[last]):
            warnings.append(f"Segment {seg.get('id', '?')} does not end on a sentence boundary")

    ordered = sorted(segments_data["segments"], key=lambda s: s["start"])
    for a, b in zip(ordered, ordered[1:]):
        if b["word_start"] <= a["word_end"]:
            warnings.append(f"Segments {a.get('id', '?')} and {b.get('id', '?')} overlap")

    write_json(args.output, segments_data, indent=2)

    print(json.dumps({
        "action": "snap_boundaries",
        "segments_processed": len(adjustments),
        "silence_refinement": not args.no_silence,
        "adjustments": adjustments,
        "warnings": warnings,
    }, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
