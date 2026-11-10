#!/usr/bin/env python3
"""Build contact sheets so Claude can *look* at the video, not just read JSON.

  source   Grid of frames across the whole source video (content type, layout,
           on-screen text, whether there is a webcam overlay, ...).
               python3 contact_sheet.py source INPUT --output WORK/qa/source.jpg

  reframe  Per clip: three frames with the computed crop drawn on top, to catch
           a crop that misses the speaker before spending time rendering.
               python3 contact_sheet.py reframe --clips-dir WORK/clips \
                   --reframe WORK/reframe.json --output-dir WORK/qa

  renders  Per rendered short: three frames (hook, middle, late) to check
           captions, hook text and framing in the final picture.
               python3 contact_sheet.py renders --dir WORK/render --output-dir WORK/qa

Each command prints the image paths as JSON; open them with the Read tool.
"""
import argparse
import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from shorts_common import fail, grab_frames, read_json, video_info  # noqa: E402


def fmt_time(seconds):
    minutes, secs = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def label(img, text, scale=0.6):
    import cv2

    (w, h), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, scale, 2)
    cv2.rectangle(img, (0, 0), (w + 12, h + 14), (0, 0, 0), -1)
    cv2.putText(img, text, (6, h + 6), cv2.FONT_HERSHEY_SIMPLEX, scale, (255, 255, 255), 2,
                cv2.LINE_AA)


def fit(img, width, height):
    """Resize keeping aspect ratio, padded onto a dark cell."""
    import cv2
    import numpy as np

    h, w = img.shape[:2]
    scale = min(width / w, height / h)
    resized = cv2.resize(img, (max(1, int(w * scale)), max(1, int(h * scale))),
                         interpolation=cv2.INTER_AREA)
    cell = np.full((height, width, 3), 24, np.uint8)
    y = (height - resized.shape[0]) // 2
    x = (width - resized.shape[1]) // 2
    cell[y:y + resized.shape[0], x:x + resized.shape[1]] = resized
    return cell


def grid(cells, columns):
    import numpy as np

    rows = []
    for i in range(0, len(cells), columns):
        row = cells[i:i + columns]
        while len(row) < columns:
            row.append(np.full_like(cells[0], 24))
        rows.append(np.hstack(row))
    return np.vstack(rows)


def save(path, img):
    import cv2

    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    cv2.imwrite(path, img, [cv2.IMWRITE_JPEG_QUALITY, 88])
    return path


def cmd_source(args):
    info = video_info(args.input)
    if not info or not info["duration"]:
        fail(f"Could not read video stream: {args.input}")
    times = [info["duration"] * (i + 0.5) / args.count for i in range(args.count)]
    frames = grab_frames(args.input, times, max_width=640)
    if not frames:
        fail("No frames could be extracted")

    cells = []
    for t, frame in frames:
        cell = fit(frame.copy(), 480, 270)
        label(cell, fmt_time(t))
        cells.append(cell)
    path = save(args.output, grid(cells, 4))
    print(json.dumps({"action": "contact_sheet", "kind": "source", "images": [path],
                      "resolution": f"{info['width']}x{info['height']}",
                      "duration": round(info["duration"], 1)}))


def crop_x_at(entry, t):
    keyframes = entry.get("crop_keyframes") or []
    if len(keyframes) < 2:
        return entry["crop"]["x"]
    if t <= keyframes[0]["t"]:
        return keyframes[0]["x"]
    for a, b in zip(keyframes, keyframes[1:]):
        if a["t"] <= t <= b["t"]:
            span = b["t"] - a["t"]
            return a["x"] + (b["x"] - a["x"]) * ((t - a["t"]) / span if span else 0)
    return keyframes[-1]["x"]


def cmd_reframe(args):
    import cv2

    reframe = read_json(args.reframe)
    images = []
    for name, entry in sorted(reframe.get("clips", {}).items()):
        clip_path = os.path.join(args.clips_dir, name)
        info = video_info(clip_path)
        if not info:
            continue
        times = [info["duration"] * f for f in (0.1, 0.5, 0.9)]
        cells = []
        for t, frame in grab_frames(clip_path, times, max_width=960):
            frame = frame.copy()
            scale = frame.shape[1] / info["width"]
            boxes = [p["crop"] for p in entry.get("panels", [])] or \
                [dict(entry["crop"], x=crop_x_at(entry, t))]
            colours = [(0, 255, 0), (0, 200, 255)]
            for i, box in enumerate(boxes):
                p1 = (int(box["x"] * scale), int(box["y"] * scale))
                p2 = (int((box["x"] + box["w"]) * scale), int((box["y"] + box["h"]) * scale))
                cv2.rectangle(frame, p1, p2, colours[i % 2], 4)
            cell = fit(frame, 640, 360)
            label(cell, f"{name} @ {t:.1f}s  {entry.get('strategy', '')}")
            cells.append(cell)
        if cells:
            out = os.path.join(args.output_dir, f"reframe_{os.path.splitext(name)[0]}.jpg")
            images.append(save(out, grid(cells, 3)))
    if not images:
        fail("No clips found for the reframe data")
    print(json.dumps({"action": "contact_sheet", "kind": "reframe", "images": images}))


def cmd_renders(args):
    images = []
    for path in sorted(glob.glob(os.path.join(args.dir, "short_*.mp4"))):
        info = video_info(path)
        if not info:
            continue
        duration = info["duration"]
        times = [min(1.5, duration * 0.2), duration * 0.5, duration * 0.85]
        cells = []
        for t, frame in grab_frames(path, times, max_width=540):
            cell = fit(frame.copy(), 405, 720)
            label(cell, f"{t:.1f}s")
            cells.append(cell)
        if cells:
            name = os.path.splitext(os.path.basename(path))[0]
            images.append(save(os.path.join(args.output_dir, f"render_{name}.jpg"), grid(cells, 3)))
    if not images:
        fail(f"No short_*.mp4 files found in {args.dir}")
    print(json.dumps({"action": "contact_sheet", "kind": "renders", "images": images}))


def main():
    parser = argparse.ArgumentParser(description="Contact sheets for visual review")
    sub = parser.add_subparsers(dest="command", required=True)

    source = sub.add_parser("source")
    source.add_argument("input")
    source.add_argument("--output", required=True)
    source.add_argument("--count", type=int, default=12)
    source.set_defaults(func=cmd_source)

    reframe = sub.add_parser("reframe")
    reframe.add_argument("--clips-dir", required=True)
    reframe.add_argument("--reframe", required=True)
    reframe.add_argument("--output-dir", required=True)
    reframe.set_defaults(func=cmd_reframe)

    renders = sub.add_parser("renders")
    renders.add_argument("--dir", required=True)
    renders.add_argument("--output-dir", required=True)
    renders.set_defaults(func=cmd_renders)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
