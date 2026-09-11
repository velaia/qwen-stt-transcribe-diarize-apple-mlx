"""Language decisions constrained to English and German.

Qwen3-ASR identifies 52 languages. Left to itself it will happily label a
mumbled half-sentence as Dutch, Danish or Chinese and then decode it that way.
Nothing here ever returns a language outside the allowed set: detection is a
choice *between* English and German, never an open-ended question.
"""

from __future__ import annotations

from collections import Counter

# Canonical names as the model reports and accepts them.
ENGLISH = "English"
GERMAN = "German"
ALLOWED = (ENGLISH, GERMAN)

CODE_TO_NAME = {
    "en": ENGLISH, "eng": ENGLISH, "english": ENGLISH,
    "de": GERMAN, "deu": GERMAN, "ger": GERMAN, "german": GERMAN, "deutsch": GERMAN,
}

# High-frequency function words. Chosen to be near-disjoint between the two
# languages: words like "in", "man", "so", "war", "die" occur in both and are
# deliberately excluded.
_DE_MARKERS = frozenset(
    """der das und ich nicht ist ein eine zu mit auf für von dass wir sie es
    aber schon noch sehr auch dann wenn oder als um nach bei über durch ohne
    haben hat sind war waren wird werden kann können muss müssen soll wollen
    mich mir dich dir uns euch ihm ihr ihnen diese dieser dieses jetzt hier
    immer nur ganz mal eben halt doch weil damit während zwischen gegen""".split()
)
_EN_MARKERS = frozenset(
    """the and is a to of that we you it but not this for with have has are
    was were will would can could should must about from they them their there
    then when what which who how why been being does did doing your our its
    just really very much some any because while between against into""".split()
)


def normalize(name: str | None) -> str | None:
    """Map a user- or model-supplied language label to a canonical name."""
    if not name:
        return None
    key = name.strip().lower()
    if key in CODE_TO_NAME:
        return CODE_TO_NAME[key]
    for canon in ALLOWED:
        if key == canon.lower():
            return canon
    return None


def score_text(text: str) -> tuple[int, int]:
    """Return (german_hits, english_hits) for a transcript."""
    words = [w.strip(".,!?;:\"'()[]—–-").lower() for w in text.split()]
    de = sum(w in _DE_MARKERS for w in words)
    en = sum(w in _EN_MARKERS for w in words)
    # Umlauts and eszett are a strong German signal on their own.
    de += 2 * sum(text.count(c) for c in "äöüßÄÖÜ")
    return de, en


def choose_by_lexicon(en_text: str, de_text: str) -> tuple[str, float]:
    """Pick between two forced decodes of the same audio.

    Returns (language, confidence in 0..1). Each decode is scored for how much
    it looks like the language it was decoded as; the more self-consistent one
    wins.
    """
    _, en_hits = score_text(en_text)
    de_hits, _ = score_text(de_text)
    en_norm = en_hits / max(1, len(en_text.split()))
    de_norm = de_hits / max(1, len(de_text.split()))
    total = en_norm + de_norm
    if total <= 0:
        return ENGLISH, 0.0
    if de_norm >= en_norm:
        return GERMAN, de_norm / total
    return ENGLISH, en_norm / total


def vote(detected: list[str | None]) -> tuple[str | None, float]:
    """Majority vote over per-probe detections, ignoring out-of-set labels.

    Returns (winner or None, share of in-set votes). None means the model never
    landed inside {English, German} and a lexical tie-break is needed.
    """
    inset = [d for d in (normalize(x) for x in detected) if d]
    if not inset:
        return None, 0.0
    counts = Counter(inset)
    winner, n = counts.most_common(1)[0]
    return winner, n / len(inset)
