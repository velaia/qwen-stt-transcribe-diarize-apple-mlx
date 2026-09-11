"""Language selection must never escape {English, German}."""

import pytest

from qstt import lang


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("en", "English"), ("EN", "English"), ("english", "English"),
        ("de", "German"), ("Deutsch", "German"), ("German", "German"),
        ("French", None), ("zh", None), ("Dutch", None), ("", None), (None, None),
    ],
)
def test_normalize_only_admits_english_and_german(raw, expected):
    assert lang.normalize(raw) == expected


def test_vote_ignores_out_of_set_labels():
    assert lang.vote(["English", "English", "Dutch", "Chinese"]) == ("English", 1.0)


def test_vote_returns_none_when_nothing_is_in_set():
    winner, share = lang.vote(["Chinese", "Danish", "Dutch"])
    assert winner is None and share == 0.0


def test_vote_reports_a_split_decision():
    winner, share = lang.vote(["English", "German"])
    assert winner in lang.ALLOWED and share == 0.5


def test_lexicon_picks_english():
    picked, conf = lang.choose_by_lexicon(
        "we should have the numbers ready for the meeting that is on friday",
        "wi schud hef the nambers reddi for the miting",
    )
    assert picked == "English" and conf > 0.5


def test_lexicon_picks_german():
    picked, conf = lang.choose_by_lexicon(
        "each vice nicked op dass is a gooter idea",
        "ich weiß nicht ob das eine gute Idee ist aber wir können es versuchen",
    )
    assert picked == "German" and conf > 0.5


def test_lexicon_defaults_to_english_on_empty_input():
    assert lang.choose_by_lexicon("", "") == ("English", 0.0)


def test_umlauts_count_toward_german():
    de, _ = lang.score_text("Grüße Straße Öl")
    assert de >= 6
