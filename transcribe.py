#!/usr/bin/env python3
"""Transcribe English/German audio with Qwen3-ASR, with optional speaker labels.

    ./transcribe.py meeting.m4a --diarize
    ./transcribe.py interview.wav --lang de -f md,srt -o out/
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from qstt import lang as langmod
from qstt import writers
from qstt.parallel import run_sharded, suggest_workers
from qstt.pipeline import DEFAULT_MODEL, Options, Transcriber

AUDIO_EXT = {".wav", ".mp3", ".m4a", ".mp4", ".aac", ".flac", ".ogg", ".opus",
             ".wma", ".aiff", ".aif", ".webm", ".mov", ".mkv", ".m4b", ".caf"}


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="transcribe.py",
        description="Qwen3-ASR transcription locked to English/German, with diarization.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""examples:
  ./transcribe.py meeting.m4a --diarize --num-speakers 3
  ./transcribe.py *.wav --lang de -f md,srt -o transcripts/
  ./transcribe.py call.mp3 --mixed          # speakers switch between EN and DE
  ./transcribe.py noisy.wav --aggressive-filler --vad-threshold 0.6
""",
    )
    p.add_argument("inputs", nargs="*", help="audio/video files or directories")
    p.add_argument("--doctor", action="store_true",
                   help="check the environment and exit")

    g = p.add_argument_group("language")
    g.add_argument("--lang", "-l", default="auto", metavar="{auto,en,de}",
                   help="force a language, or auto-detect between English and German "
                        "(default: auto)")
    g.add_argument("--mixed", action="store_true",
                   help="decide language per speech span, for recordings that switch "
                        "between English and German")
    g.add_argument("--fallback-lang", default="en", metavar="{en,de}",
                   help="language to use when detection is inconclusive (default: en)")

    g = p.add_argument_group("model")
    g.add_argument("--model", "-m", default=DEFAULT_MODEL,
                   help=f"HF id or local path (default: {DEFAULT_MODEL})")
    g.add_argument("--fast", action="store_true",
                   help="use the 0.6B model: ~3x faster, slightly higher error rate")
    g.add_argument("--context", default="",
                   help="space-separated domain vocabulary to bias decoding, "
                        'e.g. --context "Kubernetes Grafana Vattenfall"')

    g = p.add_argument_group("speakers")
    g.add_argument("--diarize", "-d", action="store_true",
                   help="label who spoke when (requires pyannote setup, see README)")
    g.add_argument("--num-speakers", type=int, default=None,
                   help="exact speaker count, when you know it")
    g.add_argument("--min-speakers", type=int, default=1)
    g.add_argument("--max-speakers", type=int, default=8)
    g.add_argument("--diarize-device", default="auto",
                   choices=["auto", "cpu", "mps", "cuda"])
    g.add_argument("--smooth-max-words", type=int, default=3, metavar="N",
                   help="runs of N words or fewer that interrupt one continuing "
                        "speaker are treated as boundary jitter (default: 3)")
    g.add_argument("--smooth-max-dur", type=float, default=1.2, metavar="SEC",
                   help="longest such run treated as jitter (default: 1.2)")
    g.add_argument("--smooth-max-gap", type=float, default=2.0, metavar="SEC",
                   help="largest silence still merged inside one turn (default: 2.0)")

    g = p.add_argument_group("filtering")
    g.add_argument("--no-vad", action="store_true",
                   help="disable voice activity detection (not recommended: silence "
                        "is the main cause of invented speech)")
    g.add_argument("--vad-threshold", type=float, default=0.5, metavar="0..1",
                   help="raise toward 0.8 for noisy recordings (default: 0.5)")
    g.add_argument("--min-speech", type=float, default=0.35, metavar="SEC",
                   help="ignore speech bursts shorter than this (default: 0.35)")
    g.add_argument("--aggressive-filler", action="store_true",
                   help='also drop bare interjections ("Ja.", "Okay.") -- these are '
                        "sometimes genuine turns, so this is off by default")
    g.add_argument("--max-repeats", type=int, default=4,
                   help="repetitions before a segment counts as a decoding loop")
    g.add_argument("--allow-foreign-ratio", type=float, default=0.15, metavar="0..1",
                   help="share of non-Latin characters a segment may contain before "
                        "it is discarded as another language (default: 0.15). Stray "
                        "characters below the threshold are stripped, so no "
                        "Chinese/Cyrillic/Arabic text reaches the output either way. "
                        "Use 0 to discard a segment over a single character")
    g.add_argument("--no-rejects", action="store_true",
                   help="omit the filtered-segment report from md/json output")

    g = p.add_argument_group("output")
    g.add_argument("-f", "--format", default="md",
                   help="comma-separated: txt,md,srt,vtt,json or all (default: md)")
    g.add_argument("-o", "--outdir", type=Path, default=None,
                   help="write files here (default: print to stdout)")
    g.add_argument("--no-timestamps", action="store_true",
                   help="skip forced alignment; faster, but no srt/vtt and coarser "
                        "speaker boundaries")
    g.add_argument("-w", "--workers", default="1", metavar="N",
                   help="split each file into N time shards and transcribe them in "
                        "parallel processes; 'auto' picks a count for this machine. "
                        "Each worker loads its own copy of the weights (default: 1)")
    g.add_argument("-q", "--quiet", action="store_true")
    g.add_argument("-v", "--verbose", action="store_true")
    return p


