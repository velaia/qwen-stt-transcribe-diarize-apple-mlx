"""Segment-level accept/reject decisions, without loading a model."""

import numpy as np
import pytest

from qstt import filters
from qstt.audio import SAMPLE_RATE
from qstt.pipeline import Options, Transcriber, _dominant_speaker, _map_to_source


class _FakeResult:
    def __init__(self, text, *, finish_reason="eos", segments=None, language="English"):
        self.text = text
        self.finish_reason = finish_reason
        self.segments = segments
        self.language = language
        self.truncated = False


class _FakeTranscriber(Transcriber):
    """Bypasses model loading; replays scripted decodes instead."""

    def __init__(self, results, opts=None):
        self.opts = opts or Options(vad=False, timestamps=False, diarize=False,
                                    language="English", max_span_sec=5.0)
        self._results = list(results)
        self._pyannote = None

    def _log(self, msg):  # noqa: D102
        pass

    def _asr(self, audio, language, timestamps):
        return self._results.pop(0)


def _audio(seconds=10.0):
    return np.zeros(int(seconds * SAMPLE_RATE), dtype=np.float32)


# Real output from a 2.5-hour podcast that an earlier build discarded.
TRUNCATED_BUT_VALID = (
    "You can solve everything else soon. Yeah, I think that what's happened"
)


def test_early_decoder_stop_does_not_discard_valid_speech():
    """Regression: `finish_reason='repetition'` is not evidence of garbage.

    The decoder stopping early says nothing about the text it already emitted.
    An earlier build treated the stop reason as fatal and silently dropped
    roughly 50 seconds of genuine podcast speech.
    """
    assert filters.screen(TRUNCATED_BUT_VALID) is None

    eng = _FakeTranscriber(
        [_FakeResult(TRUNCATED_BUT_VALID, finish_reason="repetition")] * 2
    )
    result = eng.run_array(_audio(10.0), source="podcast.mp3")

    assert [s.text for s in result.segments] == [TRUNCATED_BUT_VALID] * 2
    assert result.rejections == []
    assert result.stats["reject_reasons"]["truncated-kept"] == 2


def test_genuine_loop_is_still_rejected_even_when_decoding_ended_cleanly():
    eng = _FakeTranscriber([_FakeResult("ja ja ja ja ja ja ja ja")] * 2)
    result = eng.run_array(_audio(10.0), source="x.wav")
    assert result.segments == []
    assert all(r.reason.startswith("repetition") for r in result.rejections)


def test_foreign_language_segment_is_rejected_with_its_script_named():
    eng = _FakeTranscriber([_FakeResult("我不知道这是什么意思")] * 2)
    result = eng.run_array(_audio(10.0), source="x.wav")
    assert result.segments == []
    assert result.rejections[0].reason == "foreign-script:Han"
    # The raw decode is preserved for review rather than silently dropped.
    assert result.rejections[0].text == "我不知道这是什么意思"


def test_stray_glyph_is_stripped_and_the_speech_survives():
    text = ("We agreed to ship on Thursday 千 once the final tests pass "
            "and the whole team has signed off on the release plan")
    eng = _FakeTranscriber([_FakeResult(text)] * 2)
    result = eng.run_array(_audio(10.0), source="x.wav")
    assert len(result.segments) == 2
    assert "千" not in result.segments[0].text
    assert "Thursday" in result.segments[0].text


def test_no_speech_returns_an_empty_result_not_an_error():
    opts = Options(vad=True, timestamps=False, language="English")
    eng = _FakeTranscriber([], opts)
    result = eng.run_array(_audio(5.0), source="silence.wav")
    assert result.segments == []
    assert result.stats["note"] == "no speech detected"


def test_stats_count_kept_and_rejected():
    eng = _FakeTranscriber([_FakeResult("This sentence is perfectly fine."),
                            _FakeResult("我不知道这是什么意思")])
    result = eng.run_array(_audio(10.0), source="x.wav")
    assert result.stats["kept"] == 1
    assert result.stats["rejected"] == 1


@pytest.mark.parametrize(
    "start,end,expected",
    [(0.0, 1.0, "A"), (5.0, 6.0, "B"), (0.0, 10.0, "B"), (20.0, 21.0, None)],
)
def test_dominant_speaker_picks_greatest_overlap(start, end, expected):
    turns = [
        {"speaker": "A", "start": 0.0, "end": 2.0},
        {"speaker": "B", "start": 2.0, "end": 10.0},
    ]
    assert _dominant_speaker(start, end, turns) == expected


def test_words_map_back_onto_punctuated_source():
    source = "Hello there, Daniel. How are you?"
    words = [{"text": w} for w in
             ["Hello", "there", "Daniel", "How", "are", "you"]]
    _map_to_source(words, source)
    assert source[words[1]["_span"][0]:words[1]["_span"][1]] == "there,"
    assert source[words[2]["_span"][0]:words[2]["_span"][1]] == "Daniel."
    assert source[words[5]["_span"][0]:words[5]["_span"][1]] == "you?"


def test_word_mapping_tolerates_a_token_that_is_not_in_the_source():
    words = [{"text": "Hello"}, {"text": "zzz"}, {"text": "world"}]
    _map_to_source(words, "Hello world.")
    assert words[1]["_span"] is None
    assert words[2]["_span"] is not None
