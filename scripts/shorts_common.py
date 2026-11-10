"""Shared helpers for the shorts pipeline scripts (ffprobe, frame grabs, JSON I/O)."""
import json
import os
import subprocess
import sys


def fail(message, **extra):
    """Print a JSON error on stdout and exit non-zero."""
    print(json.dumps({"error": message, **extra}))
    sys.exit(1)


def write_json(path, data, indent=None):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=indent, ensure_ascii=False)
    os.replace(tmp, path)


def read_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def probe(path):
    """Return ffprobe JSON (format + streams) for a media file, or None."""
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json",
         "-show_format", "-show_streams", path],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        return None
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return None


def _rate(value):
    try:
        num, den = value.split("/")
        den = float(den)
        return float(num) / den if den else 0.0
    except (ValueError, AttributeError):
        return 0.0


def video_info(path):
    """Return {width, height, fps, duration, rotation} for the first video stream.

    Width/height are the *displayed* dimensions (rotation metadata applied),
    which is what FFmpeg and Remotion decode to.
    """
    data = probe(path)
    if not data:
        return None
    stream = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), None)
    if not stream:
        return None

    width, height = int(stream["width"]), int(stream["height"])
    rotation = 0
    for side in stream.get("side_data_list", []):
        if "rotation" in side:
            rotation = int(float(side["rotation"]))
    if abs(rotation) % 180 == 90:
        width, height = height, width

    fps = _rate(stream.get("avg_frame_rate", "")) or _rate(stream.get("r_frame_rate", "")) or 30.0
    duration = float(data.get("format", {}).get("duration") or stream.get("duration") or 0)
    return {
        "width": width,
        "height": height,
        "fps": fps,
        "duration": duration,
        "rotation": rotation,
        "codec": stream.get("codec_name", ""),
    }


def media_duration(path):
    data = probe(path)
    try:
        return float(data["format"]["duration"])
    except (TypeError, KeyError, ValueError):
        return 0.0


def grab_frames(path, timestamps, max_width=960):
    """Decode one frame per timestamp, downscaled to max_width. Returns [(t, bgr)].

    Uses one fast input-seek per frame, so cost is independent of video length.
    """
    import numpy as np

    info = video_info(path)
    if not info:
        return []
    scale = min(1.0, max_width / info["width"])
    out_w = max(2, int(info["width"] * scale) // 2 * 2)
    out_h = max(2, int(info["height"] * scale) // 2 * 2)
    frame_bytes = out_w * out_h * 3

    frames = []
    for t in timestamps:
        result = subprocess.run(
            ["ffmpeg", "-v", "error", "-ss", f"{max(0.0, t):.3f}", "-i", path,
             "-frames:v", "1", "-vf", f"scale={out_w}:{out_h}",
             "-f", "rawvideo", "-pix_fmt", "bgr24", "-"],
            capture_output=True,
        )
        if len(result.stdout) >= frame_bytes:
            frame = np.frombuffer(result.stdout[:frame_bytes], np.uint8).reshape(out_h, out_w, 3)
            frames.append((t, frame))
    return frames


def stream_frames(path, fps, max_width=960, gray=False):
    """Yield (t, frame) for a whole (short) clip at a fixed sample rate.

    Frames are streamed from one FFmpeg process, so memory use stays flat.
    """
    import numpy as np

    info = video_info(path)
    if not info:
        return
    scale = min(1.0, max_width / info["width"])
    out_w = max(2, int(info["width"] * scale) // 2 * 2)
    out_h = max(2, int(info["height"] * scale) // 2 * 2)
    channels = 1 if gray else 3
    frame_bytes = out_w * out_h * channels

    proc = subprocess.Popen(
        ["ffmpeg", "-v", "error", "-i", path, "-an", "-sn",
         "-vf", f"fps={fps},scale={out_w}:{out_h}",
         "-f", "rawvideo", "-pix_fmt", "gray" if gray else "bgr24", "-"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
    )
    index = 0
    try:
        while True:
            chunk = proc.stdout.read(frame_bytes)
            if len(chunk) < frame_bytes:
                break
            shape = (out_h, out_w) if gray else (out_h, out_w, 3)
            yield index / fps, np.frombuffer(chunk, np.uint8).reshape(shape)
            index += 1
    finally:
        proc.stdout.close()
        proc.wait()


def retime(t, keep):
    """Map a clip-local time to the timeline after jump cuts.

    `keep` is the list of [start, end] ranges that survive. Returns
    (new_time, inside) where `inside` is False if `t` fell in a removed range
    (new_time is then the position of the cut).
    """
    offset = 0.0
    for start, end in keep:
        if t < start:
            return offset, False
        if t <= end:
            return offset + (t - start), True
        offset += end - start
    return offset, False


def retime_span(start, end, keep, min_overlap=0.04):
    """Map an interval onto the timeline after jump cuts.

    Returns (new_start, new_end) covering the part of [start, end] that
    survives, or None if less than `min_overlap` seconds of it is kept. Judging
    by overlap (not by the midpoint) matters because Whisper often stretches a
    word's end across the pause that follows it: the word is still spoken even
    though most of its nominal interval was cut.
    """
    offset = 0.0
    new_start = new_end = None
    overlap = 0.0
    for k_start, k_end in keep:
        lo, hi = max(start, k_start), min(end, k_end)
        if hi > lo:
            if new_start is None:
                new_start = offset + (lo - k_start)
            new_end = offset + (hi - k_start)
            overlap += hi - lo
        offset += k_end - k_start
    if new_start is None or overlap < min_overlap:
        return None
    return new_start, new_end
