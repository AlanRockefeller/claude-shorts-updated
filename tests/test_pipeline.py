"""Unit tests for the pure-Python parts of the pipeline (no GPU, model or FFmpeg)."""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))

import captions  # noqa: E402
import compute_reframe  # noqa: E402
import detect_content  # noqa: E402
import snap_boundaries as snap  # noqa: E402
import transcribe  # noqa: E402


def make_words(sentences, start=1.0, word=0.3, gap=0.1, pause_after=None):
    """Build word dicts from a list of sentence strings."""
    words, t = [], start
    for s_idx, sentence in enumerate(sentences):
        for token in sentence.split():
            words.append({"text": " " + token, "start": round(t, 3), "end": round(t + word, 3),
                          "prob": 0.95})
            t += word + gap
        if pause_after and s_idx in pause_after:
            t += pause_after[s_idx]
    return transcribe.clean_word_times(words)


SENTENCES = [
    "So today I want to talk about scaling.",
    "Nobody tells you the hidden cost.",
    "We lost forty percent of our users in a week.",
    "Here is what fixed it.",
    "Ship less and measure more.",
]


# --- transcribe ---------------------------------------------------------------

def test_sentences_and_reading_view():
    words = make_words(SENTENCES, pause_after={2: 3.0})
    sentences = transcribe.build_sentences(words)
    assert [s["text"] for s in sentences] == SENTENCES
    assert sentences[1]["first_word"] == 8 and sentences[1]["last_word"] == 13

    meta = {"source": {"path": "/x/talk.mp4"}, "duration": 60, "language": "en",
            "word_count": len(words), "model": "test"}
    view = transcribe.render_reading_view(meta, sentences)
    lines = view.splitlines()
    assert any(line.startswith("[1.0-") and line.endswith("scaling.") for line in lines)
    # the 3 s pause after sentence 3 shows up as a blank line
    idx = next(i for i, line in enumerate(lines) if "forty percent" in line)
    assert lines[idx + 1] == ""


def test_long_unpunctuated_speech_is_split():
    words = make_words([" ".join(["word"] * 120)])
    sentences = transcribe.build_sentences(words)
    assert len(sentences) >= 3
    assert all(len(s["text"].split()) <= transcribe.MAX_SENTENCE_WORDS for s in sentences)


def test_word_times_are_sanitised():
    words = transcribe.clean_word_times([
        {"text": " b", "start": 2.0, "end": 2.0, "prob": 1},   # zero length
        {"text": " a", "start": 1.0, "end": 1.4, "prob": 1},   # out of order
    ], duration=10)
    assert [w["text"] for w in words] == [" a", " b"]
    assert [w["i"] for w in words] == [0, 1]
    assert words[1]["end"] > words[1]["start"]


def test_transcript_is_cached(tmp_path, monkeypatch, capsys):
    video = tmp_path / "v.mp4"
    video.write_bytes(b"not really a video")
    out = tmp_path / "work" / "transcript.json"
    calls = []

    class Info:
        language, language_probability = "en", 0.99

    def fake_whisper(*args, **kwargs):
        calls.append(1)
        return make_words(SENTENCES), Info()

    monkeypatch.setattr(transcribe, "run_whisper", fake_whisper)
    monkeypatch.setattr(transcribe, "extract_audio", lambda v, a: open(a, "wb").close())
    monkeypatch.setattr(transcribe, "probe", lambda p: {"streams": [{"codec_type": "audio"}]})
    monkeypatch.setattr(transcribe, "media_duration", lambda p: 30.0)
    monkeypatch.setattr(transcribe, "pick_device", lambda d: ("cpu", "test"))

    for _ in range(2):
        monkeypatch.setattr(sys, "argv", ["transcribe.py", str(video), "--output", str(out)])
        transcribe.main()
    assert len(calls) == 1, "second run should come from the cache"

    summaries = [json.loads(chunk) for chunk in capsys.readouterr().out.strip().split("\n}\n")
                 if chunk.strip() for chunk in [chunk if chunk.endswith("}") else chunk + "\n}"]]
    assert [s["cached"] for s in summaries] == [False, True]
    data = json.loads(out.read_text())
    assert data["version"] == 2 and data["word_count"] == len(data["words"])
    assert (tmp_path / "work" / "transcript.txt").exists()
    assert not (tmp_path / "work" / "transcript.audio.wav").exists()


