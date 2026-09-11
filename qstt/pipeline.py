"""Transcription pipeline: VAD -> language lock -> ASR -> screening -> speakers.

The ordering matters. VAD runs first so the ASR model never sees silence; the
language is locked before decoding so language identification can never pick a
third language; screening runs last to catch whatever still slipped through.
"""

from __future__ import annotations

import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from . import filters
from . import lang as langmod
from .audio import SAMPLE_RATE, duration_sec, load_audio, slice_audio
from .vad import Span, detect_speech, speech_ratio

DEFAULT_MODEL = "Qwen/Qwen3-ASR-1.7B"

# progress(stage, fraction) -- see Transcriber.run_array.
Progress = Callable[[str, "float | None"], None]


def _no_progress(stage: str, fraction: float | None) -> None:
    pass


@dataclass
class Segment:
    start: float
    end: float
    text: str
    speaker: str | None = None
    language: str = ""
    words: list[dict] = field(default_factory=list)


@dataclass
class Rejection:
    start: float
    end: float
    text: str
    reason: str


@dataclass
class Result:
    source: str
    duration: float
    language: str
    segments: list[Segment]
    rejections: list[Rejection]
    speakers: list[str]
    stats: dict

    @property
    def text(self) -> str:
        return "\n".join(s.text for s in self.segments if s.text)


@dataclass
class Options:
    model: str = DEFAULT_MODEL
    language: str | None = None          # None -> auto-detect within {en, de}
    mixed: bool = False                  # decide language per speech span
    fallback_language: str = langmod.ENGLISH
    timestamps: bool = True
    diarize: bool = False
    num_speakers: int | None = None
    min_speakers: int = 1
    max_speakers: int = 8
    diarize_device: str = "auto"
    # Diarization boundaries jitter; these bound what counts as noise rather
    # than a genuine short turn. See qstt/smooth.py.
    smooth_max_words: int = 3
    smooth_max_dur: float = 1.2
    smooth_max_gap: float = 2.0
    # Off leaves speaker-attributed but unmerged segments, for callers that
    # store them and smooth later: merging cannot be undone, re-smoothing can.
    smooth: bool = True
    context: str = ""
    aggressive_filler: bool = False
    # Below this share of foreign characters the segment is kept and the stray
    # characters stripped; above it the segment is treated as another language
    # and dropped whole. Either way no non-Latin text reaches the output.
    allow_foreign_ratio: float = 0.15
    max_repeats: int = 4
    vad: bool = True
    vad_threshold: float = 0.5
    min_speech_sec: float = 0.35
    max_span_sec: float = 28.0
    keep_rejected: bool = True
    verbose: bool = False


