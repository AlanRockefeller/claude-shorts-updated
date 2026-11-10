#!/usr/bin/env python3
"""Transcribe a video with faster-whisper (word-level timestamps).

Writes two files:

  transcript.json  Machine-readable. Flat `words[]` (with a stable index `i`)
                   and `sentences[]`. Used by snap_boundaries.py / captions.py.
  transcript.txt   Compact reading view, one sentence per line:
                       [262.4-268.9] Nobody talks about this.
                   This is the file Claude reads to pick segments. A one-hour
                   video is roughly 25k tokens, versus several MB of JSON.

Results are cached: re-running on the same file + model reuses the transcript
unless --force is given.

Usage:
    python3 transcribe.py INPUT --output WORK/transcript.json
    python3 transcribe.py INPUT --output WORK/transcript.json --model large-v3
    python3 transcribe.py INPUT --output WORK/transcript.json --language en \
        --initial-prompt "Names: Remotion, Omarchy, Hyprland"
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cuda_libs  # noqa: E402
from shorts_common import fail, media_duration, probe, read_json, write_json  # noqa: E402

SENTENCE_END = re.compile(r"[.?!…。？！][\"'”’)\]]*$")
MAX_SENTENCE_WORDS = 45
MAX_SENTENCE_GAP = 1.2  # seconds of silence that force a line break


def fingerprint(path):
    st = os.stat(path)
    return {"path": os.path.abspath(path), "size": st.st_size, "mtime": int(st.st_mtime)}


# --- GPU ---------------------------------------------------------------------

def pick_device(requested):
    """Resolve 'auto' to cuda/cpu. Returns (device, note)."""
    if requested == "cpu":
        return "cpu", "cpu requested"
    try:
        import ctranslate2
        gpu_count = ctranslate2.get_cuda_device_count()
    except Exception as e:  # noqa: BLE001 - driver problems surface in many forms
        gpu_count, err = 0, str(e)
    else:
        err = "no CUDA device visible"

    if gpu_count < 1:
        if requested == "cuda":
            fail(f"--device cuda requested but unavailable: {err}")
        return "cpu", err

    ok, missing, pip = cuda_libs.preload()
    if not ok:
        note = (f"GPU found but CUDA libraries are missing ({', '.join(missing)}). "
                f"Install into the venv with: pip install {' '.join(repr(p) for p in pip)} "
                "(or system-wide, e.g. on Arch: sudo pacman -S cuda). Falling back to CPU.")
        if requested == "cuda":
            fail(note)
        return "cpu", note
    return "cuda", "CUDA"


# --- Transcription -----------------------------------------------------------

def extract_audio(video_path, audio_path):
    """Extract the first audio stream as 16 kHz mono WAV."""
    result = subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-i", video_path, "-map", "0:a:0",
         "-vn", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1", audio_path],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        fail(f"Audio extraction failed: {result.stderr[-400:]}")


def run_whisper(audio_path, model_size, device, compute_type, language=None,
                initial_prompt=None, batch_size=0):
    """Run faster-whisper. Returns (words, info) where words is a list of
    {text, start, end, prob}; `text` keeps Whisper's leading space."""
    from faster_whisper import BatchedInferencePipeline, WhisperModel

    model = WhisperModel(model_size, device=device, compute_type=compute_type)

    options = dict(
        language=language,
        beam_size=5,
        word_timestamps=True,
        initial_prompt=initial_prompt,
        vad_filter=True,
        vad_parameters=dict(min_silence_duration_ms=500, speech_pad_ms=200),
    )
    if batch_size and batch_size > 1:
        segments_iter, info = BatchedInferencePipeline(model=model).transcribe(
            audio_path, batch_size=batch_size, **options)
    else:
        # Not conditioning on previous text prevents a hallucinated or repeated
        # line from snowballing through the rest of a long recording.
        segments_iter, info = model.transcribe(
            audio_path, condition_on_previous_text=False, **options)

    words = []
    for segment in segments_iter:
        for word in segment.words or []:
            if not word.word.strip():
                continue
            words.append({
                "text": word.word,
                "start": round(float(word.start), 3),
                "end": round(float(word.end), 3),
                "prob": round(float(word.probability), 2),
            })
    return words, info


# --- Output shaping ----------------------------------------------------------

def clean_word_times(words, duration=None):
    """Sort, index and sanitise word timings (monotonic, non-zero length)."""
    words = sorted(words, key=lambda w: (w["start"], w["end"]))
    for i, w in enumerate(words):
        if w["end"] <= w["start"]:
            w["end"] = round(w["start"] + 0.05, 3)
        if duration:
            w["end"] = min(w["end"], round(duration, 3))
            w["start"] = min(w["start"], w["end"])
        w["i"] = i
    return words


def build_sentences(words):
    """Group words into sentence-ish lines for the reading view."""
    sentences = []
    current = []

    def flush():
        if not current:
            return
        text = "".join(w["text"] for w in current).strip()
        sentences.append({
            "start": current[0]["start"],
            "end": current[-1]["end"],
            "first_word": current[0]["i"],
            "last_word": current[-1]["i"],
            "text": text,
        })
        current.clear()

    for idx, word in enumerate(words):
        if current and word["start"] - current[-1]["end"] > MAX_SENTENCE_GAP:
            flush()
        current.append(word)
        ends_sentence = bool(SENTENCE_END.search(word["text"].strip()))
        nxt = words[idx + 1] if idx + 1 < len(words) else None
        if ends_sentence or len(current) >= MAX_SENTENCE_WORDS or nxt is None:
            flush()
    return sentences


