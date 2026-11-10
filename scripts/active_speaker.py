"""Work out who is speaking from mouth movement, for two-person shots.

There is no audio-to-face link in a mixed recording, so the speaker is found
visually: the mouth region of the person talking changes from frame to frame
far more than the rest of their face. For each speaker we measure motion in a
small patch around the mouth and subtract motion in a same-sized patch around
the nose (head movement, lighting flicker, compression noise affect both).

`plan_switches` then turns the two activity curves into a calm edit: cut to a
speaker only once they have clearly held the floor for a moment, and never cut
again too soon.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from shorts_common import stream_frames  # noqa: E402

SAMPLE_FPS = 10.0
PATCH = (32, 24)  # width, height of the normalised patches


def _patch(gray, cx, cy, half_w, top, bottom):
    import cv2

    h, w = gray.shape[:2]
    x0, x1 = int(max(0, cx - half_w)), int(min(w, cx + half_w))
    y0, y1 = int(max(0, cy - top)), int(min(h, cy + bottom))
    if x1 - x0 < 4 or y1 - y0 < 4:
        return None
    patch = cv2.resize(gray[y0:y1, x0:x1], PATCH, interpolation=cv2.INTER_AREA)
    return cv2.GaussianBlur(patch, (3, 3), 0).astype("float32")


def mouth_activity(clip_path, speaker_xs, match_distance=0.15):
    """Sample mouth activity for each speaker.

    speaker_xs: normalised x-centre of each speaker (from cluster_speakers).
    Returns (times, scores) where scores[i] is a list aligned with `times`
    for speaker i (0.0 when the face was not found in that frame).
    """
    import cv2
    import numpy as np
    from facedet import FaceDetector

    detector = FaceDetector()
    times = []
    scores = [[] for _ in speaker_xs]
    previous = [None] * len(speaker_xs)  # (mouth_patch, nose_patch) per speaker

    for t, frame in stream_frames(clip_path, SAMPLE_FPS):
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        height, width = gray.shape[:2]
        faces = detector.detect(frame)
        times.append(t)

        for idx, speaker_x in enumerate(speaker_xs):
            candidates = [f for f in faces if abs(f["cx"] - speaker_x) < match_distance]
            if not candidates:
                previous[idx] = None
                scores[idx].append(0.0)
                continue
            face = min(candidates, key=lambda f: abs(f["cx"] - speaker_x))
            mx, my, mw = face["mouth"]
            nx, ny = face["nose"]
            half = max(6.0, mw * width * 0.8)
            # The mouth opens downwards, so the patch extends further below.
            mouth = _patch(gray, mx * width, my * height, half, half * 0.6, half * 1.0)
            nose = _patch(gray, nx * width, ny * height - half * 0.6, half, half * 0.8, half * 0.8)
            score = 0.0
            if mouth is not None and nose is not None and previous[idx] is not None:
                prev_mouth, prev_nose = previous[idx]
                mouth_motion = float(np.mean(np.abs(mouth - prev_mouth)))
                head_motion = float(np.mean(np.abs(nose - prev_nose)))
                score = max(0.0, mouth_motion - head_motion)
            previous[idx] = (mouth, nose) if mouth is not None and nose is not None else None
            scores[idx].append(score)
    return times, scores


def _smooth(values, window):
    if window <= 1 or not values:
        return list(values)
    half = window // 2
    out = []
    for i in range(len(values)):
        chunk = values[max(0, i - half):i + half + 1]
        out.append(sum(chunk) / len(chunk))
    return out


def plan_switches(times, scores, min_hold=1.5, confirm=0.4, lead=1.3):
    """Decide which speaker is on screen over time.

    Returns (switches, confidence):
      switches    [(t, speaker_index)] starting at t=0, one entry per cut
      confidence  fraction of the clip where one speaker was clearly ahead
    """
    if not times or len(scores) < 2:
        return [(0.0, 0)], 0.0

    step = times[1] - times[0] if len(times) > 1 else 1.0 / SAMPLE_FPS
    window = max(1, int(round(0.8 / step)) | 1)
    a, b = _smooth(scores[0], window), _smooth(scores[1], window)

    peak = sorted(max(x, y) for x, y in zip(a, b))
    floor = peak[len(peak) // 2] * 0.35 if peak else 0.0  # ignore near-silent stretches

    leaders = []
    for x, y in zip(a, b):
        if max(x, y) <= floor or max(x, y) <= 0:
            leaders.append(None)
        elif x >= y * lead:
            leaders.append(0)
        elif y >= x * lead:
            leaders.append(1)
        else:
            leaders.append(None)

    decisive = [leader for leader in leaders if leader is not None]
    confidence = len(decisive) / len(leaders)
    if not decisive:
        return [(0.0, 0 if sum(a) >= sum(b) else 1)], 0.0

    current = decisive[0]
    switches = [(0.0, current)]
    last_switch = 0.0
    challenger_since = None
    for t, leader in zip(times, leaders):
        if leader is None or leader == current:
            if leader == current:
                challenger_since = None
            continue
        if challenger_since is None:
            challenger_since = t
        if t - challenger_since >= confirm and t - last_switch >= min_hold:
            # Cut at the moment they started, but not inside the previous hold.
            cut_at = max(challenger_since, last_switch + min_hold)
            current, last_switch, challenger_since = leader, cut_at, None
            switches.append((round(cut_at, 2), current))
    return switches, round(confidence, 3)
