"""Audio loading and slicing. Everything downstream assumes mono 16 kHz float32."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import numpy as np

SAMPLE_RATE = 16_000


def load_audio(path: str | Path) -> np.ndarray:
    """Decode any ffmpeg-readable file to mono 16 kHz float32 in [-1, 1]."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(path)
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("ffmpeg not found on PATH. Install it with: brew install ffmpeg")

    cmd = [
        "ffmpeg", "-nostdin", "-threads", "0",
        "-i", str(path),
        "-f", "s16le", "-ac", "1", "-acodec", "pcm_s16le",
        "-ar", str(SAMPLE_RATE),
        "-",
    ]
    proc = subprocess.run(cmd, capture_output=True)
    if proc.returncode != 0:
        tail = proc.stderr.decode("utf-8", "replace").strip().splitlines()[-5:]
        raise RuntimeError(f"ffmpeg failed on {path.name}:\n" + "\n".join(tail))

    pcm = np.frombuffer(proc.stdout, dtype=np.int16)
    return (pcm.astype(np.float32) / 32768.0).copy()


def duration_sec(audio: np.ndarray) -> float:
    return len(audio) / SAMPLE_RATE


def slice_audio(audio: np.ndarray, start: float, end: float) -> np.ndarray:
    """Slice by seconds, clamped to the array bounds."""
    a = max(0, int(start * SAMPLE_RATE))
    b = min(len(audio), int(end * SAMPLE_RATE))
    return audio[a:b] if b > a else np.zeros(0, dtype=np.float32)