def render_reading_view(meta, sentences):
    lines = [
        f"# Transcript: {os.path.basename(meta['source']['path'])}",
        f"# duration={meta['duration']}s language={meta['language']} "
        f"words={meta['word_count']} model={meta['model']}",
        "# Format: [start-end] sentence   (times in seconds from the start of the source video)",
        "# A blank line marks a pause longer than 2 seconds.",
        "",
    ]
    prev_end = None
    for s in sentences:
        if prev_end is not None and s["start"] - prev_end > 2.0:
            lines.append("")
        lines.append(f"[{s['start']:.1f}-{s['end']:.1f}] {s['text']}")
        prev_end = s["end"]
    return "\n".join(lines) + "\n"


def build_output(words, source, duration, language, language_probability, model,
                 device, compute_type, elapsed):
    words = clean_word_times(words, duration)
    sentences = build_sentences(words)
    return {
        "version": 2,
        "source": source,
        "language": language,
        "language_probability": language_probability,
        "duration": round(duration, 2),
        "word_count": len(words),
        "model": model,
        "device": device,
        "compute_type": compute_type,
        "transcription_time_sec": round(elapsed, 1),
        "sentences": sentences,
        "words": words,
    }


def summary(output, out_path, txt_path, cached, note=""):
    elapsed = output.get("transcription_time_sec") or 0
    data = {
        "action": "transcribe",
        "cached": cached,
        "output": out_path,
        "reading_view": txt_path,
        "language": output["language"],
        "duration": output["duration"],
        "word_count": output["word_count"],
        "sentence_count": len(output["sentences"]),
        "model": output["model"],
        "device": output["device"],
        "transcription_time_sec": elapsed,
        "realtime_factor": round(output["duration"] / elapsed, 1) if elapsed else None,
    }
    if note:
        data["note"] = note
    print(json.dumps(data, indent=2))


def main():
    parser = argparse.ArgumentParser(description="Transcribe video with faster-whisper")
    parser.add_argument("input", help="Input video or audio file")
    parser.add_argument("--output", required=True, help="Output JSON path (transcript.json)")
    parser.add_argument("--model", default="large-v3-turbo",
                        help="Whisper model (default: large-v3-turbo; large-v3 for max accuracy, "
                             "small for low VRAM)")
    parser.add_argument("--language", default=None,
                        help="Language code (e.g. en). Default: auto-detect")
    parser.add_argument("--initial-prompt", default=None,
                        help="Spelling hints: names, jargon, product names in the video")
    parser.add_argument("--device", default="auto", choices=["auto", "cuda", "cpu"])
    parser.add_argument("--compute-type", default="auto",
                        choices=["auto", "float16", "int8_float16", "int8", "float32"])
    parser.add_argument("--batch-size", type=int, default=0,
                        help="Batched inference (e.g. 8) for faster GPU transcription. "
                             "Default 0 = sequential")
    parser.add_argument("--force", action="store_true", help="Ignore cached transcript")
    args = parser.parse_args()

    if not os.path.isfile(args.input):
        fail(f"Input file not found: {args.input}")

    out_path = os.path.abspath(args.output)
    txt_path = os.path.splitext(out_path)[0] + ".txt"
    source = fingerprint(args.input)

    # Cache hit?
    if not args.force and os.path.isfile(out_path):
        try:
            cached = read_json(out_path)
        except (OSError, json.JSONDecodeError):
            cached = None
        if (cached and cached.get("version") == 2 and cached.get("source") == source
                and cached.get("model") == args.model and cached.get("words")):
            if not os.path.isfile(txt_path):
                with open(txt_path, "w", encoding="utf-8") as f:
                    f.write(render_reading_view(cached, cached["sentences"]))
            summary(cached, out_path, txt_path, cached=True)
            return

    info = probe(args.input)
    if not info or not any(s.get("codec_type") == "audio" for s in info.get("streams", [])):
        fail("Input has no audio stream — nothing to transcribe")

    device, device_note = pick_device(args.device)
    compute_type = args.compute_type
    if compute_type == "auto":
        compute_type = "float16" if device == "cuda" else "int8"

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    # Keep the WAV next to the output rather than in /tmp (tmpfs on many distros).
    audio_path = os.path.splitext(out_path)[0] + ".audio.wav"

    start_time = time.time()
    extract_audio(args.input, audio_path)
    try:
        try:
            words, whisper_info = run_whisper(
                audio_path, args.model, device, compute_type,
                language=args.language, initial_prompt=args.initial_prompt,
                batch_size=args.batch_size)
        except Exception as e:  # noqa: BLE001
            if device != "cuda" or args.device == "cuda":
                fail(f"Transcription failed: {type(e).__name__}: {e}",
                     hint="If the model could not be downloaded, check network access to "
                          "huggingface.co. If memory ran out, try --model small.")
            device_note = f"CUDA transcription failed ({e}); retried on CPU"
            device, compute_type = "cpu", "int8"
            words, whisper_info = run_whisper(
                audio_path, args.model, device, compute_type,
                language=args.language, initial_prompt=args.initial_prompt)
    finally:
        if os.path.exists(audio_path):
            os.unlink(audio_path)
    elapsed = time.time() - start_time

    if not words:
        fail("No speech detected in the input")

    output = build_output(
        words, source, media_duration(args.input),
        whisper_info.language, round(float(whisper_info.language_probability), 3),
        args.model, device, compute_type, elapsed)

    write_json(out_path, output)
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write(render_reading_view(output, output["sentences"]))

    summary(output, out_path, txt_path, cached=False,
            note=device_note if device == "cpu" and args.device == "auto" else "")


if __name__ == "__main__":
    main()
