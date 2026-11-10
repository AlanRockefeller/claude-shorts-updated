#!/usr/bin/env python3
"""Compute reframe coordinates for vertical (9:16) video from landscape clips.

Strategies by content type:

- talking-head: 9:16 crop that follows the speaker's face. Static when the
  speaker stays put, a smoothed pan when they move around.
- podcast: finds each speaker. If both fit one 9:16 crop, frames them together.
  Otherwise cuts to whoever is talking (detected from mouth movement), or, if
  that cannot be detected reliably, stacks both in a split-screen. Choose
  explicitly with `--podcast-layout switch|split|single`.
- screen: framed layout (zoomed content with background fill) that pans to
  follow the mouse cursor.

Usage:
    python3 compute_reframe.py --clips-dir WORK/clips/ \
        --content-type talking-head --output WORK/reframe.json

Output (reframe.json):
{
  "clips": {
    "clip_01.mp4": {
      "strategy": "face-track",
      "layout": "fill",                       # fill | framed | split
      "source_resolution": "1920x1080",
      "fps": 30.0,
      "crop": {"x": 656, "y": 0, "w": 608, "h": 1080},
      "crop_keyframes": [{"t": 0.0, "x": 600}, ...],   # optional pan
      "panels": [{"crop": {...}}, {"crop": {...}}]      # split layout only
    }
  }
}
"""
import argparse
import glob
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from shorts_common import fail, stream_frames, video_info, write_json  # noqa: E402

FACE_SAMPLE_FPS = 2.0
PANEL_ASPECT = 1080 / 960  # each half of a stacked split-screen


def even(value):
    return int(value) // 2 * 2


def clamp(value, low, high):
    return max(low, min(value, high))


# --- Faces -------------------------------------------------------------------

def detect_face_frames(clip_path):
    """Return [(t, [faces])] sampled across the clip."""
    from facedet import FaceDetector

    detector = FaceDetector()
    return [(t, detector.detect(frame)) for t, frame in stream_frames(clip_path, FACE_SAMPLE_FPS)]


def cluster_speakers(face_frames, merge_distance=0.12):
    """Group detections into speakers by horizontal position.

    Returns clusters sorted by importance, each:
      {cx, cy, w, h, presence, samples: [(t, cx)]}
    """
    import numpy as np

    clusters = []
    for t, faces in face_frames:
        used = set()
        for face in faces:
            best, best_dist = None, merge_distance
            for idx, cluster in enumerate(clusters):
                if idx in used:
                    continue
                dist = abs(face["cx"] - cluster["cx"])
                if dist < best_dist:
                    best, best_dist = idx, dist
            if best is None:
                clusters.append({"cx": face["cx"], "items": [(t, face)]})
                used.add(len(clusters) - 1)
            else:
                cluster = clusters[best]
                cluster["items"].append((t, face))
                recent = [f["cx"] for _, f in cluster["items"][-8:]]
                cluster["cx"] = sum(recent) / len(recent)
                used.add(best)

    total = max(len(face_frames), 1)
    speakers = []
    for cluster in clusters:
        faces = [f for _, f in cluster["items"]]
        presence = len({t for t, _ in cluster["items"]}) / total
        speakers.append({
            "cx": float(np.median([f["cx"] for f in faces])),
            "cy": float(np.median([f["cy"] for f in faces])),
            "w": float(np.median([f["w"] for f in faces])),
            "h": float(np.median([f["h"] for f in faces])),
            "presence": round(presence, 3),
            "samples": [(t, f["cx"]) for t, f in cluster["items"]],
        })
    # Ignore blips (posters, false positives, people walking past).
    speakers = [s for s in speakers if s["presence"] >= 0.2]
    speakers.sort(key=lambda s: s["presence"] * (s["w"] * s["h"]) ** 0.5, reverse=True)
    return speakers


def smooth_series(values, window=5):
    """Median filter then moving average; keeps the pan calm."""
    import numpy as np

    values = np.asarray(values, dtype=float)
    if len(values) < window:
        return values
    half = window // 2
    padded = np.pad(values, half, mode="edge")
    medians = np.array([np.median(padded[i:i + window]) for i in range(len(values))])
    padded = np.pad(medians, half, mode="edge")
    return np.convolve(padded, np.ones(window) / window, mode="valid")


def full_height_crop(src_w, src_h):
    """Largest 9:16 crop that fits the source. Returns (w, h)."""
    crop_h = src_h
    crop_w = even(crop_h * 9 / 16)
    if crop_w > src_w:  # source is already narrower than 9:16
        crop_w = even(src_w)
        crop_h = min(src_h, even(crop_w * 16 / 9))
    return crop_w, crop_h


