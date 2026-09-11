"""Shard merging and worker sizing."""

from qstt.parallel import _merge, suggest_workers
from qstt.pipeline import Rejection, Result, Segment


def _part(source, segs, rejs=(), spans=0, language="English"):
    return Result(source, 0.0, language, segs, list(rejs), [],
                  {"spans": spans, "vad": "silero", "reject_reasons": {}})


def test_merge_orders_segments_across_shards():
    a = _part("f", [Segment(10.0, 12.0, "second")], spans=1)
    b = _part("f", [Segment(0.0, 2.0, "first")], spans=1)
    merged = _merge([a, b], "f", 20.0)
    assert [s.text for s in merged.segments] == ["first", "second"]
    assert merged.stats["spans"] == 2
    assert merged.stats["workers"] == 2


def test_merge_sums_rejection_reasons():
    a = _part("f", [], [Rejection(1, 2, "x", "foreign-script:Han")])
    b = _part("f", [], [Rejection(5, 6, "y", "repetition:word")])
    a.stats["reject_reasons"] = {"foreign-script": 1}
    b.stats["reject_reasons"] = {"foreign-script": 2, "repetition": 1}
    merged = _merge([a, b], "f", 10.0)
    assert merged.stats["reject_reasons"] == {"foreign-script": 3, "repetition": 1}
    assert len(merged.rejections) == 2


def test_merge_takes_majority_language():
    parts = [
        _part("f", [], language="German"),
        _part("f", [], language="German"),
        _part("f", [], language="English"),
    ]
    assert _merge(parts, "f", 10.0).language == "German"


def test_merge_sorts_rejections_by_time():
    a = _part("f", [], [Rejection(9, 10, "late", "x")])
    b = _part("f", [], [Rejection(1, 2, "early", "x")])
    assert [r.text for r in _merge([a, b], "f", 10.0).rejections] == ["early", "late"]


def test_short_audio_gets_a_single_worker():
    assert suggest_workers("Qwen/Qwen3-ASR-1.7B", 60) == 1
    assert suggest_workers("Qwen/Qwen3-ASR-1.7B", 200) == 1


def test_worker_count_is_capped():
    assert 1 <= suggest_workers("Qwen/Qwen3-ASR-1.7B", 100_000) <= 6
