"""Hooks the web UI relies on: progress/cancel, deferred smoothing, JSON reload."""

import json

import numpy as np
import pytest

from qstt import writers
from qstt.audio import SAMPLE_RATE
from qstt.pipeline import Options, Rejection, Result, Segment, Transcriber


class _FakeResult:
    def __init__(self, text):
        self.text = text
        self.finish_reason = "eos"
        self.segments = None
        self.language = "English"
        self.truncated = False


class _FakeTranscriber(Transcriber):
    """Bypasses model and diarization loading; replays scripted decodes."""

    def __init__(self, texts, opts, turns=None):
        self.opts = opts
        self._texts = list(texts)
        self._turns = turns or []
        self._pyannote = None
        self.calls = 0

    def _log(self, msg):
        pass

    def _asr(self, audio, language, timestamps):
        self.calls += 1
        return _FakeResult(self._texts.pop(0))

    def _speaker_turns(self, audio):
        return self._turns


def _opts(**kw):
    base = dict(vad=False, timestamps=False, language="English", max_span_sec=5.0)
    return Options(**(base | kw))


def _audio(seconds=10.0):
    return np.zeros(int(seconds * SAMPLE_RATE), dtype=np.float32)


SENTENCES = ["The first sentence is fine.", "The second one is fine too."]


def test_progress_reports_stages_in_order_and_span_fractions():
    seen = []
    eng = _FakeTranscriber(SENTENCES, _opts())
    eng.run_array(_audio(), progress=lambda stage, frac: seen.append((stage, frac)))

    stages = [s for s, _ in seen]
    assert stages[0] == "vad"
    assert stages.index("language") < stages.index("transcribe")
    assert [f for s, f in seen if s == "transcribe"] == [0.0, 0.5]
    assert seen[-1] == ("done", 1.0)


def test_progress_callback_can_cancel_between_spans():
    class Cancelled(Exception):
        pass

    def progress(stage, frac):
        if stage == "transcribe" and frac and frac >= 0.5:
            raise Cancelled

    eng = _FakeTranscriber(SENTENCES, _opts())
    with pytest.raises(Cancelled):
        eng.run_array(_audio(), progress=progress)
    assert eng.calls == 1  # the second span was never decoded


def test_no_speech_still_reports_done():
    seen = []
    eng = _FakeTranscriber([], _opts(vad=True))
    eng.run_array(_audio(5.0), progress=lambda s, f: seen.append((s, f)))
    assert seen[-1] == ("done", 1.0)


TURNS = [{"speaker": "SPEAKER_00", "start": 0.0, "end": 10.0}]


def test_smoothing_merges_same_speaker_by_default():
    eng = _FakeTranscriber(SENTENCES, _opts(diarize=True), TURNS)
    result = eng.run_array(_audio())
    assert len(result.segments) == 1
    assert result.segments[0].speaker == "SPEAKER_00"


def test_smooth_false_keeps_speakers_but_leaves_segments_unmerged():
    eng = _FakeTranscriber(SENTENCES, _opts(diarize=True, smooth=False), TURNS)
    result = eng.run_array(_audio())
    assert [s.text for s in result.segments] == SENTENCES
    assert {s.speaker for s in result.segments} == {"SPEAKER_00"}
    assert result.speakers == ["SPEAKER_00"]


def _sample_result():
    words = [{"text": "Hallo", "start": 0.5, "end": 0.9, "speaker": "SPEAKER_01"}]
    return Result(
        source="/tmp/interview.m4a",
        duration=12.5,
        language="German",
        segments=[Segment(0.5, 0.9, "Hallo.", "SPEAKER_01", "German", words)],
        rejections=[Rejection(3.0, 4.0, "我", "foreign-script:Han")],
        speakers=["SPEAKER_01"],
        stats={"vad": "silero", "speech_ratio": 0.5},
    )


def test_load_result_is_the_inverse_of_to_json():
    first = writers.to_json(_sample_result())
    again = writers.to_json(writers.load_result(json.loads(first)))
    assert again == first


def test_load_result_reads_a_file(tmp_path):
    path = tmp_path / "t.json"
    path.write_text(writers.to_json(_sample_result()), encoding="utf-8")
    result = writers.load_result(path)
    assert result.segments[0].words[0]["text"] == "Hallo"
    assert result.rejections[0].reason == "foreign-script:Han"