class Transcriber:
    """Holds the loaded model so repeated files do not pay for reloading."""

    def __init__(self, opts: Options):
        from mlx_qwen3_asr import Session

        self.opts = opts
        self._log(f"loading model {opts.model} ...")
        t0 = time.time()
        self.session = Session(model=opts.model)
        self._log(f"model ready in {time.time() - t0:.1f}s")
        self._pyannote = None

    def _log(self, msg: str) -> None:
        if self.opts.verbose:
            print(f"[qstt] {msg}", flush=True)

    # -- ASR -------------------------------------------------------------

    def _asr(self, audio: np.ndarray, language: str | None, timestamps: bool):
        return self.session.transcribe(
            audio,
            language=language,
            context=self.opts.context,
            return_timestamps=timestamps,
        )

    # -- language --------------------------------------------------------

    def _detect_language(self, audio: np.ndarray, spans: list[Span]) -> tuple[str, str]:
        """Decide the file's language, constrained to English or German.

        Returns (language, how) where `how` explains the decision for logging.
        """
        forced = langmod.normalize(self.opts.language)
        if forced:
            return forced, "forced"

        # Probe the longest spans first -- more speech, more reliable evidence.
        probe = sorted(spans, key=lambda s: s.dur, reverse=True)[:4]
        budget, chosen = 30.0, []
        for s in probe:
            if budget <= 0:
                break
            chosen.append(s)
            budget -= s.dur
        if not chosen:
            return self.opts.fallback_language, "no-speech"

        detected = []
        for s in chosen:
            clip = slice_audio(audio, s.start, min(s.end, s.start + 15.0))
            if len(clip) < SAMPLE_RATE // 2:
                continue
            try:
                detected.append(self._asr(clip, None, False).language)
            except Exception as exc:
                self._log(f"probe failed on {s.start:.1f}s: {exc}")
        self._log(f"raw detections: {detected}")

        winner, share = langmod.vote(detected)
        if winner and share >= 0.6:
            return winner, f"vote({share:.0%})"

        # The model landed outside {English, German}, or split evenly. Decode
        # the probe both ways and let the function-word profile decide.
        first = chosen[0]
        clip = slice_audio(audio, first.start, min(first.end, first.start + 15.0))
        try:
            en = self._asr(clip, langmod.ENGLISH, False).text
            de = self._asr(clip, langmod.GERMAN, False).text
            pick, conf = langmod.choose_by_lexicon(en, de)
            return pick, f"lexicon({conf:.0%})"
        except Exception as exc:
            self._log(f"lexical tie-break failed: {exc}")
        return winner or self.opts.fallback_language, "fallback"

    # -- diarization -----------------------------------------------------

    def _speaker_turns(self, audio: np.ndarray) -> list[dict]:
        from mlx_qwen3_asr.diarization import (
            infer_speaker_turns,
            validate_diarization_config,
        )

        cfg = validate_diarization_config(
            num_speakers=self.opts.num_speakers,
            min_speakers=self.opts.min_speakers,
            max_speakers=self.opts.max_speakers,
            device=self.opts.diarize_device,
        )
        t0 = time.time()
        turns = infer_speaker_turns(audio, sr=SAMPLE_RATE, config=cfg)
        self._log(f"diarization: {len(turns)} turns in {time.time() - t0:.1f}s")
        return turns

    # -- main ------------------------------------------------------------

    def run(self, path: str | Path, progress: Progress | None = None) -> Result:
        """Transcribe a file from disk."""
        (progress or _no_progress)("decode", None)
        audio = load_audio(path)
        self._log(f"{Path(path).name}: {duration_sec(audio):.1f}s")
        return self.run_array(audio, source=str(path), progress=progress)

    def run_array(
        self,
        audio: np.ndarray,
        *,
        source: str = "<array>",
        progress: Progress | None = None,
    ) -> Result:
        """Transcribe already-decoded mono 16 kHz audio.

        `progress(stage, fraction)` is called as work advances, with fraction
        None where a stage cannot measure itself. It may raise to abandon the
        run -- that is how callers cancel, since it is called between spans.
        """
        opts = self.opts
        total = duration_sec(audio)
        report = progress or _no_progress

        report("vad", None)
        if opts.vad:
            spans, backend = detect_speech(
                audio,
                threshold=opts.vad_threshold,
                min_speech_sec=opts.min_speech_sec,
                max_span_sec=opts.max_span_sec,
            )
            self._log(
                f"vad({backend}): {len(spans)} spans, "
                f"{speech_ratio(spans, total):.0%} of audio is speech"
            )
        else:
            spans = [
                Span(t, min(total, t + opts.max_span_sec))
                for t in np.arange(0, total, opts.max_span_sec)
            ]
            backend = "off"

        if not spans:
            report("done", 1.0)
            return Result(
                source, total, opts.fallback_language, [], [], [],
                {"vad": backend, "speech_ratio": 0.0, "note": "no speech detected"},
            )

        report("language", None)
        file_lang, how = self._detect_language(audio, spans)
        self._log(f"language: {file_lang} ({how})")

        turns: list[dict] = []
        if opts.diarize:
            report("diarize", None)
            turns = self._speaker_turns(audio)

        segments: list[Segment] = []
        rejections: list[Rejection] = []
        reasons: Counter[str] = Counter()
        want_ts = opts.timestamps or opts.diarize

        for i, span in enumerate(spans, 1):
            report("transcribe", (i - 1) / len(spans))
            clip = slice_audio(audio, span.start, span.end)
            if len(clip) < SAMPLE_RATE // 4:
                continue

            span_lang = file_lang
            try:
                if opts.mixed and not langmod.normalize(opts.language):
                    res, span_lang = self._decode_mixed(clip, file_lang, want_ts)
                else:
                    res = self._asr(clip, span_lang, want_ts)
            except Exception as exc:
                self._log(f"span {i} failed: {exc}")
                reasons["asr-error"] += 1
                continue

            # Screen the raw decode: stripping first would erase the very
            # characters the foreign-script guard looks for.
            raw = (res.text or "").strip()
            reason = filters.screen(
                raw,
                allow_foreign_ratio=opts.allow_foreign_ratio,
                max_repeats=opts.max_repeats,
                aggressive_filler=opts.aggressive_filler,
            )
            # `finish_reason == "repetition"` means the decoder stopped early,
            # not that what it produced is junk -- the text-level repetition
            # guard above already rejects genuine loops. Treating the stop
            # reason as fatal discarded real speech, so it is only recorded.
            truncated = getattr(res, "finish_reason", None) == "repetition" or getattr(
                res, "truncated", False
            )
            if truncated:
                reasons["truncated-kept"] += 1

            text = filters.strip_disallowed(raw)
            if reason:
                reasons[reason.split(":")[0]] += 1
                if opts.keep_rejected:
                    rejections.append(
                        Rejection(span.start, span.end, (res.text or "").strip(), reason)
                    )
                self._log(f"span {i} rejected ({reason}): {(res.text or '')[:60]!r}")
                continue

            words = []
            for w in getattr(res, "segments", None) or []:
                words.append(
                    {
                        "text": w.get("text", ""),
                        "start": float(w.get("start", 0.0)) + span.start,
                        "end": float(w.get("end", 0.0)) + span.start,
                    }
                )
            segments.append(
                Segment(span.start, span.end, text, None, span_lang, words)
            )

        if turns:
            segments = self._attach_speakers(segments, turns)
        if turns and opts.smooth:
            from .smooth import smooth

            before = len(segments)
            segments = smooth(
                segments,
                max_words=opts.smooth_max_words,
                max_dur=opts.smooth_max_dur,
                max_gap=opts.smooth_max_gap,
            )
            self._log(f"speaker smoothing: {before} -> {len(segments)} turns")

        speakers = sorted({s.speaker for s in segments if s.speaker})
        stats = {
            "vad": backend,
            "speech_ratio": round(speech_ratio(spans, total), 4),
            "spans": len(spans),
            "kept": len(segments),
            "rejected": len(rejections) + reasons.get("asr-error", 0),
            "reject_reasons": dict(reasons),
            "language_decision": how,
        }
        report("done", 1.0)
        return Result(source, total, file_lang, segments, rejections, speakers, stats)

    def _decode_mixed(self, clip: np.ndarray, default: str, want_ts: bool):
        """Decode one span of a code-switched recording.

        Decoding unforced yields both a transcript and the model's own language
        label. When that label is already English or German the transcript is
        exactly what forcing that language would have produced, so it is kept
        as-is -- only a label outside the pair costs a second decode.
        """
        probe = self._asr(clip, None, want_ts)
        detected = langmod.normalize(probe.language)
        if detected:
            return probe, detected
        self._log(f"span language {probe.language!r} out of set; forcing {default}")
        return self._asr(clip, default, want_ts), default

    def _attach_speakers(
        self, segments: list[Segment], turns: list[dict]
    ) -> list[Segment]:
        """Split segments at speaker boundaries using word timings when present."""
        from mlx_qwen3_asr.diarization import diarize_word_segments

        out: list[Segment] = []
        for seg in segments:
            if not seg.words:
                seg.speaker = _dominant_speaker(seg.start, seg.end, turns)
                out.append(seg)
                continue

            _map_to_source(seg.words, seg.text)
            labeled = diarize_word_segments(seg.words, speaker_turns=turns)
            run: list[dict] = []
            current: str | None = None
            for w in labeled:
                spk = w.get("speaker")
                if current is not None and spk != current and run:
                    out.append(_from_words(run, current, seg.language, seg.text))
                    run = []
                current = spk
                run.append(w)
            if run:
                out.append(_from_words(run, current, seg.language, seg.text))
        return out


