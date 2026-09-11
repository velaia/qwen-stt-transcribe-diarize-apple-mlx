"""Building result.json (smoothed) from result.raw.json, and render helpers."""

from __future__ import annotations

import copy
import gzip
import json
import os
from pathlib import Path

from qstt import writers
from qstt.pipeline import Result
from qstt.smooth import smooth

DEFAULT_SMOOTHING = {"max_words": 3, "max_dur": 1.2, "max_gap": 2.0}


def build_smoothed(raw_source, smoothing: dict) -> tuple[Result, str]:
    """Load a raw Result and apply speaker smoothing if it has speakers.

    `raw_source` is anything `qstt.writers.load_result` accepts (path, dict,
    or JSON string parsed to a dict). Returns (Result, json_text).
    """
    result = writers.load_result(raw_source)
    if result.speakers:
        before = len(result.segments)
        result.segments = smooth(
            result.segments,
            max_words=smoothing.get("max_words", DEFAULT_SMOOTHING["max_words"]),
            max_dur=smoothing.get("max_dur", DEFAULT_SMOOTHING["max_dur"]),
            max_gap=smoothing.get("max_gap", DEFAULT_SMOOTHING["max_gap"]),
        )
        result.speakers = sorted({s.speaker for s in result.segments if s.speaker})
        result.stats = dict(result.stats) | {
            "turns_before_smoothing": before,
            "turns_after_smoothing": len(result.segments),
        }
    return result, writers.to_json(result)


def write_result_files(item_dir: Path, json_text: str) -> None:
    """Write result.json and its precompressed result.json.gz, atomically."""
    item_dir = Path(item_dir)
    json_bytes = json_text.encode("utf-8")
    gz_bytes = gzip.compress(json_bytes, compresslevel=6, mtime=0)

    json_tmp = item_dir / "result.json.tmp"
    gz_tmp = item_dir / "result.json.gz.tmp"
    json_tmp.write_bytes(json_bytes)
    gz_tmp.write_bytes(gz_bytes)
    os.replace(json_tmp, item_dir / "result.json")
    os.replace(gz_tmp, item_dir / "result.json.gz")


def write_raw_result(item_dir: Path, result: Result) -> None:
    """Write result.raw.json atomically."""
    item_dir = Path(item_dir)
    text = writers.to_json(result)
    tmp = item_dir / "result.raw.json.tmp"
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, item_dir / "result.raw.json")


def apply_speaker_names(result: Result, names: dict[str, str]) -> Result:
    """Map raw speaker labels to display names, for downloads only. Mutates a copy."""
    if not names:
        return result
    result = copy.deepcopy(result)

    def mapped(label: str | None) -> str | None:
        if not label:
            return label
        return names.get(label, label)

    for seg in result.segments:
        seg.speaker = mapped(seg.speaker)
        for w in seg.words:
            if w.get("speaker"):
                w["speaker"] = mapped(w["speaker"])
    result.speakers = [mapped(s) for s in result.speakers]
    return result


def render_download(result: Result, fmt: str, title: str, speaker_names: dict[str, str]) -> str:
    """Render one of the CLI's output formats, with speaker names and title applied."""
    result = apply_speaker_names(result, speaker_names)
    result.source = title.replace("/", "_")
    return writers.WRITERS[fmt](result)


def plain_text_body(result: Result) -> str:
    """Flat transcript text for the FTS index."""
    return "\n".join(s.text for s in result.segments if s.text)


def load_json_file(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))
