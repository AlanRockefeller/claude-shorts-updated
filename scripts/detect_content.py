#!/usr/bin/env python3
"""Auto-detect video content type: talking-head, screen recording, or podcast.

Samples N frames evenly across the video, runs face detection, and classifies
from face count, size and position. This is a heuristic first guess: the skill
also has Claude look at a contact sheet (contact_sheet.py) before committing.

Usage:
    python3 detect_content.py INPUT_VIDEO --output content_type.json

Classification rules:
- talking-head: 1 face, either large (>=5% of frame area) or consistently
  present and roughly centred
- podcast: 2+ faces consistently
- screen: few/no faces, or a small off-centre face (webcam overlay)
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from shorts_common import fail, grab_frames, video_info, write_json  # noqa: E402


def face_stats(frames):
    """Aggregate face detections over sampled frames."""
    import numpy as np
    from facedet import FaceDetector

    detector = FaceDetector()
    counts, sizes, centers = [], [], []
    for _, frame in frames:
        faces = detector.detect(frame)
        counts.append(len(faces))
        for face in faces:
            sizes.append(face["w"] * face["h"] * 100)
            centers.append(face["cx"])

    center_bias = 0.0
    if centers:
        center_bias = 1.0 - float(np.mean([abs(x - 0.5) for x in centers])) * 2
    return {
        "avg_count": round(float(np.mean(counts)) if counts else 0.0, 2),
        "avg_size_pct": round(float(np.mean(sizes)) if sizes else 0.0, 2),
        "max_size_pct": round(float(np.max(sizes)) if sizes else 0.0, 2),
        "center_bias": round(center_bias, 3),
        "frames_with_faces": sum(1 for c in counts if c > 0),
        "total_frames": len(counts),
    }


def classify(stats):
    """Return (content_type, confidence)."""
    avg_count = stats["avg_count"]
    avg_size = stats["avg_size_pct"]
    center_bias = stats["center_bias"]
    face_ratio = stats["frames_with_faces"] / max(stats["total_frames"], 1)

    if avg_count < 0.5 or (avg_size < 5 and face_ratio < 0.5):
        confidence = min(1.0, (1.0 - min(avg_count, 1.0)) * 0.5 + (1.0 - face_ratio) * 0.5)
        return "screen", round(confidence, 3)

    if avg_count >= 1.8 and face_ratio > 0.7:
        confidence = min(1.0, (avg_count - 1.0) * 0.3 + face_ratio * 0.3 + 0.4)
        return "podcast", round(confidence, 3)

    # Small face far from centre: webcam overlay on a screen recording.
    if avg_size < 8 and center_bias < 0.4:
        confidence = min(1.0, (1.0 - center_bias) * 0.3 + (8 - avg_size) / 8 * 0.3 + 0.3)
        return "screen", round(confidence, 3)

    # Close-up, or a smaller face that is consistently present and centred
    # (seated medium shot). Tight face boxes are ~1-3% of a 16:9 frame there.
    large_face = avg_size >= 5
    steady_centred_face = avg_size >= 0.8 and center_bias >= 0.45 and face_ratio >= 0.6
    if avg_count >= 0.5 and (large_face or steady_centred_face):
        confidence = min(1.0, center_bias * 0.4 + min(avg_size / 20, 1.0) * 0.3 + face_ratio * 0.3)
        return "talking-head", round(confidence, 3)

    return "screen", 0.5


def main():
    parser = argparse.ArgumentParser(description="Detect video content type")
    parser.add_argument("input", help="Input video file")
    parser.add_argument("--output", required=True, help="Output JSON file")
    parser.add_argument("--frames", type=int, default=16,
                        help="Number of frames to sample (default: 16)")
    args = parser.parse_args()

    if not os.path.isfile(args.input):
        fail(f"Input file not found: {args.input}")
    info = video_info(args.input)
    if not info or not info["duration"]:
        fail(f"Could not read video stream: {args.input}")

    start = time.time()
    timestamps = [info["duration"] * (i + 0.5) / args.frames for i in range(args.frames)]
    frames = grab_frames(args.input, timestamps)
    if not frames:
        fail("No frames could be extracted")

    stats = face_stats(frames)
    content_type, confidence = classify(stats)

    output = {
        "content_type": content_type,
        "confidence": confidence,
        "face_stats": stats,
        "frames_sampled": len(frames),
        "source_resolution": f"{info['width']}x{info['height']}",
        "detection_time_sec": round(time.time() - start, 2),
    }
    write_json(args.output, output, indent=2)
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
