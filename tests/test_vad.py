"""VAD keeps silence away from the ASR model."""

import numpy as np

from qstt.audio import SAMPLE_RATE
from qstt.vad import Span, detect_speech, speech_ratio


def _tone(seconds: float, freq: float = 180.0, amp: float = 0.3) -> np.ndarray:
    t = np.arange(int(seconds * SAMPLE_RATE)) / SAMPLE_RATE
    # Amplitude-modulated tone: closer to speech than a pure sine.
    env = 0.5 + 0.5 * np.sin(2 * np.pi * 3.0 * t)
    return (amp * env * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def _silence(seconds: float) -> np.ndarray:
    return np.zeros(int(seconds * SAMPLE_RATE), dtype=np.float32)


def test_pure_silence_yields_no_spans():
    spans, _ = detect_speech(_silence(10.0))
    assert spans == []


def test_speech_ratio_of_empty_is_zero():
    assert speech_ratio([], 10.0) == 0.0
    assert speech_ratio([], 0.0) == 0.0


def test_spans_never_exceed_the_length_cap():
    audio = np.concatenate([_tone(60.0)])
    spans, _ = detect_speech(audio, max_span_sec=10.0)
    assert all(s.dur <= 10.01 for s in spans)


def test_spans_are_ordered_and_within_bounds():
    audio = np.concatenate([_silence(1.0), _tone(3.0), _silence(2.0), _tone(3.0)])
    total = len(audio) / SAMPLE_RATE
    spans, _ = detect_speech(audio)
    assert all(0 <= s.start < s.end <= total + 0.01 for s in spans)
    assert spans == sorted(spans, key=lambda s: s.start)


def test_speech_ratio_is_bounded():
    assert speech_ratio([Span(0, 5), Span(4, 12)], 10.0) == 1.0