def _map_to_source(words: list[dict], source: str) -> None:
    """Annotate each word with its character span in the punctuated transcript.

    Forced-aligner output is bare tokens, so rebuilding a speaker turn by
    joining them drops every comma and full stop. The aligner derives those
    tokens from `source` in order, so a single forward walk recovers where each
    one came from and lets us slice the original text instead.
    """
    pos = 0
    low = source.lower()
    for w in words:
        token = (w.get("text") or "").strip()
        if not token:
            w["_span"] = None
            continue
        idx = low.find(token.lower(), pos)
        if idx < 0:  # aligner and transcript disagree; fall back to the token
            w["_span"] = None
            continue
        end = idx + len(token)
        # Absorb punctuation that belongs to this word.
        while end < len(source) and source[end] in ".,!?;:)]}\u2019\"'":
            end += 1
        w["_span"] = (idx, end)
        pos = end


def _from_words(
    words: list[dict], speaker: str | None, language: str, source: str = ""
) -> Segment:
    spans = [w["_span"] for w in words if w.get("_span")]
    if spans and source:
        text = source[spans[0][0] : spans[-1][1]].strip()
    else:
        text = " ".join(w["text"] for w in words if w.get("text")).strip()
    text = filters.strip_disallowed(text)
    clean = [{k: v for k, v in w.items() if k != "_span"} for w in words]
    return Segment(
        start=float(words[0]["start"]),
        end=float(words[-1]["end"]),
        text=text,
        speaker=speaker,
        language=language,
        words=clean,
    )


def _dominant_speaker(start: float, end: float, turns: list[dict]) -> str | None:
    """Speaker with the greatest overlap over [start, end)."""
    best, best_ov = None, 0.0
    for t in turns:
        ov = min(end, float(t["end"])) - max(start, float(t["start"]))
        if ov > best_ov:
            best, best_ov = t.get("speaker"), ov
    return best