def collect(inputs: list[str]) -> list[Path]:
    files: list[Path] = []
    for item in inputs:
        path = Path(item)
        if path.is_dir():
            files += sorted(
                p for p in path.rglob("*") if p.suffix.lower() in AUDIO_EXT
            )
        elif path.is_file():
            files.append(path)
        else:
            print(f"warning: no such file: {item}", file=sys.stderr)
    return files


def resolve_formats(spec: str) -> list[str]:
    if spec.strip() == "all":
        return list(writers.WRITERS)
    out = []
    for f in spec.split(","):
        f = f.strip().lower()
        if f not in writers.WRITERS:
            raise SystemExit(
                f"unknown format {f!r}; choose from {', '.join(writers.WRITERS)} or 'all'"
            )
        out.append(f)
    return out


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.doctor:
        from qstt.doctor import run as doctor

        return doctor()

    if not args.inputs:
        build_parser().print_usage(sys.stderr)
        print("error: no input files given (use --doctor to check setup)", file=sys.stderr)
        return 2

    files = collect(args.inputs)
    if not files:
        print("error: no audio files found", file=sys.stderr)
        return 1

    formats = resolve_formats(args.format)

    if args.lang.lower() in ("auto", ""):
        forced = None
    else:
        forced = langmod.normalize(args.lang)
        if forced is None:
            print(
                f"error: --lang must be auto, en or de (got {args.lang!r}). "
                "This tool is deliberately restricted to English and German.",
                file=sys.stderr,
            )
            return 2

    fallback = langmod.normalize(args.fallback_lang) or langmod.ENGLISH
    needs_ts = not args.no_timestamps or bool({"srt", "vtt"} & set(formats))

    opts = Options(
        model="Qwen/Qwen3-ASR-0.6B" if args.fast else args.model,
        language=forced,
        mixed=args.mixed,
        fallback_language=fallback,
        timestamps=needs_ts,
        diarize=args.diarize,
        num_speakers=args.num_speakers,
        min_speakers=args.min_speakers,
        max_speakers=args.max_speakers,
        diarize_device=args.diarize_device,
        smooth_max_words=args.smooth_max_words,
        smooth_max_dur=args.smooth_max_dur,
        smooth_max_gap=args.smooth_max_gap,
        context=args.context,
        aggressive_filler=args.aggressive_filler,
        allow_foreign_ratio=args.allow_foreign_ratio,
        max_repeats=args.max_repeats,
        vad=not args.no_vad,
        vad_threshold=args.vad_threshold,
        min_speech_sec=args.min_speech,
        keep_rejected=not args.no_rejects,
        verbose=args.verbose,
    )

    sharded = str(args.workers).lower() != "1"
    engine = None
    if not sharded:
        try:
            engine = Transcriber(opts)
        except Exception as exc:
            print(f"error: could not load model: {exc}", file=sys.stderr)
            return 3

    if sharded and args.diarize:
        print(
            "error: --diarize needs the whole recording at once and cannot be "
            "combined with --workers. Run them as separate passes.",
            file=sys.stderr,
        )
        return 2

    failures = 0
    for path in files:
        t0 = time.time()
        try:
            if sharded:
                from qstt.audio import duration_sec, load_audio

                dur = duration_sec(load_audio(path))
                n = (
                    suggest_workers(opts.model, dur)
                    if str(args.workers).lower() == "auto"
                    else int(args.workers)
                )
                if not args.quiet:
                    print(f"{path.name}: {dur:.0f}s across {n} worker(s)")
                result = run_sharded(path, opts, n, dur)
            else:
                result = engine.run(path)
        except Exception as exc:
            print(f"error: {path.name}: {exc}", file=sys.stderr)
            failures += 1
            continue

        elapsed = time.time() - t0
        if args.outdir:
            written = writers.write(result, formats, args.outdir, path.stem)
            if not args.quiet:
                rtf = elapsed / result.duration if result.duration else 0
                print(
                    f"{path.name}  [{result.language}] "
                    f"{len(result.segments)} segments, "
                    f"{len(result.rejections)} filtered, "
                    f"{elapsed:.1f}s ({rtf:.2f}x realtime)"
                )
                for w in written:
                    print(f"  -> {w}")
        else:
            if len(files) > 1 and not args.quiet:
                print(f"\n===== {path.name} =====")
            sys.stdout.write(writers.WRITERS[formats[0]](result))

        if not result.segments and not args.quiet:
            print(
                f"note: {path.name} produced no usable speech "
                f"({result.stats.get('reject_reasons') or 'no speech detected'})",
                file=sys.stderr,
            )

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