def test_cpu_fallback_when_no_gpu():
    device, note = transcribe.pick_device("auto")
    assert device in ("cpu", "cuda") and note


# --- snap_boundaries ------------------------------------------------------------

def test_start_prefers_sentence_start_and_end_completes_sentence():
    words = make_words(SENTENCES)
    # Proposed range starts mid-sentence 2 and stops mid-sentence 3.
    s2, s3 = words[8], words[22]
    start, end, first, last, _ = snap.snap_segment(
        words, {"start": s2["start"] + 0.5, "end": s3["start"] - 0.5}, 60.0, 1.0, 180.0)
    assert words[first]["text"].strip() == "Nobody"
    assert words[last]["text"].strip() == "week."
    assert start <= words[first]["start"] and start >= words[first - 1]["end"]
    assert end >= words[last]["end"] and end <= words[last + 1]["start"]


def test_never_cuts_mid_word_when_enforcing_max_duration():
    words = make_words(SENTENCES * 12)  # ~3 minutes of speech
    start, end, first, last, notes = snap.snap_segment(
        words, {"start": 1.0, "end": 170.0}, 400.0, 5.0, 60.0)
    assert end - start <= 60.0
    assert snap.ends_sentence(words[last])
    assert all(not (w["start"] < end < w["end"]) for w in words), "cut lands inside a word"
    assert any("shortened" in n for n in notes)


def test_min_duration_extends_to_later_sentence_end():
    words = make_words(SENTENCES)
    start, end, first, last, notes = snap.snap_segment(
        words, {"start": words[8]["start"], "end": words[9]["end"]}, 60.0, 8.0, 180.0)
    assert end - start >= 8.0
    assert snap.ends_sentence(words[last])


def test_lead_in_does_not_reach_into_previous_word():
    words = make_words(SENTENCES, gap=0.01)  # nearly continuous speech
    start, _, first, _, _ = snap.snap_segment(
        words, {"start": words[8]["start"], "end": words[13]["end"]}, 60.0, 1.0, 180.0)
    assert start > words[first - 1]["end"]


# --- captions ---------------------------------------------------------------------

def run_apply(tmp_path, words, seg, edits=None, keep_fillers=False):
    transcript = tmp_path / "t.json"
    segments = tmp_path / "s.json"
    out = tmp_path / "c.json"
    transcript.write_text(json.dumps({"words": words}))
    segments.write_text(json.dumps({"segments": [seg]}))
    argv = ["captions.py", "apply", "--segments", str(segments), "--transcript", str(transcript),
            "--output", str(out)]
    if edits is not None:
        edits_path = tmp_path / "e.json"
        edits_path.write_text(json.dumps(edits))
        argv += ["--edits", str(edits_path)]
    if keep_fillers:
        argv.append("--keep-fillers")
    old = sys.argv
    sys.argv = argv
    try:
        captions.main()
    finally:
        sys.argv = old
    return json.loads(out.read_text())["by_segment"][str(seg["id"])]


def test_caption_edits_keep_timing(tmp_path, capsys):
    words = make_words(["Nobody um tells you the hidden cost."])
    seg = {"id": 3, "start": words[0]["start"] - 0.1, "end": words[-1]["end"] + 0.3,
           "word_start": 0, "word_end": len(words) - 1}
    # words: Nobody(0) um(1) tells(2) you(3) the(4) hidden(5) cost.(6)
    caps = run_apply(tmp_path, words, seg,
                     {"3": {"delete": [3], "replace": {"6": "price."}, "emphasis": [6]}})
    texts = [c["text"] for c in caps]
    assert texts == [" Nobody", " tells", " the", " hidden", " price."]  # um auto-removed
    assert caps[0]["startMs"] == 100
    hidden = next(c for c in caps if c["text"] == " hidden")
    assert hidden["startMs"] == round((words[5]["start"] - seg["start"]) * 1000)
    assert caps[-1].get("emphasis") is True
    assert all(c["endMs"] > c["startMs"] >= 0 for c in caps)


