#!/usr/bin/env python3
"""Build per-segment caption tracks, with optional edits from Claude.

Two subcommands:

  dump   Print the words of each approved segment with their transcript index,
         so Claude can review them and write a small edits file instead of
         rewriting the transcript:

             python3 captions.py dump --segments WORK/snapped_segments.json \
                 --transcript WORK/transcript.json --output WORK/caption_words.txt

  apply  Produce the caption file Remotion renders from:

             python3 captions.py apply --segments WORK/snapped_segments.json \
                 --transcript WORK/transcript.json \
                 --edits WORK/caption_edits.json --output WORK/captions.json

Edits file (all keys optional, word indexes are the `i` values from `dump`):

    {
      "1": {
        "delete":   [412, 413],                # drop filler / false starts
        "replace":  {"430": "Remotion"},       # fix a mis-heard word
        "emphasis": [421, 447]                 # key words to highlight
      }
    }

Timing is never changed: captions stay in sync with the audio.
Output times are clip-local milliseconds.
"""
import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from shorts_common import fail, read_json, retime_span, write_json  # noqa: E402

# Only unambiguous hesitation sounds are removed automatically. Context-dependent
# fillers ("like", "you know", "right") are left for Claude to judge.
AUTO_FILLERS = {"um", "umm", "uh", "uhh", "uhm", "er", "erm", "hmm", "mm", "mhm", "ah"}
LOW_CONFIDENCE = 0.5


def normalise(text):
    return re.sub(r"[^\w']+", "", text.lower())


def segment_words(words, seg):
    """Words that belong to a segment (by index if snapped, else by time)."""
    if "word_start" in seg and "word_end" in seg:
        return [w for w in words if seg["word_start"] <= w["i"] <= seg["word_end"]]
    return [w for w in words
            if w["start"] >= seg["start"] - 0.05 and w["end"] <= seg["end"] + 0.05]


def load(segments_path, transcript_path):
    segments = read_json(segments_path)["segments"]
    transcript = read_json(transcript_path)
    words = transcript.get("words")
    if not words:
        fail("Transcript has no words[] — re-run transcribe.py")
    return segments, words


def cmd_dump(args):
    segments, words = load(args.segments, args.transcript)
    lines = [
        "# Caption words per segment. Columns: index | start(s) | word",
        "# '?' marks low-confidence words (likely mis-heard: check names, jargon, numbers).",
        "# '~' marks hesitation sounds that are removed automatically.",
        "",
    ]
    for seg in segments:
        seg_words = segment_words(words, seg)
        lines.append(f"## Segment {seg.get('id')}  [{seg['start']:.2f}-{seg['end']:.2f}]  "
                     f"{len(seg_words)} words")
        lines.append("".join(w["text"] for w in seg_words).strip())
        lines.append("")
        for w in seg_words:
            flag = " "
            if normalise(w["text"]) in AUTO_FILLERS:
                flag = "~"
            elif w.get("prob", 1.0) < LOW_CONFIDENCE:
                flag = "?"
            lines.append(f"{w['i']:>6} {flag} {w['start']:>8.2f}  {w['text'].strip()}")
        lines.append("")

    text = "\n".join(lines)
    if args.output:
        os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(text)
        print(json.dumps({"action": "captions_dump", "output": args.output,
                          "segments": len(segments)}))
    else:
        print(text)


def cmd_apply(args):
    segments, words = load(args.segments, args.transcript)
    edits = read_json(args.edits) if args.edits else {}

    by_segment, report = {}, []
    for seg in segments:
        seg_id = str(seg.get("id"))
        seg_edits = edits.get(seg_id, {})
        delete = {int(i) for i in seg_edits.get("delete", [])}
        replace = {int(k): v for k, v in seg_edits.get("replace", {}).items()}
        emphasis = {int(i) for i in seg_edits.get("emphasis", [])}

        seg_words = segment_words(words, seg)
        valid = {w["i"] for w in seg_words}
        unknown = sorted((delete | set(replace) | emphasis) - valid)

        start_ms = seg["start"] * 1000.0
        duration_ms = (seg["end"] - seg["start"]) * 1000.0
        keep = seg.get("keep")
        captions, auto_removed, cut_out = [], 0, 0
        for w in seg_words:
            if w["i"] in delete:
                continue
            text = w["text"]
            if w["i"] in replace:
                new_text = str(replace[w["i"]]).strip()
                if not new_text:
                    continue
                text = (" " if text.startswith(" ") else "") + new_text
            elif not args.keep_fillers and normalise(text) in AUTO_FILLERS:
                auto_removed += 1
                continue

            cap_start = max(0.0, w["start"] * 1000.0 - start_ms)
            cap_end = min(duration_ms, w["end"] * 1000.0 - start_ms)
            if keep:
                # Jump cuts changed the clip's timeline: move the word with it,
                # and drop words whose audio was cut out.
                span = retime_span(cap_start / 1000.0, cap_end / 1000.0, keep)
                if span is None:
                    cut_out += 1
                    continue
                cap_start = span[0] * 1000.0
                cap_end = max(span[1] * 1000.0, cap_start + 80.0)
            if cap_end <= cap_start:
                continue
            caption = {"text": text, "startMs": round(cap_start), "endMs": round(cap_end)}
            if w["i"] in emphasis:
                caption["emphasis"] = True
            captions.append(caption)

        by_segment[seg_id] = captions
        entry = {
            "id": seg.get("id"),
            "words": len(captions),
            "deleted": len(delete & valid),
            "replaced": len(set(replace) & valid),
            "emphasised": len(emphasis & valid),
            "auto_fillers_removed": auto_removed,
        }
        if keep:
            entry["jump_cut"] = True
            entry["words_in_cut_ranges"] = cut_out
        if unknown:
            entry["ignored_indexes_outside_segment"] = unknown
        report.append(entry)

    stray = sorted(set(edits) - {str(s.get("id")) for s in segments})
    write_json(args.output, {"version": 2, "by_segment": by_segment})
    out = {"action": "captions_apply", "output": args.output, "segments": report}
    if stray:
        out["warning"] = f"Edits reference unknown segment ids: {stray}"
    print(json.dumps(out, indent=2))


def main():
    parser = argparse.ArgumentParser(description="Per-segment caption tracks")
    sub = parser.add_subparsers(dest="command", required=True)

    dump = sub.add_parser("dump", help="List segment words with indexes for review")
    dump.add_argument("--segments", required=True)
    dump.add_argument("--transcript", required=True)
    dump.add_argument("--output", help="Write to file instead of stdout")
    dump.set_defaults(func=cmd_dump)

    apply_cmd = sub.add_parser("apply", help="Write captions.json for rendering")
    apply_cmd.add_argument("--segments", required=True)
    apply_cmd.add_argument("--transcript", required=True)
    apply_cmd.add_argument("--edits", help="Edits JSON written by Claude (optional)")
    apply_cmd.add_argument("--output", required=True)
    apply_cmd.add_argument("--keep-fillers", action="store_true",
                           help="Do not auto-remove um/uh hesitation sounds")
    apply_cmd.set_defaults(func=cmd_apply)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
