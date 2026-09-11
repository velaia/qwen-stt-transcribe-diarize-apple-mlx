"""Diarization jitter suppression."""

from qstt.pipeline import Segment
from qstt.smooth import absorb_micro_turns, merge_same_speaker, smooth


def seg(start, end, text, speaker):
    return Segment(start, end, text, speaker, "English", [])


def test_single_word_interruption_is_absorbed():
    """The real failure: one word torn out of a sentence mid-turn."""
    segs = [
        seg(0.0, 3.0, "But nonetheless, if I had", "SPEAKER_00"),
        seg(3.0, 3.3, "to", "SPEAKER_02"),
        seg(3.3, 8.0, "pick a single best model today", "SPEAKER_00"),
    ]
    out = smooth(segs)
    assert len(out) == 1
    assert out[0].speaker == "SPEAKER_00"
    assert out[0].text == "But nonetheless, if I had to pick a single best model today"


def test_genuine_short_turn_between_two_speakers_is_kept():
    """A -> B -> C is a real exchange, not boundary noise."""
    segs = [
        seg(0.0, 3.0, "What do you think?", "SPEAKER_00"),
        seg(3.0, 3.4, "Yes.", "SPEAKER_01"),
        seg(3.4, 8.0, "I disagree entirely.", "SPEAKER_02"),
    ]
    assert [s.speaker for s in smooth(segs)] == ["SPEAKER_00", "SPEAKER_01", "SPEAKER_02"]


def test_long_interjection_is_not_absorbed():
    segs = [
        seg(0.0, 3.0, "So here is the plan.", "SPEAKER_00"),
        seg(3.0, 9.0, "Hold on, I really do not think that is going to work at all",
            "SPEAKER_01"),
        seg(9.0, 12.0, "Fair enough.", "SPEAKER_00"),
    ]
    assert len(smooth(segs)) == 3


def test_short_but_slow_interjection_is_not_absorbed():
    """Few words but a long span is a real pause-filled turn, not jitter."""
    segs = [
        seg(0.0, 3.0, "And then?", "SPEAKER_00"),
        seg(3.0, 8.0, "Well... no.", "SPEAKER_01"),
        seg(8.0, 11.0, "Understood.", "SPEAKER_00"),
    ]
    assert len(smooth(segs)) == 3


def test_consecutive_same_speaker_segments_merge():
    segs = [
        seg(0.0, 3.0, "First part.", "A"),
        seg(3.1, 6.0, "Second part.", "A"),
        seg(6.1, 9.0, "Third part.", "A"),
    ]
    out = merge_same_speaker(segs)
    assert len(out) == 1
    assert out[0].text == "First part. Second part. Third part."
    assert out[0].start == 0.0 and out[0].end == 9.0


def test_a_long_gap_starts_a_new_turn():
    segs = [seg(0.0, 3.0, "Before the break.", "A"),
            seg(30.0, 33.0, "After the break.", "A")]
    assert len(merge_same_speaker(segs, max_gap=2.0)) == 2


def test_edges_are_never_absorbed():
    """A short first or last segment has only one neighbour, so it stands."""
    segs = [seg(0.0, 0.3, "Hi", "A"),
            seg(0.3, 5.0, "A long stretch of speech from someone else", "B"),
            seg(5.0, 5.3, "Bye", "C")]
    out = absorb_micro_turns(segs)
    assert out[0].speaker == "A" and out[2].speaker == "C"


def test_unlabelled_neighbours_are_left_alone():
    segs = [seg(0.0, 3.0, "one", None), seg(3.0, 3.2, "two", "A"),
            seg(3.2, 6.0, "three", None)]
    assert absorb_micro_turns(segs)[1].speaker == "A"


def test_smoothing_preserves_the_transcript_text():
    segs = [seg(0.0, 3.0, "But nonetheless, if I had", "A"),
            seg(3.0, 3.3, "to", "B"),
            seg(3.3, 8.0, "pick a model", "A")]
    words_before = " ".join(s.text for s in segs).split()
    words_after = " ".join(s.text for s in smooth(segs)).split()
    assert words_before == words_after


def test_smoothing_is_stable_when_run_twice():
    segs = [seg(0.0, 3.0, "a b c", "A"), seg(3.0, 3.2, "to", "B"),
            seg(3.2, 6.0, "d e f", "A")]
    once = smooth(segs)
    assert [(s.speaker, s.text) for s in smooth(once)] == \
           [(s.speaker, s.text) for s in once]


def test_empty_and_single_inputs_are_handled():
    assert smooth([]) == []
    one = [seg(0.0, 1.0, "Alone.", "A")]
    assert len(smooth(one)) == 1
