#!/usr/bin/env python3
"""Measure whether sharding across processes actually speeds this up.

Decoding is memory-bandwidth bound and every worker loads its own copy of the
weights, so more workers is not automatically faster. This runs the same audio
at several worker counts and prints the real numbers for this machine.

    ./bench.py audio.mp3 --workers 1,2,4 --seconds 600
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
import time
from pathlib import Path


def excerpt(src: Path, seconds: int, start: int = 0) -> Path:
    out = Path(tempfile.gettempdir()) / f"bench_{seconds}s_{start}.wav"
    if not out.exists():
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-ss", str(start), "-t",
             str(seconds), "-i", str(src), "-ar", "16000", "-ac", "1", str(out)],
            check=True,
        )
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("audio", type=Path)
    ap.add_argument("--workers", default="1,2,4",
                    help="comma-separated worker counts to compare")
    ap.add_argument("--seconds", type=int, default=600,
                    help="length of the excerpt to benchmark (default: 600)")
    ap.add_argument("--start", type=int, default=300)
    ap.add_argument("--model", default="Qwen/Qwen3-ASR-1.7B")
    ap.add_argument("--timestamps", action="store_true",
                    help="include forced alignment in the measurement")
    args = ap.parse_args()

    from qstt.audio import duration_sec, load_audio
    from qstt.parallel import run_sharded
    from qstt.pipeline import Options

    clip = excerpt(args.audio, args.seconds, args.start)
    dur = duration_sec(load_audio(clip))
    opts = Options(model=args.model, language="English",
                   timestamps=args.timestamps, verbose=False)

    counts = [int(x) for x in args.workers.split(",")]
    print(f"\naudio: {clip.name}  ({dur:.0f}s)   model: {args.model}"
          f"   timestamps: {args.timestamps}\n")
    print(f"{'workers':>8} {'wall (s)':>10} {'x realtime':>12} {'speedup':>9} "
          f"{'segments':>9}")
    print("-" * 54)

    baseline = None
    for n in counts:
        t0 = time.time()
        res = run_sharded(clip, opts, n, dur)
        wall = time.time() - t0
        baseline = baseline or wall
        print(f"{n:>8} {wall:>10.1f} {dur / wall:>12.1f} "
              f"{baseline / wall:>8.2f}x {len(res.segments):>9}")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
