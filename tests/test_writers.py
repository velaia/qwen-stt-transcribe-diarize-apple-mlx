"""Output formats, including speaker labels and the filtering report."""

import json

import pytest

from qstt import writers
from qstt.pipeline import Rejection, Result, Segment


@pytest.fixture
def result():
    return Result(
        source="/tmp/standup.m4a",
        duration=95.0,
        language="German",
        segments=[
            Segment(0.0, 4.2, "Guten Morgen zusammen.", "SPEAKER_00", "German"),
            Segment(4.5, 9.0, "Ich habe die Zahlen geprüft.", "SPEAKER_01", "German"),
            Segment(9.2, 12.0, "Sehr gut, danke dir.", "SPEAKER_00", "German"),
        ],
        rejections=[Rejection(30.0, 31.0, "嗯嗯", "foreign-script:Han")],
        speakers=["SPEAKER_00", "SPEAKER_01"],
        stats={"speech_ratio": 0.42, "vad": "silero"},
    )


def test_txt_prefixes_speakers(result):
    out = writers.to_txt(result)
    assert out.startswith("SPEAKER_00: Guten Morgen zusammen.")
    assert "SPEAKER_01: Ich habe die Zahlen geprüft." in out


def test_txt_omits_speaker_when_not_diarized(result):
    for seg in result.segments:
        seg.speaker = None
    assert "SPEAKER" not in writers.to_txt(result)


def test_srt_indices_and_timing_format(result):
    srt = writers.to_srt(result)
    assert srt.startswith("1\n00:00:00,000 --> 00:00:04,200\n")
    assert "\n2\n" in srt and "\n3\n" in srt


def test_vtt_uses_voice_spans(result):
    vtt = writers.to_vtt(result)
    assert vtt.startswith("WEBVTT")
    assert "<v SPEAKER_00>Guten Morgen zusammen." in vtt
    assert "00:00:04.500 --> 00:00:09.000" in vtt


def test_json_roundtrips_and_carries_rejections(result):
    data = json.loads(writers.to_json(result))
    assert data["language"] == "German"
    assert data["speakers"] == ["SPEAKER_00", "SPEAKER_01"]
    assert len(data["segments"]) == 3
    assert data["rejected"][0]["reason"] == "foreign-script:Han"


def test_markdown_reports_what_was_filtered(result):
    md = writers.to_markdown(result)
    assert "## Filtered segments" in md
    assert "foreign-script:Han" in md
    assert "**SPEAKER_00**" in md


def test_markdown_has_no_filter_section_when_nothing_dropped(result):
    result.rejections = []
    assert "## Filtered segments" not in writers.to_markdown(result)


def test_markdown_escapes_pipes_in_rejected_text(result):
    result.rejections = [Rejection(1.0, 2.0, "a | b", "low-alpha")]
    assert r"a \| b" in writers.to_markdown(result)


def test_result_text_joins_segments(result):
    assert writers.to_txt(result).count("\n") == 3
    assert result.text.startswith("Guten Morgen")


def test_empty_result_produces_valid_output(result):
    result.segments, result.rejections, result.speakers = [], [], []
    assert writers.to_txt(result) == ""
    assert writers.to_srt(result) == ""
    assert json.loads(writers.to_json(result))["segments"] == []
    assert "not diarized" in writers.to_markdown(result)
