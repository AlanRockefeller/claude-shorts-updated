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

    # Changing a setting that affects the transcript must not reuse the cache.
    monkeypatch.setattr(sys, "argv", ["transcribe.py", str(video), "--output", str(out),
                                      "--initial-prompt", "Names: OmniRoute"])
    transcribe.main()
    assert len(calls) == 2

    summaries = [json.loads(chunk) for chunk in capsys.readouterr().out.strip().split("\n}\n")
                 if chunk.strip() for chunk in [chunk if chunk.endswith("}") else chunk + "\n}"]]
    assert [s["cached"] for s in summaries] == [False, True, False]
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


# --- jump cuts ------------------------------------------------------------------------

import jumpcut  # noqa: E402
from shorts_common import retime  # noqa: E402


def total(ranges):
    return sum(end - start for start, end in ranges)


def test_long_pause_is_shortened_and_short_pause_left_alone():
    # speech 0-4, silence 4-7 (long), speech 7-9, silence 9-9.4 (short), speech 9.4-12
    keep, stats = jumpcut.plan_cuts(
        12.0, silences=[(4.0, 7.0), (9.0, 9.4)], dropped_words=[],
        kept_words=[(0.2, 4.0), (7.0, 9.0), (9.4, 11.8)], max_pause=0.5, keep_pause=0.2)
    assert keep == [[0.0, 4.1], [6.9, 12.0]]
    assert stats["pauses_cut"] == 1 and stats["filler_words_cut"] == 0
    assert abs(stats["removed_sec"] - 2.8) < 1e-6


def test_filler_is_cut_with_its_surrounding_silence():
    # "word [pause] um [pause] word": the filler and both pauses go, one short pause stays
    keep, stats = jumpcut.plan_cuts(
        10.0, silences=[(2.0, 2.6), (3.0, 3.7)], dropped_words=[(2.6, 3.0)],
        kept_words=[(0.5, 2.0), (3.7, 9.5)], max_pause=0.5, keep_pause=0.2)
    assert keep == [[0.0, 2.1], [3.6, 10.0]]
    assert stats["filler_words_cut"] == 1


def test_filler_in_continuous_speech_is_cut_at_word_boundaries():
    keep, stats = jumpcut.plan_cuts(
        6.0, silences=[], dropped_words=[(2.0, 2.3)],
        kept_words=[(1.0, 2.0), (2.3, 3.0)], max_pause=0.5, keep_pause=0.2)
    assert keep == [[0.0, 2.0], [2.3, 6.0]]


def test_kept_words_are_never_cut_even_if_audio_reads_as_silent():
    # A quiet speaker: the level meter calls 1-5 s "silence", but words are there.
    keep, stats = jumpcut.plan_cuts(
        6.0, silences=[(1.0, 5.0)], dropped_words=[],
        kept_words=[(1.0, 1.6), (1.7, 2.4), (2.5, 3.2), (3.3, 4.0), (4.1, 5.0)],
        max_pause=0.5, keep_pause=0.2)
    assert keep == [[0.0, 6.0]] and stats["removed_sec"] == 0


def test_leading_filler_and_trailing_silence():
    keep, _ = jumpcut.plan_cuts(
        10.0, silences=[(0.4, 0.9), (8.0, 10.0)], dropped_words=[(0.0, 0.4)],
        kept_words=[(0.9, 8.0)], max_pause=0.5, keep_pause=0.2)
    assert keep[0][0] == pytest.approx(0.9 - jumpcut.HEAD_ROOM)   # starts just before speech
    assert keep[-1][1] == pytest.approx(8.0 + jumpcut.TAIL_ROOM)  # short tail, not 2 s of nothing


def test_video_frames_track_audio_without_drift():
    # Many awkward cut lengths: video must stay within one frame of the audio throughout.
    fps = 30.0
    keep, t = [], 0.0
    for i in range(60):
        length = 0.37 + 0.011 * (i % 7)
        keep.append([t, t + length])
        t += length + 0.5
    frames = jumpcut.video_frame_ranges(keep, fps)
    audio_total = video_total = 0.0
    for (start, end), (first, last) in zip(keep, frames):
        assert last > first
        assert abs(first / fps - start) <= 0.5 / fps + 1e-9
        audio_total += end - start
        video_total += (last - first) / fps
        assert abs(audio_total - video_total) <= 0.5 / fps + 1e-9


def test_retime_maps_times_onto_the_cut_timeline():
    keep = [[0.0, 2.0], [5.0, 8.0]]
    assert retime(1.5, keep) == (1.5, True)
    assert retime(6.0, keep) == (3.0, True)
    assert retime(3.0, keep) == (2.0, False)   # inside the removed range
    assert retime(9.0, keep) == (5.0, False)   # after the end