def test_keep_fillers_flag(tmp_path, capsys):
    words = make_words(["Nobody um knows."])
    seg = {"id": 1, "start": 0.5, "end": 3.0}
    assert len(run_apply(tmp_path, words, seg, keep_fillers=True)) == 3
    assert len(run_apply(tmp_path, words, seg)) == 2


# --- reframing ----------------------------------------------------------------------

def face(cx, cy=0.4, size=0.1):
    return {"cx": cx, "cy": cy, "w": size, "h": size * 1.3, "score": 0.9}


def test_two_far_apart_speakers_get_split_panels():
    frames = [(i / 2, [face(0.2), face(0.8)]) for i in range(40)]
    speakers = compute_reframe.cluster_speakers(frames)
    assert len(speakers) == 2
    left, right = sorted(speakers, key=lambda s: s["cx"])
    panels = compute_reframe.split_panels(left, right, 1920, 1080)
    a, b = panels[0]["crop"], panels[1]["crop"]
    assert a["x"] + a["w"] <= 0.8 * 1920 - 0.05 * 1920, "left panel must not show the right speaker"
    assert b["x"] >= 0.2 * 1920 + 0.05 * 1920, "right panel must not show the left speaker"
    for crop in (a, b):
        assert crop["x"] >= 0 and crop["y"] >= 0
        assert crop["x"] + crop["w"] <= 1920 and crop["y"] + crop["h"] <= 1080
        assert abs(crop["w"] / crop["h"] - 1080 / 960) < 0.01


def test_moving_speaker_gets_keyframes_and_still_speaker_does_not():
    moving = [(i / 2, [face(0.2 + 0.6 * i / 40)]) for i in range(40)]
    speaker = compute_reframe.cluster_speakers(moving)[0]
    crop, keyframes = compute_reframe.follow_speaker(speaker, 1920, 1080, 20.0)
    assert len(keyframes) >= 3
    assert keyframes[0]["x"] < keyframes[-1]["x"]
    assert all(0 <= k["x"] <= 1920 - crop["w"] for k in keyframes)

    still = [(i / 2, [face(0.5 + 0.01 * (i % 3))]) for i in range(40)]
    speaker = compute_reframe.cluster_speakers(still)[0]
    crop, keyframes = compute_reframe.follow_speaker(speaker, 1920, 1080, 20.0)
    assert keyframes == []
    assert abs(crop["x"] + crop["w"] / 2 - 0.5 * 1920) < 40


def test_brief_false_positives_are_ignored():
    frames = [(i / 2, [face(0.5)] + ([face(0.9, size=0.03)] if i == 7 else [])) for i in range(40)]
    assert len(compute_reframe.cluster_speakers(frames)) == 1


@pytest.mark.parametrize("stats,expected", [
    ({"avg_count": 0.1, "avg_size_pct": 0.5, "center_bias": 0.2, "frames_with_faces": 1, "total_frames": 16}, "screen"),
    ({"avg_count": 1.0, "avg_size_pct": 9.0, "center_bias": 0.9, "frames_with_faces": 16, "total_frames": 16}, "talking-head"),
    ({"avg_count": 1.0, "avg_size_pct": 1.8, "center_bias": 0.8, "frames_with_faces": 16, "total_frames": 16}, "talking-head"),
    ({"avg_count": 2.0, "avg_size_pct": 1.2, "center_bias": 0.4, "frames_with_faces": 16, "total_frames": 16}, "podcast"),
    ({"avg_count": 1.0, "avg_size_pct": 1.0, "center_bias": 0.2, "frames_with_faces": 16, "total_frames": 16}, "screen"),
])
def test_content_classification(stats, expected):
    assert detect_content.classify(stats)[0] == expected
