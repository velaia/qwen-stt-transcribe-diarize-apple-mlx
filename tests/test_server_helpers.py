"""Shared test scaffolding for the server/ test suite: no real model is ever loaded.

Not a test module itself (no test_* functions), just shared helpers -- named to
match the tests/test_server_*.py convention so it lives next to what uses it.
"""

from __future__ import annotations

import time
import wave
from collections.abc import Callable
from pathlib import Path

import numpy as np

from qstt.pipeline import Options, Result, Segment
from server.app import create_app


def make_wav(path: Path, seconds: float = 1.0, freq: float = 440.0,
             silent: bool = False, sr: int = 16000) -> Path:
    """Write a tiny mono 16-bit PCM WAV: a sine tone, or true silence."""
    n = max(1, int(seconds * sr))
    if silent:
        data = np.zeros(n, dtype=np.int16)
    else:
        t = np.arange(n) / sr
        data = (0.2 * np.sin(2 * np.pi * freq * t) * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(data.tobytes())
    return path


def _read_wav_samples(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as w:
        n = w.getnframes()
        raw = w.readframes(n)
        sr = w.getframerate()
    arr = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    return arr, sr


class FakeEngine:
    """Stand-in for qstt.pipeline.Transcriber: loads no model, reads the real
    WAV bytes the test wrote and fabricates a plausible Result from `self.opts`.
    """

    def __init__(self, opts: Options):
        self.opts = opts
        self.loaded_for = opts.model

    def run(self, path, progress: Callable | None = None) -> Result:
        report = progress or (lambda *a: None)
        opts = self.opts

        report("decode", None)
        audio, sr = _read_wav_samples(path)
        duration = len(audio) / sr if sr else 0.0

        report("vad", None)
        is_silence = len(audio) == 0 or float(np.abs(audio).max()) < 1e-4
        if is_silence:
            report("done", 1.0)
            return Result(
                str(path), duration, opts.fallback_language, [], [], [],
                {"vad": "fake", "speech_ratio": 0.0, "note": "no speech detected"},
            )

        report("language", None)
        segments: list[Segment] = []

        if opts.diarize:
            report("diarize", None)
            # A fixed jitter pattern -- two one-word interjections from a
            # second speaker, each surrounded by the first speaker -- so
            # smoothing with different parameters visibly changes the turn
            # count in server/results.py: default settings absorb both
            # interjections but stop merging across the >2s gap (3 turns);
            # aggressive settings (bigger max_gap) merge across it too (2
            # turns); zero settings absorb nothing (6 turns).
            raw = [
                (0.0, 1.0, "SPEAKER_00", "But nonetheless if I had"),
                (1.0, 1.2, "SPEAKER_01", "to"),
                (1.2, 2.4, "SPEAKER_00", "pick a single best model today"),
                (5.0, 5.2, "SPEAKER_01", "well"),
                (5.2, 6.4, "SPEAKER_00", "for this particular use case"),
                (8.0, 9.0, "SPEAKER_01", "I disagree with that entirely"),
            ]
            for i, (start, end, spk, text) in enumerate(raw):
                report("transcribe", i / len(raw))
                segments.append(Segment(start, end, text, spk, "English", []))
        elif opts.mixed:
            report("transcribe", 0.0)
            segments.append(Segment(0.0, duration / 2, "This is English.", None, "English", []))
            report("transcribe", 0.5)
            segments.append(Segment(duration / 2, duration, "Das ist Deutsch.", None, "German", []))
        else:
            report("transcribe", 0.0)
            lang = opts.language or "English"
            text = "Grüße aus Berlin." if lang == "German" else "Hello from the fake engine."
            segments.append(Segment(0.0, duration, text, None, lang, []))

        report("finalize", None)
        speakers = sorted({s.speaker for s in segments if s.speaker})
        stats = {
            "vad": "fake", "speech_ratio": 1.0, "spans": len(segments),
            "kept": len(segments), "rejected": 0, "reject_reasons": {},
            "language_decision": "forced",
        }
        report("done", 1.0)
        language = segments[0].language if segments else opts.fallback_language
        return Result(str(path), duration, language, segments, [], speakers, stats)


def build_test_app(data_dir, engine_factory=None):
    return create_app(data_dir=data_dir, engine_factory=engine_factory or FakeEngine)


def wait_until(predicate: Callable[[], bool], timeout: float = 5.0, interval: float = 0.02) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


def upload(client, path: Path, *, title: str | None = None, options: dict | None = None):
    import json as _json

    data = {}
    if title is not None:
        data["title"] = title
    if options is not None:
        data["options"] = _json.dumps(options)
    with open(path, "rb") as f:
        files = {"file": (path.name, f, "audio/wav")}
        return client.post("/api/transcripts", data=data, files=files)


def wait_for_status(client, item_id: str, statuses=("done", "error", "cancelled"),
                     timeout: float = 10.0):
    result = {}

    def check():
        r = client.get(f"/api/transcripts/{item_id}")
        result["resp"] = r
        return r.status_code == 200 and r.json()["status"] in statuses

    wait_until(check, timeout=timeout)
    return result["resp"]