def follow_speaker(speaker, src_w, src_h, duration):
    """9:16 crop on one speaker, with pan keyframes if they move a lot."""
    import numpy as np

    crop_w, crop_h = full_height_crop(src_w, src_h)
    crop_y = (src_h - crop_h) // 2
    max_x = src_w - crop_w

    def to_x(cx):
        return int(clamp(cx * src_w - crop_w / 2, 0, max_x))

    static = {"x": to_x(speaker["cx"]), "y": crop_y, "w": crop_w, "h": crop_h}
    samples = sorted(speaker["samples"])
    if len(samples) < 6 or max_x == 0:
        return static, []

    times = [t for t, _ in samples]
    xs = smooth_series([cx * src_w for _, cx in samples])
    travel = float(np.percentile(xs, 95) - np.percentile(xs, 5))
    if travel < crop_w * 0.25:
        # Speaker stays comfortably inside a fixed frame: don't move the camera.
        return static, []

    keyframes = []
    deadband = crop_w * 0.04
    for t, x in zip(times, xs):
        crop_x = int(clamp(x - crop_w / 2, 0, max_x))
        if not keyframes or abs(crop_x - keyframes[-1]["x"]) > deadband:
            keyframes.append({"t": round(t, 2), "x": crop_x})
    if keyframes and keyframes[0]["t"] > 0:
        keyframes.insert(0, {"t": 0.0, "x": keyframes[0]["x"]})
    if keyframes and keyframes[-1]["t"] < duration:
        keyframes.append({"t": round(duration, 2), "x": keyframes[-1]["x"]})
    if len(keyframes) < 3:
        return static, []
    return static, keyframes


def split_panels(left, right, src_w, src_h):
    """One crop per speaker for a stacked split-screen (top = left speaker)."""
    separation = (right["cx"] - left["cx"]) * src_w
    # Frame each speaker so the face is roughly a fifth of the panel height,
    # using one scale for both panels so they match.
    face_h = max(left["h"], right["h"]) * src_h
    crop_h = clamp(face_h * 4.5, src_h * 0.5, src_h * 0.9)
    crop_w = crop_h * PANEL_ASPECT
    # Don't let one panel show the other speaker.
    crop_w = min(crop_w, max(separation, src_w * 0.2), src_w)
    crop_h = crop_w / PANEL_ASPECT
    crop_w, crop_h = even(crop_w), even(crop_h)

    panels = []
    for speaker in (left, right):
        x = int(clamp(speaker["cx"] * src_w - crop_w / 2, 0, src_w - crop_w))
        # Put the face a little above the middle of its panel.
        y = int(clamp(speaker["cy"] * src_h - crop_h * 0.42, 0, src_h - crop_h))
        panels.append({"crop": {"x": x, "y": y, "w": crop_w, "h": crop_h}})
    return panels


def switch_speakers(clip_path, left, right, src_w, src_h):
    """Single 9:16 crop that cuts to whoever is talking. Returns entry or None
    when mouth movement does not tell the two speakers apart clearly enough."""
    import active_speaker

    times, scores = active_speaker.mouth_activity(clip_path, [left["cx"], right["cx"]])
    switches, confidence = active_speaker.plan_switches(times, scores)
    if confidence < 0.45:
        return None, confidence

    crop_w, crop_h = full_height_crop(src_w, src_h)
    crop_y = (src_h - crop_h) // 2
    xs = [int(clamp(s["cx"] * src_w - crop_w / 2, 0, src_w - crop_w)) for s in (left, right)]
    keyframes = [{"t": t, "x": xs[speaker]} for t, speaker in switches]
    entry = {
        "strategy": "speaker-switch",
        "layout": "fill",
        "crop": {"x": keyframes[0]["x"], "y": crop_y, "w": crop_w, "h": crop_h},
        "speaker_confidence": confidence,
        "speaker_cuts": len(keyframes) - 1,
    }
    if len(keyframes) > 1:
        entry["crop_keyframes"] = keyframes
        entry["crop_mode"] = "cut"   # jump between positions instead of panning
    return entry, confidence


