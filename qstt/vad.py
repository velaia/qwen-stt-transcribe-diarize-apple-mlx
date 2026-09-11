"""Voice activity detection.

This is the first line of defence against the classic ASR failure mode: feed a
model silence, breathing or room tone and it will confidently invent speech --
often in a language that never appears in the recording. We never hand
non-speech audio to the ASR model at all.

Silero is used when available; a spectral-flatness/energy detector is the
fallback so the pipeline still runs offline without it.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .audio import SAMPLE_RATE, duration_sec


@dataclass(frozen=True)
class Span:
    start: float
    end: float

    @property
    def dur(self) -> float:
        return self.end - self.start


def _merge(spans: list[Span], gap: float, pad: float, total: float) -> list[Span]:
    """Pad each span, then merge any that now overlap or sit closer than `gap`."""
    if not spans:
        return []
    spans = sorted(spans, key=lambda s: s.start)
    out = [Span(max(0.0, spans[0].start - pad), min(total, spans[0].end + pad))]
    for s in spans[1:]:
        lo, hi = max(0.0, s.start - pad), min(total, s.end + pad)
        if lo - out[-1].end <= gap:
            out[-1] = Span(out[-1].start, max(out[-1].end, hi))
        else:
            out.append(Span(lo, hi))
    return out


def _quietest_near(audio: np.ndarray, target: float, window: float) -> float:
    """Find the lowest-energy instant within +/- `window` of `target`.

    Splitting a long span at a fixed offset can land in the middle of a word,
    which the decoder then reports as two fragments ("To" + "day"). Snapping the
    cut to the quietest nearby point puts it in a pause instead.
    """
    lo = max(0, int((target - window) * SAMPLE_RATE))
    hi = min(len(audio), int((target + window) * SAMPLE_RATE))
    if hi - lo < SAMPLE_RATE // 10:
        return target
    seg = audio[lo:hi]
    frame = SAMPLE_RATE // 50  # 20 ms
    n = len(seg) // frame
    if n < 3:
        return target
    energy = (seg[: n * frame].reshape(n, frame) ** 2).mean(axis=1)
    # Smooth so we pick a genuine pause, not a single quiet sample.
    k = min(5, n)
    smooth = np.convolve(energy, np.ones(k) / k, mode="same")
    return (lo + int(smooth.argmin()) * frame) / SAMPLE_RATE


def _split_long(
    spans: list[Span], max_len: float, audio: np.ndarray | None = None
) -> list[Span]:
    """Cap span length so each ASR call stays inside the model's comfort zone."""
    out: list[Span] = []
    for s in spans:
        if s.dur <= max_len:
            out.append(s)
            continue
        n = int(np.ceil(s.dur / max_len))
        step = s.dur / n
        cuts = [s.start]
        for i in range(1, n):
            target = s.start + i * step
            if audio is not None:
                target = _quietest_near(audio, target, window=min(1.5, step / 4))
            cuts.append(max(cuts[-1] + 0.5, min(target, s.end - 0.5)))
        cuts.append(s.end)
        out.extend(Span(cuts[i], cuts[i + 1]) for i in range(len(cuts) - 1))
    return out


def _silero(audio: np.ndarray, threshold: float, min_speech: float) -> list[Span] | None:
    try:
        import torch
        from silero_vad import get_speech_timestamps, load_silero_vad
    except Exception:
        return None
    try:
        model = load_silero_vad()
        ts = get_speech_timestamps(
            torch.from_numpy(audio),
            model,
            sampling_rate=SAMPLE_RATE,
            threshold=threshold,
            min_speech_duration_ms=int(min_speech * 1000),
            min_silence_duration_ms=300,
            return_seconds=True,
        )
    except Exception:
        return None
    return [Span(float(t["start"]), float(t["end"])) for t in ts]


def _energy(audio: np.ndarray, min_speech: float) -> list[Span]:
    """Fallback VAD: frame energy above a noise-floor-relative threshold.

    The floor is estimated from the 10th percentile of frame energy, so it
    adapts to the recording rather than to an absolute dBFS value.
    """
    win, hop = 512, 256
    if len(audio) < win:
        return []
    n = 1 + (len(audio) - win) // hop
    frames = np.lib.stride_tricks.as_strided(
        audio, shape=(n, win), strides=(audio.strides[0] * hop, audio.strides[0])
    )
    rms = np.sqrt((frames.astype(np.float64) ** 2).mean(axis=1) + 1e-12)
    db = 20 * np.log10(rms + 1e-12)
    floor = np.percentile(db, 10)
    active = db > (floor + 8.0)

    spans, run = [], None
    for i, a in enumerate(active):
        t = i * hop / SAMPLE_RATE
        if a and run is None:
            run = t
        elif not a and run is not None:
            if t - run >= min_speech:
                spans.append(Span(run, t))
            run = None
    if run is not None:
        end = len(audio) / SAMPLE_RATE
        if end - run >= min_speech:
            spans.append(Span(run, end))
    return spans


def detect_speech(
    audio: np.ndarray,
    *,
    threshold: float = 0.5,
    min_speech_sec: float = 0.35,
    merge_gap_sec: float = 0.6,
    pad_sec: float = 0.2,
    max_span_sec: float = 28.0,
) -> tuple[list[Span], str]:
    """Return (speech spans, backend name). Empty list means: no speech found."""
    total = duration_sec(audio)
    spans = _silero(audio, threshold, min_speech_sec)
    backend = "silero"
    if spans is None:
        spans = _energy(audio, min_speech_sec)
        backend = "energy"

    spans = [s for s in spans if s.dur >= min_speech_sec]
    spans = _merge(spans, merge_gap_sec, pad_sec, total)
    return _split_long(spans, max_span_sec, audio), backend


def speech_ratio(spans: list[Span], total: float) -> float:
    if total <= 0:
        return 0.0
    return min(1.0, sum(s.dur for s in spans) / total)
