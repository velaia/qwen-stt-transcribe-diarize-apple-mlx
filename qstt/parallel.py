"""Process-level sharding for long recordings.

MLX has no batched-decode path here (``transcribe_batch`` upstream is a loop
that merely reuses loaded weights), so the only real parallelism available is
running several worker processes over disjoint time ranges of the same file.

Measured on an M1 Max (64 GB) over 10 minutes of podcast audio with the 1.7B
model, it barely helps:

    workers   wall     speedup
          1   44.8s      1.00x
          2   39.4s      1.14x
          4   41.2s      1.09x
          6   46.3s      0.97x

Autoregressive decoding is memory-bandwidth bound and each worker holds its own
copy of the weights, so they compete for the same unified memory bus; past two
workers the contention outweighs the parallelism. Sharding stays opt-in
(``--workers``, default 1) for the cases where it does pay -- different
hardware, or recordings with enough silence that VAD and encoder work dominate.
Run ``bench.py`` on your own audio before relying on it.
"""

from __future__ import annotations

import math
import os
from concurrent.futures import ProcessPoolExecutor
from dataclasses import replace
from pathlib import Path

from .pipeline import Options, Result, Segment, Transcriber

# Weights per worker at fp16: ~3.4 GB for 1.7B, ~1.2 GB for 0.6B.
_MODEL_GB = {"1.7B": 3.6, "0.6B": 1.4}


def suggest_workers(model: str, duration: float) -> int:
    """A conservative worker count for this machine and model size."""
    try:
        total_gb = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 1e9
    except (ValueError, OSError):
        total_gb = 16.0
    per = _MODEL_GB["0.6B" if "0.6B" in model else "1.7B"]
    by_memory = max(1, int((total_gb * 0.5) // per))
    by_length = max(1, int(duration // 300))  # <5 min of audio: not worth it
    return max(1, min(by_memory, by_length, os.cpu_count() or 4, 6))


def _shard(args) -> Result:
    """Worker entry point: transcribe one time range of one file."""
    path, start, end, opts = args
    import numpy as np

    from .audio import SAMPLE_RATE, load_audio

    audio = load_audio(path)
    a, b = int(start * SAMPLE_RATE), int(end * SAMPLE_RATE)
    shard = np.ascontiguousarray(audio[a:b])

    engine = Transcriber(replace(opts, verbose=False))
    result = engine.run_array(shard, source=str(path))

    # Shift every timestamp back into the original file's timeline.
    for seg in result.segments:
        seg.start += start
        seg.end += start
        for w in seg.words:
            w["start"] += start
            w["end"] += start
    for rej in result.rejections:
        rej.start += start
        rej.end += start
    return result


def run_sharded(
    path: str | Path, opts: Options, workers: int, duration: float
) -> Result:
    """Transcribe `path` using `workers` processes over contiguous time shards.

    Shard boundaries are placed on plain time offsets; each worker runs its own
    VAD, so a boundary that lands mid-utterance costs at most one span, not a
    lost word.
    """
    workers = max(1, workers)
    if workers == 1:
        return Transcriber(opts).run(path)

    step = math.ceil(duration / workers)
    ranges = [(i * step, min(duration, (i + 1) * step)) for i in range(workers)]
    ranges = [r for r in ranges if r[1] - r[0] > 1.0]

    jobs = [(str(path), s, e, opts) for s, e in ranges]
    with ProcessPoolExecutor(max_workers=len(jobs)) as pool:
        parts = list(pool.map(_shard, jobs))

    return _merge(parts, str(path), duration)


def _merge(parts: list[Result], source: str, duration: float) -> Result:
    segments: list[Segment] = []
    rejections = []
    reasons: dict[str, int] = {}
    kept_spans = 0
    languages = []

    for part in parts:
        segments.extend(part.segments)
        rejections.extend(part.rejections)
        kept_spans += part.stats.get("spans", 0)
        languages.append(part.language)
        for k, v in (part.stats.get("reject_reasons") or {}).items():
            reasons[k] = reasons.get(k, 0) + v

    segments.sort(key=lambda s: s.start)
    rejections.sort(key=lambda r: r.start)

    # Majority language across shards keeps one noisy shard from relabelling
    # the whole recording.
    language = max(set(languages), key=languages.count) if languages else "English"

    stats = {
        "vad": parts[0].stats.get("vad") if parts else "n/a",
        "spans": kept_spans,
        "kept": len(segments),
        "rejected": len(rejections),
        "reject_reasons": reasons,
        "language_decision": "sharded-majority",
        "workers": len(parts),
    }
    return Result(source, duration, language, segments, rejections, [], stats)