def reframe_faces(clip_path, src_w, src_h, duration, content_type, podcast_layout="auto"):
    """Reframe talking-head / podcast clips. Returns a dict of entry fields."""
    face_frames = detect_face_frames(clip_path)
    speakers = cluster_speakers(face_frames)
    crop_w, crop_h = full_height_crop(src_w, src_h)
    info = {"faces_detected": sum(1 for _, f in face_frames if f), "speakers": len(speakers)}

    if not speakers:
        crop = {"x": (src_w - crop_w) // 2, "y": (src_h - crop_h) // 2, "w": crop_w, "h": crop_h}
        return {"strategy": "center", "layout": "fill", "crop": crop, **info}

    main = speakers[0]
    if content_type == "podcast" and len(speakers) >= 2:
        left, right = sorted(speakers[:2], key=lambda s: s["cx"])
        if left["presence"] >= 0.4 and right["presence"] >= 0.4:
            span = (right["cx"] - left["cx"]) * src_w + (left["w"] + right["w"]) / 2 * src_w
            if span <= crop_w * 0.85:
                middle = (left["cx"] + right["cx"]) / 2
                crop = {"x": int(clamp(middle * src_w - crop_w / 2, 0, src_w - crop_w)),
                        "y": (src_h - crop_h) // 2, "w": crop_w, "h": crop_h}
                return {"strategy": "two-shot", "layout": "fill", "crop": crop, **info}
            if podcast_layout in ("auto", "switch"):
                entry, confidence = switch_speakers(clip_path, left, right, src_w, src_h)
                if entry is not None:
                    return {**entry, **info}
                info["speaker_confidence"] = confidence
                info["note"] = ("could not tell who is speaking from mouth movement; "
                                "using split-screen instead")
            if podcast_layout != "single":
                panels = split_panels(left, right, src_w, src_h)
                return {"strategy": "split-speakers", "layout": "split",
                        "crop": panels[0]["crop"], "panels": panels, **info}

    crop, keyframes = follow_speaker(main, src_w, src_h, duration)
    entry = {"strategy": "face-track", "layout": "fill", "crop": crop, **info}
    if keyframes:
        entry["crop_keyframes"] = keyframes
    return entry


# --- Screen recordings -------------------------------------------------------

def detect_cursor_positions(clip_path, sample_interval=0.5):
    """Estimate cursor x-position over time via frame differencing.

    Looks for small changed regions between consecutive samples. Returns a list
    of {t, x_norm}. Frames are analysed at 960 px width, so thresholds are
    resolution independent.
    """
    import cv2

    positions = []
    last_x = 0.5
    prev = None
    for t, frame in stream_frames(clip_path, 1.0 / sample_interval, max_width=960, gray=True):
        if prev is not None:
            diff = cv2.absdiff(prev, frame)
            _, thresh = cv2.threshold(diff, 25, 255, cv2.THRESH_BINARY)
            contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            width = frame.shape[1]
            candidates = []
            for contour in contours:
                area = cv2.contourArea(contour)
                x, _, w, h = cv2.boundingRect(contour)
                if 12 < area < 1250 and 0.2 < (w / max(h, 1)) < 5.0:
                    candidates.append((x + w / 2) / width)
            if candidates:
                last_x = min(candidates, key=lambda cx: abs(cx - last_x))
            positions.append({"t": round(t, 2), "x_norm": round(last_x, 4)})
        prev = frame.copy()

    if positions:
        positions.insert(0, {"t": 0.0, "x_norm": positions[0]["x_norm"]})
    return positions


def reframe_screen(clip_path, src_w, src_h, zoom, cursor_track):
    crop_h = even(src_h)
    crop_w = min(even(src_w * zoom), even(src_w))
    max_x = src_w - crop_w
    center = {"x": max_x // 2, "y": 0, "w": crop_w, "h": crop_h}

    if not cursor_track or max_x == 0:
        return {"strategy": "framed", "layout": "framed", "crop": center}

    positions = detect_cursor_positions(clip_path)
    if len(positions) < 2:
        return {"strategy": "framed", "layout": "framed", "crop": center}

    xs = smooth_series([p["x_norm"] * src_w for p in positions])
    keyframes = []
    for pos, x in zip(positions, xs):
        crop_x = int(clamp(x - crop_w / 2, 0, max_x))
        if not keyframes or abs(crop_x - keyframes[-1]["x"]) > crop_w * 0.02:
            keyframes.append({"t": pos["t"], "x": crop_x})
    last = {"t": positions[-1]["t"], "x": int(clamp(xs[-1] - crop_w / 2, 0, max_x))}
    if keyframes[-1]["t"] != last["t"]:
        keyframes.append(last)

    avg_x = int(sum(k["x"] for k in keyframes) / len(keyframes))
    entry = {"strategy": "cursor-track", "layout": "framed",
             "crop": {"x": clamp(avg_x, 0, max_x), "y": 0, "w": crop_w, "h": crop_h}}
    if len(keyframes) >= 2:
        entry["crop_keyframes"] = keyframes
    return entry


# --- Main --------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Compute reframe coordinates")
    parser.add_argument("--clips-dir", required=True, help="Directory with clip_*.mp4 files")
    parser.add_argument("--content-type", required=True,
                        choices=["talking-head", "screen", "podcast"])
    parser.add_argument("--output", required=True, help="Output JSON file")
    parser.add_argument("--zoom", type=float, default=0.55,
                        help="Screen recordings: fraction of source width to show (default 0.55)")
    parser.add_argument("--no-cursor-track", action="store_true",
                        help="Screen recordings: static centre crop")
    parser.add_argument("--podcast-layout", default="auto",
                        choices=["auto", "switch", "split", "single"],
                        help="Podcast with speakers too far apart for one crop: 'switch' cuts "
                             "to whoever is talking, 'split' stacks both, 'single' stays on the "
                             "main speaker. 'auto' (default) switches when the active speaker "
                             "can be detected reliably, otherwise splits")
    parser.add_argument("--no-split", action="store_true",
                        help="Same as --podcast-layout single")
    parser.add_argument("--only", default=None,
                        help="Comma-separated segment ids (e.g. 2,5). Only these clips are "
                             "recomputed and merged into the existing output file, so clips "
                             "can use different content types")
    args = parser.parse_args()

    if not os.path.isdir(args.clips_dir):
        fail(f"Clips directory not found: {args.clips_dir}")
    clips = sorted(glob.glob(os.path.join(args.clips_dir, "clip_*.mp4")))
    if not clips:
        fail(f"No clip_*.mp4 files found in {args.clips_dir}")

    previous = {}
    if args.only:
        wanted = {f"clip_{int(i):02d}.mp4" for i in args.only.split(",") if i.strip()}
        clips = [c for c in clips if os.path.basename(c) in wanted]
        if not clips:
            fail(f"--only {args.only} matches no clips in {args.clips_dir}")
        if os.path.isfile(args.output):
            with open(args.output, encoding="utf-8") as f:
                previous = json.load(f).get("clips", {})

    start = time.time()
    results = {}
    for clip_path in clips:
        info = video_info(clip_path)
        if not info:
            fail(f"Could not read video stream: {clip_path}")
        src_w, src_h = info["width"], info["height"]

        if src_w / src_h <= 9 / 16 + 0.02:
            # Already vertical: use the whole frame.
            crop_w, crop_h = full_height_crop(src_w, src_h)
            entry = {"strategy": "vertical-source", "layout": "fill",
                     "crop": {"x": (src_w - crop_w) // 2, "y": (src_h - crop_h) // 2,
                              "w": crop_w, "h": crop_h}}
        elif args.content_type == "screen":
            entry = reframe_screen(clip_path, src_w, src_h, args.zoom, not args.no_cursor_track)
        else:
            entry = reframe_faces(clip_path, src_w, src_h, info["duration"],
                                  args.content_type,
                                  "single" if args.no_split else args.podcast_layout)

        entry.update({
            "content_type": args.content_type,
            "source_resolution": f"{src_w}x{src_h}",
            "fps": round(info["fps"], 3),
            "duration": round(info["duration"], 2),
            "output_resolution": "1080x1920",
        })
        results[os.path.basename(clip_path)] = entry

    elapsed = round(time.time() - start, 2)
    merged = {**previous, **results}
    write_json(args.output, {
        "content_type": args.content_type,
        "clip_count": len(merged),
        "computation_time_sec": elapsed,
        "clips": merged,
    }, indent=2)

    summary = {"action": "compute_reframe", "content_type": args.content_type,
               "clips_processed": len(results), "computation_time_sec": elapsed, "clips": {}}
    for name, data in results.items():
        item = {k: data[k] for k in ("strategy", "layout", "crop") if k in data}
        for key in ("speakers", "faces_detected", "speaker_confidence", "speaker_cuts",
                    "crop_mode", "note"):
            if key in data:
                item[key] = data[key]
        if "crop_keyframes" in data:
            item["keyframe_count"] = len(data["crop_keyframes"])
        if "panels" in data:
            item["panels"] = [p["crop"] for p in data["panels"]]
        summary["clips"][name] = item
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
