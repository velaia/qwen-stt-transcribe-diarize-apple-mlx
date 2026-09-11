#!/usr/bin/env python3
"""Re-apply speaker smoothing to an existing JSON transcript.

Diarization jitter is a post-processing problem, so retuning it does not need
another transcription pass -- a 2.5-hour recording is reprocessed in under a
second.

    ./resmooth.py transcripts_diarized/episode.json -f md,srt
    ./resmooth.py episode.json --max-words 5 --max-gap 3.0   # more aggressive
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from qstt import writers
from qstt.smooth import smooth


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("json_file", type=Path, help="a JSON transcript from transcribe.py")
    ap.add_argument("-f", "--format", default="md,srt,json")
    ap.add_argument("-o", "--outdir", type=Path, default=None,
                    help="default: alongside the input file")
    ap.add_argument("--max-words", type=int, default=3,
                    help="longest run treated as jitter rather than a real turn")
    ap.add_argument("--max-dur", type=float, default=1.2,
                    help="longest run in seconds treated as jitter")
    ap.add_argument("--max-gap", type=float, default=2.0,
                    help="largest silence still merged within one speaker's turn")
    args = ap.parse_args(argv)

    if not args.json_file.exists():
        print(f"error: no such file: {args.json_file}", file=sys.stderr)
        return 1

    result = writers.load_result(args.json_file)
    before = len(result.segments)
    result.segments = smooth(
        result.segments,
        max_words=args.max_words,
        max_dur=args.max_dur,
        max_gap=args.max_gap,
    )
    result.speakers = sorted({s.speaker for s in result.segments if s.speaker})
    result.stats = dict(result.stats) | {
        "turns_before_smoothing": before,
        "turns_after_smoothing": len(result.segments),
    }

    formats = list(writers.WRITERS) if args.format == "all" else [
        f.strip() for f in args.format.split(",")
    ]
    outdir = args.outdir or args.json_file.parent
    written = writers.write(result, formats, outdir, args.json_file.stem)

    print(f"{before} -> {len(result.segments)} turns "
          f"({100 * (1 - len(result.segments) / max(1, before)):.0f}% fewer fragments)")
    for w in written:
        print(f"  -> {w}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
