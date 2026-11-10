#!/usr/bin/env python3
"""B-roll: cutaway footage or stills shown over the speaker while they talk.

The footage comes from a folder the user provides (their own clips, screen
captures, product shots, stock they have licensed).

  catalog  Index a folder and build thumbnail sheets so Claude can see what
           each file shows:
               python3 broll.py catalog --dir ~/Videos/broll --output-dir WORK/qa

  apply    Turn Claude's plan into exact timings for the renderer:
               python3 broll.py apply --plan WORK/broll_plan.json --dir ~/Videos/broll \
                   --segments WORK/snapped_segments.json --transcript WORK/transcript.json \
                   --output WORK/broll.json

Plan file, keyed by segment id. Each cutaway is anchored to the words it
illustrates (word indexes from caption_words.txt), so it lands on the right
moment even after jump cuts:

    {
      "1": [
        {"file": "dashboard.mp4", "from_word": 421, "to_word": 428},
        {"file": "office.jpg", "from_word": 440, "to_word": 446, "source_start": 0}
      ]
    }

`source_start` (seconds) picks where in a video file to start. Run this after
jumpcut.py if jump cuts are used.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from shorts_common import fail, grab_frames, read_json, retime, video_info, write_json  # noqa: E402

VIDEO_EXT = {".mp4", ".mov", ".m4v", ".webm", ".mkv"}
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp"}
MIN_DURATION = 1.2    # shorter cutaways read as a glitch
MAX_DURATION = 8.0    # longer ones lose the speaker
LEAD = 0.15           # start slightly before the anchoring word


def kind_of(path):
    ext = os.path.splitext(path)[1].lower()
    if ext in VIDEO_EXT:
        return "video"
    if ext in IMAGE_EXT:
        return "image"
    return None


def list_media(directory):
    items = []
    for root, _, files in os.walk(directory):
        for name in sorted(files):
            path = os.path.join(root, name)
            kind = kind_of(path)
            if kind and not name.startswith("."):
                items.append((os.path.relpath(path, directory), path, kind))
    return sorted(items)


def cmd_catalog(args):
    import cv2

    from contact_sheet import fit, grid, label, save

    if not os.path.isdir(args.dir):
        fail(f"B-roll folder not found: {args.dir}")
    media = list_media(args.dir)
    if not media:
        fail(f"No video or image files found in {args.dir}")

    entries, cells = [], []
    for index, (rel, path, kind) in enumerate(media, start=1):
        entry = {"n": index, "file": rel, "kind": kind}
        frame = None
        if kind == "video":
            info = video_info(path)
            if not info:
                continue
            entry.update({"duration": round(info["duration"], 1),
                          "resolution": f"{info['width']}x{info['height']}"})
            frames = grab_frames(path, [info["duration"] * 0.3], max_width=480)
            frame = frames[0][1] if frames else None
        else:
            frame = cv2.imread(path)
            if frame is not None:
                entry["resolution"] = f"{frame.shape[1]}x{frame.shape[0]}"
        if frame is None:
            continue
        cell = fit(frame.copy(), 384, 216)
        suffix = f" {entry['duration']}s" if kind == "video" else " (still)"
        label(cell, f"{index}: {rel[:28]}{suffix}", scale=0.45)
        cells.append(cell)
        entries.append(entry)

    images = []
    per_sheet = 20  # 5 x 4
    for sheet, start in enumerate(range(0, len(cells), per_sheet), start=1):
        out = os.path.join(args.output_dir, f"broll_catalog_{sheet:02d}.jpg")
        images.append(save(out, grid(cells[start:start + per_sheet], 5)))

    print(json.dumps({"action": "broll_catalog", "dir": os.path.abspath(args.dir),
                      "count": len(entries), "images": images, "files": entries}, indent=2))


def resolve_item(item, seg, words_by_index, directory):
    """Return (resolved_dict, None) or (None, problem)."""
    path = os.path.join(directory, item.get("file", ""))
    if not os.path.isfile(path):
        return None, f"file not found: {item.get('file')}"
    kind = kind_of(path)
    if not kind:
        return None, f"unsupported file type: {item.get('file')}"

    clip_length = seg.get("render_duration") or (seg["end"] - seg["start"])
    keep = seg.get("keep")

    def to_clip_time(source_seconds):
        local = source_seconds - seg["start"]
        return retime(local, keep)[0] if keep else local

    if "from_word" in item:
        first = words_by_index.get(int(item["from_word"]))
        last = words_by_index.get(int(item.get("to_word", item["from_word"])))
        if not first or not last:
            return None, f"word index outside the transcript: {item}"
        start = to_clip_time(first["start"]) - LEAD
        end = to_clip_time(last["end"])
    elif "start" in item:
        start = float(item["start"])
        end = start + float(item.get("duration", 3.0))
    else:
        return None, f"needs from_word/to_word or start/duration: {item}"

    start = max(0.0, start)
    duration = min(max(end - start, MIN_DURATION), MAX_DURATION)
    source_start = max(0.0, float(item.get("source_start", 0)))
    if kind == "video":
        info = video_info(path)
        if not info:
            return None, f"cannot read video: {item.get('file')}"
        available = info["duration"] - source_start
        if available < MIN_DURATION:
            return None, f"{item.get('file')} is too short from source_start={source_start}"
        duration = min(duration, available)
    duration = min(duration, clip_length - start)
    if duration < MIN_DURATION:
        return None, f"{item.get('file')}: no room before the end of the clip"

    return {"file": item["file"], "path": os.path.abspath(path), "kind": kind,
            "start": round(start, 3), "duration": round(duration, 3),
            "source_start": round(source_start, 3)}, None


def cmd_apply(args):
    plan = read_json(args.plan)
    segments = read_json(args.segments)["segments"]
    words = read_json(args.transcript).get("words") or []
    words_by_index = {w["i"]: w for w in words}

    by_segment, report, problems = {}, [], []
    for seg in segments:
        seg_id = str(seg.get("id"))
        resolved = []
        for item in plan.get(seg_id, []):
            entry, problem = resolve_item(item, seg, words_by_index, args.dir)
            if problem:
                problems.append(f"segment {seg_id}: {problem}")
                continue
            resolved.append(entry)
        resolved.sort(key=lambda e: e["start"])
        # Cutaways must not overlap; trim or drop the later one.
        cleaned = []
        for entry in resolved:
            if cleaned:
                prev_end = cleaned[-1]["start"] + cleaned[-1]["duration"]
                if entry["start"] < prev_end + 0.3:
                    shift = prev_end + 0.3 - entry["start"]
                    entry["start"] = round(entry["start"] + shift, 3)
                    entry["duration"] = round(entry["duration"] - shift, 3)
                    if entry["duration"] < MIN_DURATION:
                        problems.append(f"segment {seg_id}: {entry['file']} overlaps the previous "
                                        "cutaway and was dropped")
                        continue
            cleaned.append(entry)
        if cleaned:
            by_segment[seg_id] = cleaned
            clip_length = seg.get("render_duration") or (seg["end"] - seg["start"])
            covered = sum(e["duration"] for e in cleaned)
            report.append({"id": seg.get("id"), "cutaways": len(cleaned),
                           "covered_sec": round(covered, 1),
                           "covered_pct": round(100 * covered / clip_length),
                           "items": [{k: e[k] for k in ("file", "start", "duration")} for e in cleaned]})

    unknown = sorted(set(plan) - {str(s.get("id")) for s in segments})
    if unknown:
        problems.append(f"plan references unknown segment ids: {unknown}")

    write_json(args.output, {"version": 1, "by_segment": by_segment}, indent=2)
    print(json.dumps({"action": "broll_apply", "output": args.output, "segments": report,
                      "problems": problems}, indent=2))
    if problems and not by_segment:
        sys.exit(2)


def main():
    parser = argparse.ArgumentParser(description="B-roll catalogue and timing")
    sub = parser.add_subparsers(dest="command", required=True)

    catalog = sub.add_parser("catalog")
    catalog.add_argument("--dir", required=True)
    catalog.add_argument("--output-dir", required=True)
    catalog.set_defaults(func=cmd_catalog)

    apply_cmd = sub.add_parser("apply")
    apply_cmd.add_argument("--plan", required=True)
    apply_cmd.add_argument("--dir", required=True)
    apply_cmd.add_argument("--segments", required=True)
    apply_cmd.add_argument("--transcript", required=True)
    apply_cmd.add_argument("--output", required=True)
    apply_cmd.set_defaults(func=cmd_apply)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
