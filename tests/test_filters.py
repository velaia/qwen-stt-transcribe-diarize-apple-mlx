"""The guards that keep non-English/German text and hallucinations out."""

import pytest

from qstt import filters


@pytest.mark.parametrize(
    "text",
    [
        "Guten Morgen, wie geht es Ihnen heute?",
        "The quarterly numbers look strong this year.",
        "Wir treffen uns um halb zehn im großen Besprechungsraum.",
        "I'll send the follow-up email after stand-up.",
        "Die Straße war gesperrt, deshalb kam ich zu spät.",
        "Nein, das sehe ich anders.",
    ],
)
def test_genuine_speech_survives(text):
    assert filters.screen(text) is None


@pytest.mark.parametrize(
    "text,script",
    [
        ("嗯 嗯 我不知道", "Han"),
        ("ご視聴ありがとうございました", "Hiragana"),
        ("Спасибо за просмотр", "Cyrillic"),
        ("شكرا لكم", "Arabic"),
        ("감사합니다", "Hangul"),
        ("ขอบคุณครับ", "Thai"),
        ("Ευχαριστώ πολύ", "Greek"),
    ],
)
def test_foreign_scripts_are_rejected(text, script):
    reason = filters.screen(text)
    assert reason is not None and reason.startswith("foreign-script")


def test_foreign_script_names_the_script():
    assert filters.foreign_script("我不知道") == "foreign-script:Han"


def test_umlauts_and_eszett_are_not_foreign():
    assert filters.foreign_script("Grüße aus München, Straße, Öl") is None


@pytest.mark.parametrize(
    "text",
    [
        "ja ja ja ja ja ja ja ja",
        "Ich weiß nicht. Ich weiß nicht. Ich weiß nicht. Ich weiß nicht.",
        "you you you you you you you you you",
    ],
)
def test_decoding_loops_are_rejected(text):
    assert (filters.screen(text) or "").startswith("repetition")


def test_natural_repetition_survives():
    # Repetition for emphasis is normal speech, not a decoding loop.
    assert filters.screen(
        "It was very, very good and we were very happy with the result overall."
    ) is None


@pytest.mark.parametrize(
    "text",
    [
        "Untertitel von Stephanie Geiges",
        "Untertitelung im Auftrag des ZDF, 2021",
        "Subtitles by the Amara.org community",
        "Thanks for watching!",
        "Please subscribe",
    ],
)
def test_boilerplate_credits_always_rejected(text):
    assert filters.screen(text) is not None


@pytest.mark.parametrize("text", ["Ja.", "Okay.", "Vielen Dank.", "Yes"])
def test_bare_interjections_kept_by_default(text):
    """These are often genuine turns, so the default must not delete them."""
    assert filters.screen(text) is None
    assert filters.screen(text, aggressive_filler=True) is not None


def test_symbol_soup_rejected():
    assert filters.screen("♪ ♪ ♪ ♪ ♪ ♪") is not None
    assert filters.low_alpha("-- -- -- -- -- -- -- --") == "low-alpha"


def test_empty_rejected():
    assert filters.screen("") == "empty"
    assert filters.screen("   ") == "empty"


def test_strip_disallowed_keeps_german_and_drops_cjk():
    assert filters.strip_disallowed("Größe 中文 test") == "Größe test"


def test_allow_foreign_ratio_tolerates_a_stray_character():
    text = "The model is called Qwen 千 and it runs locally on this machine here"
    assert filters.screen(text) is not None
    assert filters.screen(text, allow_foreign_ratio=0.1) is None


def test_foreign_script_is_screened_before_stripping():
    """Regression: stripping first erases the evidence the guard needs.

    `strip_disallowed` removes CJK characters outright, so screening a stripped
    string reports 'empty' and `allow_foreign_ratio` silently stops working.
    The pipeline must screen the raw decode.
    """
    raw = "嗯嗯我不知道"
    assert filters.screen(raw) == "foreign-script:Han"
    assert filters.strip_disallowed(raw) == ""
    assert filters.screen(filters.strip_disallowed(raw)) == "empty"


def test_default_ratio_keeps_speech_with_one_stray_glyph():
    """One artifact must not cost a whole span of real speech."""
    text = ("We reviewed the deployment plan and agreed to ship on Thursday 千 "
            "once the final tests pass and the team signs off on the release")
    assert filters.screen(text, allow_foreign_ratio=0.15) is None
    assert "千" not in filters.strip_disallowed(text)


def test_default_ratio_still_rejects_a_foreign_segment():
    assert filters.screen("我不知道这是什么意思", allow_foreign_ratio=0.15) is not None
