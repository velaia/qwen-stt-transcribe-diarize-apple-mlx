#!/usr/bin/env python3
"""Even out speaker loudness: a loud host and a quiet guest end up equally loud.

Without a transcript the running speech level is tracked automatically. With a
diarized JSON transcript from transcribe.py each speaker gets one gain, which is
the more precise of the two.

    ./level.py episode.mp3                              # -> episode.leveled.mp3
    ./level.py episode.mp3 -t transcripts/episode.json  # per-speaker gains
    ./level.py episode.mp3 -o out.m4a --target -19      # Apple Podcasts level
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from qstt.level import level_file


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("audio", type=Path, help="any ffmpeg-readable audio or video file")
    ap.add_argument("-o", "--output", type=Path, default=None,
                    help="default: <input>.leveled<ext> next to the input")
    ap.add_argument("-t", "--transcript", type=Path, default=None,
                    help="JSON transcript with speaker labels (transcribe.py -f json)")
    ap.add_argument("--target", type=float, default=-16.0,
                    help="overall loudness in LUFS (default -16, the usual podcast level)")
    ap.add_argument("--max-boost", type=float, default=18.0,
                    help="most dB a quiet passage is raised (default 18)")
    ap.add_argument("--max-cut", type=float, default=18.0,
                    help="most dB a loud passage is lowered (default 18)")
    ap.add_argument("--turn-trim", type=float, default=4.0,
                    help="transcript mode: extra dB a single turn may move toward the "
                         "common level (0 = one fixed gain per speaker)")
    ap.add_argument("--smooth", type=float, default=0.5,
                    help="seconds over which gain changes are faded (default 0.5)")
    ap.add_argument("-b", "--bitrate", default=None,
                    help="encoder bitrate, e.g. 128k (default: ffmpeg's for the format)")
    args = ap.parse_args(argv)

    if not args.audio.exists():
        print(f"error: no such file: {args.audio}", file=sys.stderr)
        return 1
    out = args.output or args.audio.with_name(f"{args.audio.stem}.leveled{args.audio.suffix}")

    report = level_file(
        args.audio, out, args.transcript, bitrate=args.bitrate,
        target_lufs=args.target, max_boost=args.max_boost, max_cut=args.max_cut,
        turn_trim=args.turn_trim, smooth_sec=args.smooth,
    )

    print(f"mode: {report.mode}")
    for spk, g in report.speaker_gains_db.items():
        print(f"  {spk}: speaks at {report.speaker_levels_db[spk]:.1f} LUFS, gain {g:+.1f} dB")
    lo, hi = report.gain_range_db
    print(f"gain applied: {lo:+.1f} to {hi:+.1f} dB (incl. {report.offset_db:+.1f} dB to target)")
    if report.output_lufs is not None:
        print(f"output loudness: {report.output_lufs:.1f} LUFS")
    print(f"  -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
