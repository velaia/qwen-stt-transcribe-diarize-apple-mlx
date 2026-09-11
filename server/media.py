"""ffprobe/ffmpeg helpers: media inspection and the playback proxy."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

VIDEO_CONTAINER_EXTS = {"mp4", "m4v", "mov", "webm"}


class MediaError(RuntimeError):
    pass


@dataclass
class Probe:
    duration: float | None
    media_kind: str | None  # "audio" | "video" | None
    playable_original: bool


def probe(path: Path) -> Probe:
    path = Path(path)
    cmd = [
        "ffprobe", "-v", "error",
        "-print_format", "json", "-show_format", "-show_streams",
        str(path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        tail = proc.stderr.strip().splitlines()[-5:]
        raise MediaError("ffprobe failed: " + (" ".join(tail) or "(no output)"))

    data = json.loads(proc.stdout or "{}")
    fmt = data.get("format") or {}
    duration = None
    if fmt.get("duration") is not None:
        try:
            duration = float(fmt["duration"])
        except (TypeError, ValueError):
            duration = None

    streams = data.get("streams") or []
    has_real_video = any(
        s.get("codec_type") == "video"
        and (s.get("disposition") or {}).get("attached_pic", 0) != 1
        for s in streams
    )
    has_audio = any(s.get("codec_type") == "audio" for s in streams)

    if has_real_video:
        media_kind = "video"
    elif has_audio:
        media_kind = "audio"
    else:
        media_kind = None

    ext = path.suffix.lower().lstrip(".")
    playable_original = media_kind == "video" and ext in VIDEO_CONTAINER_EXTS
    return Probe(duration=duration, media_kind=media_kind, playable_original=playable_original)


def make_proxy(src: Path, dst: Path) -> None:
    """Build an AAC mono 64kbps playback proxy, written atomically."""
    src, dst = Path(src), Path(dst)
    # Keep the real extension so ffmpeg's muxer autodetection still works.
    tmp = dst.with_name(dst.stem + ".tmp" + dst.suffix)
    cmd = [
        "ffmpeg", "-nostdin", "-y",
        "-i", str(src),
        "-vn", "-map", "0:a:0",
        "-ac", "1", "-c:a", "aac", "-b:a", "64k",
        "-movflags", "+faststart",
        str(tmp),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        tmp.unlink(missing_ok=True)
        tail = proc.stderr.strip().splitlines()[-5:]
        raise MediaError("ffmpeg proxy build failed: " + "\n".join(tail))
    os.replace(tmp, dst)


def audio_version(path: Path) -> str:
    """Opaque cache key that changes iff the file's bytes change (heuristically)."""
    st = Path(path).stat()
    h = hashlib.sha1(f"{st.st_size}-{st.st_mtime_ns}".encode()).hexdigest()
    return h[:12]