def test_captions_follow_jump_cuts(tmp_path, capsys):
    words = make_words(["Nobody tells you the hidden cost."], start=1.0, word=0.3, gap=0.1)
    # Pretend a 2 s pause after "you" (word index 2) was cut down: shift later words.
    for w in words[3:]:
        w["start"] += 2.0
        w["end"] += 2.0
    seg = {"id": 1, "start": 0.5, "end": words[-1]["end"] + 0.3, "word_start": 0,
           "word_end": len(words) - 1}
    uncut = run_apply(tmp_path, words, seg)
    pause_start = words[2]["end"] - seg["start"] + 0.1
    seg["keep"] = [[0.0, pause_start], [pause_start + 1.8, seg["end"] - seg["start"]]]
    cut = run_apply(tmp_path, words, seg)
    assert [c["text"] for c in cut] == [c["text"] for c in uncut]
    for before, after in zip(uncut[:3], cut[:3]):
        assert after["startMs"] == before["startMs"]
    for before, after in zip(uncut[3:], cut[3:]):
        assert after["startMs"] == pytest.approx(before["startMs"] - 1800, abs=2)
        assert after["endMs"] - after["startMs"] == pytest.approx(300, abs=2)


# --- active speaker, b-roll -------------------------------------------------------------

import active_speaker  # noqa: E402
import broll  # noqa: E402


def activity(pattern, step=0.1):
    """pattern: [(seconds, left_level, right_level)] -> (times, [left, right])"""
    times, left, right = [], [], []
    t = 0.0
    for seconds, a, b in pattern:
        for _ in range(int(round(seconds / step))):
            times.append(round(t, 2)); left.append(a); right.append(b)
            t += step
    return times, [left, right]


def test_switches_follow_the_speaker():
    times, scores = activity([(6, 5.0, 0.3), (6, 0.2, 4.0), (5, 6.0, 0.4)])
    switches, confidence = active_speaker.plan_switches(times, scores)
    assert [s for _, s in switches] == [0, 1, 0]
    assert abs(switches[1][0] - 6.0) < 0.8 and abs(switches[2][0] - 12.0) < 0.8
    assert confidence > 0.8


def test_brief_interjections_do_not_cause_a_cut():
    # The other person says "yeah" for 0.3 s: stay on the main speaker.
    times, scores = activity([(5, 5.0, 0.2), (0.3, 0.3, 5.0), (5, 5.0, 0.2)])
    switches, _ = active_speaker.plan_switches(times, scores)
    assert switches == [(0.0, 0)]


def test_cuts_are_never_closer_than_the_minimum_hold():
    times, scores = activity([(1, 5, 0.1), (1, 0.1, 5), (1, 5, 0.1), (1, 0.1, 5), (4, 5, 0.1)])
    switches, _ = active_speaker.plan_switches(times, scores, min_hold=1.5)
    gaps = [b[0] - a[0] for a, b in zip(switches, switches[1:])]
    assert all(g >= 1.5 - 1e-6 for g in gaps)


def test_no_mouth_movement_gives_no_confidence():
    times, scores = activity([(10, 0.0, 0.0)])
    switches, confidence = active_speaker.plan_switches(times, scores)
    assert confidence == 0.0 and len(switches) == 1


def test_broll_is_anchored_to_words_through_jump_cuts(tmp_path):
    image = tmp_path / "chart.png"
    image.write_bytes(b"x")
    words = make_words(["Nobody tells you the hidden cost of scaling a team."], start=10.0)
    index = {w["i"]: w for w in words}
    seg = {"id": 1, "start": 9.9, "end": words[-1]["end"] + 0.3}
    item = {"file": "chart.png", "from_word": 4, "to_word": 9}

    plain, problem = broll.resolve_item(item, seg, index, str(tmp_path))
    assert problem is None and plain["kind"] == "image"
    assert plain["start"] == pytest.approx(words[4]["start"] - 9.9 - broll.LEAD, abs=0.01)

    # Cut 1 s out of the clip before the anchor word: the cutaway moves 1 s earlier.
    seg_cut = dict(seg, keep=[[0.0, 0.5], [1.5, seg["end"] - seg["start"]]],
                   render_duration=seg["end"] - seg["start"] - 1.0)
    moved, problem = broll.resolve_item(item, seg_cut, index, str(tmp_path))
    assert problem is None
    assert moved["start"] == pytest.approx(plain["start"] - 1.0, abs=0.01)
    assert moved["start"] + moved["duration"] <= seg_cut["render_duration"] + 1e-6

    missing, problem = broll.resolve_item({"file": "nope.mp4", "from_word": 4}, seg, index, str(tmp_path))
    assert missing is None and "not found" in problem


def test_stretched_word_keeps_its_caption_after_a_pause_is_cut(tmp_path, capsys):
    from shorts_common import retime_span

    # Whisper gave the word 0.2-3.0 s, but only 0.2-0.8 is speech; the pause was cut.
    keep = [[0.0, 0.8], [3.1, 4.0]]
    assert retime_span(0.2, 3.0, keep) == pytest.approx((0.2, 0.8))
    assert retime_span(1.0, 2.9, keep) is None            # entirely inside the cut
    assert retime_span(3.2, 3.6, keep) == pytest.approx((0.9, 1.3))

    words = transcribe.clean_word_times([
        {"text": " Wait", "start": 10.2, "end": 13.0, "prob": 0.9},
        {"text": " what?", "start": 13.2, "end": 13.6, "prob": 0.9},
    ])
    seg = {"id": 1, "start": 10.0, "end": 14.0, "keep": keep, "render_duration": 1.7}
    caps = run_apply(tmp_path, words, seg)
    assert [c["text"] for c in caps] == [" Wait", " what?"]
    assert caps[0]["startMs"] == 200 and caps[0]["endMs"] == 800
    assert caps[1]["startMs"] == 900
