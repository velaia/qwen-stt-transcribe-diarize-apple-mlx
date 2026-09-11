"""Output formats. Speaker labels appear wherever diarization ran."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path


def _ts(seconds: float, sep: str = ",") -> str:
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{ms:03d}"


def _clock(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h:d}:{m:02d}:{s:02d}" if h else f"{m:d}:{s:02d}"


def to_txt(result) -> str:
    lines = []
    for seg in result.segments:
        if not seg.text:
            continue
        lines.append(f"{seg.speaker}: {seg.text}" if seg.speaker else seg.text)
    return "\n".join(lines) + ("\n" if lines else "")


def to_markdown(result) -> str:
    """Readable transcript: speaker turns merged, timestamps in the margin."""
    name = Path(result.source).name
    out = [
        f"# {name}",
        "",
        f"- **Duration:** {_clock(result.duration)}",
        f"- **Language:** {result.language}",
        f"- **Speakers:** {', '.join(result.speakers) if result.speakers else 'not diarized'}",
        f"- **Speech detected:** {result.stats.get('speech_ratio', 0):.0%} of audio",
    ]
    if result.rejections:
        out.append(f"- **Filtered out:** {len(result.rejections)} segment(s)")
    out += ["", "---", ""]

    prev = object()
    for seg in result.segments:
        if not seg.text:
            continue
        if seg.speaker != prev:
            out.append("")
            head = seg.speaker or "Transcript"
            out.append(f"**{head}** · `{_clock(seg.start)}`")
            prev = seg.speaker
        out.append(seg.text)

    if result.rejections:
        out += ["", "---", "", "## Filtered segments", "",
                "Rejected as hallucination or off-language; shown for review only.", "",
                "| Time | Reason | Raw output |", "|---|---|---|"]
        for r in result.rejections:
            raw = r.text.replace("|", "\\|")[:80] or "(empty)"
            out.append(f"| `{_clock(r.start)}` | `{r.reason}` | {raw} |")
    return "\n".join(out).strip() + "\n"


def to_srt(result) -> str:
    blocks = []
    for i, seg in enumerate((s for s in result.segments if s.text), 1):
        label = f"{seg.speaker}: " if seg.speaker else ""
        blocks.append(
            f"{i}\n{_ts(seg.start)} --> {_ts(seg.end)}\n{label}{seg.text}\n"
        )
    return "\n".join(blocks)


def to_vtt(result) -> str:
    out = ["WEBVTT", ""]
    for seg in result.segments:
        if not seg.text:
            continue
        label = f"<v {seg.speaker}>" if seg.speaker else ""
        out.append(f"{_ts(seg.start, '.')} --> {_ts(seg.end, '.')}")
        out.append(f"{label}{seg.text}")
        out.append("")
    return "\n".join(out)


def to_json(result) -> str:
    payload = {
        "source": result.source,
        "duration_sec": round(result.duration, 3),
        "language": result.language,
        "speakers": result.speakers,
        "stats": result.stats,
        "segments": [
            {
                "start": round(s.start, 3),
                "end": round(s.end, 3),
                "speaker": s.speaker,
                "language": s.language,
                "text": s.text,
                "words": s.words,
            }
            for s in result.segments
        ],
        "rejected": [asdict(r) for r in result.rejections],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


WRITERS = {
    "txt": to_txt,
    "md": to_markdown,
    "srt": to_srt,
    "vtt": to_vtt,
    "json": to_json,
}


def write(result, formats: list[str], outdir: Path, stem: str) -> list[Path]:
    outdir.mkdir(parents=True, exist_ok=True)
    written = []
    for fmt in formats:
        path = outdir / f"{stem}.{fmt}"
        path.write_text(WRITERS[fmt](result), encoding="utf-8")
        written.append(path)
    return written
