"""Rejection filters for hallucinated and off-language output.

Three independent guards, applied to every candidate segment:

1. Script guard   -- English and German are written in Latin script. Any
                     Han/Kana/Hangul/Cyrillic/Arabic/Hebrew/Thai/Devanagari/
                     Greek character is, by construction, not a transcript of
                     English or German speech. This is a hard guarantee rather
                     than a heuristic.
2. Repetition     -- degenerate decoding loops ("ja ja ja ja ja ...").
3. Filler phrases -- the stock sentences ASR models emit over silence, music
                     and mumbling ("Thank you.", "Untertitel von ...").

Each returns a reason string when it rejects, or None to accept.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter

# Codepoint ranges that cannot occur in genuine English or German text.
_FOREIGN_SCRIPT_RANGES: tuple[tuple[int, int, str], ...] = (
    (0x0370, 0x03FF, "Greek"),
    (0x0400, 0x04FF, "Cyrillic"),
    (0x0500, 0x052F, "Cyrillic"),
    (0x0530, 0x058F, "Armenian"),
    (0x0590, 0x05FF, "Hebrew"),
    (0x0600, 0x06FF, "Arabic"),
    (0x0700, 0x074F, "Syriac"),
    (0x0750, 0x077F, "Arabic"),
    (0x0900, 0x097F, "Devanagari"),
    (0x0980, 0x09FF, "Bengali"),
    (0x0A00, 0x0A7F, "Gurmukhi"),
    (0x0B80, 0x0BFF, "Tamil"),
    (0x0E00, 0x0E7F, "Thai"),
    (0x0E80, 0x0EFF, "Lao"),
    (0x1000, 0x109F, "Myanmar"),
    (0x10A0, 0x10FF, "Georgian"),
    (0x1100, 0x11FF, "Hangul"),
    (0x1780, 0x17FF, "Khmer"),
    (0x3040, 0x309F, "Hiragana"),
    (0x30A0, 0x30FF, "Katakana"),
    (0x3100, 0x312F, "Bopomofo"),
    (0x3400, 0x4DBF, "Han"),
    (0x4E00, 0x9FFF, "Han"),
    (0xA000, 0xA4CF, "Yi"),
    (0xAC00, 0xD7AF, "Hangul"),
    (0xF900, 0xFAFF, "Han"),
    (0xFF00, 0xFFEF, "Fullwidth"),
    (0x20000, 0x2FA1F, "Han"),
)

# Stock hallucinations, in two tiers.
#
# ALWAYS: boilerplate from subtitle/video training data. These are never a
# genuine utterance in a meeting or interview recording, so they are dropped
# unconditionally.
#
# WEAK: bare interjections and one-word answers. A real speaker genuinely says
# "Ja." or "Okay.", so these are kept by default and only dropped under
# --aggressive-filler. Deleting them silently would lose real turns.
_FILLER_ALWAYS: frozenset[str] = frozenset(
    {
        "thanks for watching", "thank you for watching", "please subscribe",
        "subscribe to my channel", "like and subscribe", "see you next time",
        "transcription by", "subtitles by", "amara org", "www amara org",
        "end of transcript", "copyright", "all rights reserved",
        "untertitel im auftrag des zdf", "untertitel der tagesschau",
        "mehr infos auf", "bis zum nachsten mal",
        "applause", "music", "laughter", "applaus", "musik", "gelachter",
    }
)

# Prefix families: the credit line varies by broadcaster/volunteer name, so the
# stable part is the prefix.
_FILLER_PREFIXES: tuple[str, ...] = (
    "untertitel von", "untertitelung im auftrag", "untertitel im auftrag",
    "subtitles by the amara", "subtitling by", "transcript by",
    "copyright wdr", "swr ", "zdf ",
)

_FILLER_WEAK: frozenset[str] = frozenset(
    {
        "thank you", "thanks", "bye", "bye bye", "you", "okay", "ok", "mm",
        "mhm", "hmm", "uh", "um", "so", "the", "yeah", "yes", "no",
        "vielen dank", "danke", "dankeschon", "danke schon", "tschuss",
        "auf wiedersehen", "ja", "nein", "ah", "aha", "also", "das war s",
        "...", ".", "-",
    }
)

_PUNCT = re.compile(r"[^\w\s]", re.UNICODE)
_WS = re.compile(r"\s+")

# Latin letters, German umlauts/eszett, digits and ordinary punctuation.
_ALLOWED_CHAR = re.compile(r"[A-Za-z0-9À-ɏ\s.,!?;:'\"()\[\]{}\-–—…/%&@#*+=<>$€£~^_|\\`]")


def _fold(text: str) -> str:
    """Lowercase, strip accents and punctuation, collapse whitespace."""
    nfkd = unicodedata.normalize("NFKD", text.lower())
    stripped = "".join(c for c in nfkd if not unicodedata.combining(c))
    return _WS.sub(" ", _PUNCT.sub(" ", stripped)).strip()


def foreign_script(text: str, *, max_ratio: float = 0.0) -> str | None:
    """Reject text containing scripts that English/German never use.

    `max_ratio` is the fraction of such characters tolerated before rejecting;
    0.0 means any occurrence is fatal.
    """
    hits: Counter[str] = Counter()
    letters = 0
    for ch in text:
        cp = ord(ch)
        if ch.isalpha() or cp > 0x2E80:
            letters += 1
        for lo, hi, name in _FOREIGN_SCRIPT_RANGES:
            if lo <= cp <= hi:
                hits[name] += 1
                break
    if not hits:
        return None
    total = sum(hits.values())
    if letters and (total / letters) <= max_ratio:
        return None
    worst = hits.most_common(1)[0][0]
    return f"foreign-script:{worst}"


def repetitive(text: str, *, max_repeats: int = 4, min_words: int = 6) -> str | None:
    """Reject degenerate loops: one token, or one short phrase, on repeat."""
    words = _fold(text).split()
    if len(words) < min_words:
        return None

    counts = Counter(words)
    word, n = counts.most_common(1)[0]
    if n >= max_repeats and n / len(words) > 0.55:
        return f"repetition:word:{word!r}x{n}"

    # Repeated n-grams, e.g. "ich weiss nicht ich weiss nicht ich weiss nicht".
    for size in (2, 3, 4, 5):
        if len(words) < size * max_repeats:
            continue
        grams = Counter(
            tuple(words[i : i + size]) for i in range(len(words) - size + 1)
        )
        gram, gn = grams.most_common(1)[0]
        if gn >= max_repeats and (gn * size) / len(words) > 0.6:
            return f"repetition:ngram:{' '.join(gram)!r}x{gn}"
    return None


def filler_only(text: str, *, aggressive: bool = False) -> str | None:
    """Reject segments that are nothing but a stock hallucination.

    Boilerplate credits are always rejected. Bare interjections ("Ja.",
    "Okay.") are only rejected when `aggressive` is set, since they are often
    genuine short turns.
    """
    folded = _fold(text)
    if not folded:
        return "empty"

    def hit(s: str) -> str | None:
        if s in _FILLER_ALWAYS:
            return f"filler:{s!r}"
        if any(s.startswith(p.strip()) for p in _FILLER_PREFIXES):
            return f"filler-credit:{s[:40]!r}"
        if aggressive and s in _FILLER_WEAK:
            return f"filler-weak:{s!r}"
        return None

    if (r := hit(folded)):
        return r

    # "Thank you. Thank you. Thank you." -> unique content is a single filler.
    parts = [p for p in (_fold(s) for s in re.split(r"[.!?]+", text)) if p]
    if parts and len(set(parts)) == 1 and (r := hit(parts[0])):
        return r.replace("filler", "filler-repeated", 1)
    return None


def low_alpha(text: str, *, min_ratio: float = 0.5, min_len: int = 12) -> str | None:
    """Reject symbol soup (e.g. '♪ ♪ ♪', '-- -- --') once past a length floor."""
    if len(text.strip()) < min_len:
        return None
    alpha = sum(c.isalpha() for c in text)
    if alpha / max(1, len(text.strip())) < min_ratio:
        return "low-alpha"
    return None


def strip_disallowed(text: str) -> str:
    """Drop stray characters outside the English/German character set."""
    return _WS.sub(" ", "".join(_ALLOWED_CHAR.findall(text))).strip()


def screen(
    text: str,
    *,
    allow_foreign_ratio: float = 0.0,
    max_repeats: int = 4,
    aggressive_filler: bool = False,
) -> str | None:
    """Run every guard. Returns a rejection reason, or None if the text is good."""
    if not text or not text.strip():
        return "empty"
    for reason in (
        foreign_script(text, max_ratio=allow_foreign_ratio),
        repetitive(text, max_repeats=max_repeats),
        low_alpha(text),
        filler_only(text, aggressive=aggressive_filler),
    ):
        if reason:
            return reason
    return None
