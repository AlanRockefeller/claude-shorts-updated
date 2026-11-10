#!/usr/bin/env python3
"""Generate a synthetic source video plus a matching word-level transcript.

Lets the whole pipeline after transcription run without a Whisper model or a
GPU: every "word" is a 0.3 s tone burst, so the audio really has the silences
the transcript claims.

Usage:
    python3 tests/make_fixture.py OUT_DIR [--duration 60] [--overlay face.jpg] [--moving]
Writes OUT_DIR/source.mp4 and OUT_DIR/transcript.json (+ transcript.txt).
"""
import argparse
import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))
import transcribe  # noqa: E402
from shorts_common import write_json  # noqa: E402

WORD_PERIOD = 0.4
WORD_LENGTH = 0.3
FIRST_WORD = 1.0
PAUSE = (20.0, 23.0)  # a real silence in the middle of the video
SENTENCE = ["This", "is", "sentence", "number", "{n}", "of", "the", "test."]


def fixture_words(duration):
    words, slot, n = [], 0, 1
    t = FIRST_WORD
    while t + WORD_LENGTH < duration - 0.5:
        if not (PAUSE[0] - WORD_LENGTH <= t < PAUSE[1]):
            token = SENTENCE[slot % len(SENTENCE)].format(n=n)
            if slot % len(SENTENCE) == len(SENTENCE) - 1:
                n += 1
            # Sprinkle in a hesitation and a low-confidence word for captions.py.
            if slot == 11:
                token = "um"
            words.append({
                "text": " " + token,
                "start": round(t, 3),
                "end": round(t + WORD_LENGTH, 3),
                "prob": 0.31 if slot == 18 else 0.97,
            })
            slot += 1
        t = round(t + WORD_PERIOD, 3)
    return words


def make_video(path, duration, overlay=None, moving=False):
    tone = (f"sin(2*PI*330*t)*0.5*gte(t,{FIRST_WORD})"
            f"*lt(mod(t-{FIRST_WORD},{WORD_PERIOD}),{WORD_LENGTH})"
            f"*(1-between(t,{PAUSE[0] - WORD_LENGTH},{PAUSE[1]}))")
    cmd = ["ffmpeg", "-y", "-v", "error"]
    if overlay:
        x = "'100+t*20'" if moving else "500"
        cmd += ["-f", "lavfi", "-i", f"color=c=0x404850:s=1920x1080:r=30:d={duration}",
                "-i", overlay,
                "-f", "lavfi", "-i", f"aevalsrc='{tone}':s=48000:d={duration}",
                "-filter_complex", f"[1:v]scale=-2:900[p];[0:v][p]overlay=x={x}:y=180[v]",
                "-map", "[v]", "-map", "2:a"]
    else:
        cmd += ["-f", "lavfi", "-i", f"testsrc2=size=1920x1080:rate=30:duration={duration}",
                "-f", "lavfi", "-i", f"aevalsrc='{tone}':s=48000:d={duration}"]
    cmd += ["-c:v", "libx264", "-preset", "ultrafast", "-g", "150", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "128k", "-t", str(duration), path]
    subprocess.run(cmd, check=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("out_dir")
    parser.add_argument("--duration", type=float, default=60.0)
    parser.add_argument("--overlay", help="Image to composite (e.g. a face photo)")
    parser.add_argument("--moving", action="store_true", help="Slide the overlay sideways")
    args = parser.parse_args()

    os.makedirs(args.out_dir, exist_ok=True)
    video = os.path.join(args.out_dir, "source.mp4")
    make_video(video, args.duration, args.overlay, args.moving)

    output = transcribe.build_output(
        fixture_words(args.duration), transcribe.fingerprint(video), args.duration,
        "en", 0.99, "fixture", "cpu", "int8", 0.0)
    out_json = os.path.join(args.out_dir, "transcript.json")
    write_json(out_json, output)
    with open(os.path.splitext(out_json)[0] + ".txt", "w", encoding="utf-8") as f:
        f.write(transcribe.render_reading_view(output, output["sentences"]))
    print(video)


if __name__ == "__main__":
    main()
