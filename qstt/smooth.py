"""Suppress diarization jitter.

Speaker turn boundaries from pyannote wobble by a few hundred milliseconds. Split
a transcript on them word by word and single words get torn out of a sentence
and handed to whoever happened to own that instant:

    SPEAKER_00: But nonetheless, if I had
    SPEAKER_02: to
    SPEAKER_00: pick a single best model today

Nobody said "to" as a turn. Two passes fix it: absorb implausibly short runs
back into the speaker that surrounds them, then merge what is left.
"""

from __future__ import annotations

from .pipeline import Segment


def absorb_micro_turns(
    segments: list[Segment],
    *,
    max_words: int = 3,
    max_dur: float = 1.2,
) -> list[Segment]:
    """Reassign short interjections that interrupt a single continuing speaker.

    A run only qualifies when the speaker before and after it are the same:
    that pattern (A -> B -> A over a word or two) is boundary noise, whereas a
    genuine short turn sits between two *different* speakers, or stands at the
    edge of the recording.
    """
    if len(segments) < 3:
        return segments

    out = [s for s in segments]
    for i in range(1, len(out) - 1):
        seg = out[i]
        prev, nxt = out[i - 1], out[i + 1]
        if prev.speaker is None or prev.speaker != nxt.speaker:
            continue
        if seg.speaker == prev.speaker:
            continue
        if len(seg.text.split()) > max_words or (seg.end - seg.start) > max_dur:
            continue
        out[i] = Segment(seg.start, seg.end, seg.text, prev.speaker,
                         seg.language, seg.words)
    return out


def merge_same_speaker(
    segments: list[Segment], *, max_gap: float = 2.0
) -> list[Segment]:
    """Join consecutive segments from one speaker into a single turn."""
    if not segments:
        return []

    merged = [segments[0]]
    for seg in segments[1:]:
        last = merged[-1]
        if seg.speaker == last.speaker and (seg.start - last.end) <= max_gap:
            text = " ".join(t for t in (last.text, seg.text) if t)
            merged[-1] = Segment(
                start=last.start,
                end=max(last.end, seg.end),
                text=text,
                speaker=last.speaker,
                language=last.language or seg.language,
                words=last.words + seg.words,
            )
        else:
            merged.append(seg)
    return merged


def smooth(
    segments: list[Segment],
    *,
    max_words: int = 3,
    max_dur: float = 1.2,
    max_gap: float = 2.0,
    passes: int = 2,
) -> list[Segment]:
    """Absorb jitter, then merge. Repeated because absorbing exposes new pairs."""
    out = segments
    for _ in range(max(1, passes)):
        before = len(out)
        out = merge_same_speaker(absorb_micro_turns(
            out, max_words=max_words, max_dur=max_dur), max_gap=max_gap)
        if len(out) == before:
            break
    return out
