"""Server configuration: data directory layout and one-time environment setup."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8765


@dataclass
class Settings:
    data_dir: Path
    db_path: Path
    items_dir: Path
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT


def load_settings(data_dir: str | Path | None = None) -> Settings:
    """Resolve the data directory: explicit arg > $QSTT_DATA > <repo>/data."""
    root = Path(data_dir) if data_dir else Path(os.environ.get("QSTT_DATA", REPO_ROOT / "data"))
    root = root.expanduser().resolve()
    return Settings(
        data_dir=root,
        db_path=root / "qstt.db",
        items_dir=root / "items",
    )


def ensure_pyannote_token() -> None:
    """Populate PYANNOTE_AUTH_TOKEN from the cached HF token, if not already set.

    Never logs or prints the token itself.
    """
    if os.environ.get("PYANNOTE_AUTH_TOKEN"):
        return
    for candidate in (
        Path.home() / ".cache/huggingface/token",
        Path.home() / ".huggingface/token",
    ):
        if candidate.exists():
            token = candidate.read_text(encoding="utf-8").strip()
            if token:
                os.environ["PYANNOTE_AUTH_TOKEN"] = token
            return


def diarization_available() -> bool:
    """Best-effort local check: can we even attempt diarization here.

    Deliberately does not hit the network (unlike qstt.doctor, which checks
    whether the gated model's terms were accepted) -- this is queried on every
    GET /api/options and must be fast.
    """
    token = (
        os.environ.get("PYANNOTE_AUTH_TOKEN")
        or os.environ.get("HF_TOKEN")
        or os.environ.get("HUGGINGFACE_HUB_TOKEN")
    )
    if not token:
        return False
    try:
        import pyannote.audio  # noqa: F401
        import torch  # noqa: F401
    except Exception:
        return False
    return True
