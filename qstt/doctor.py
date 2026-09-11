"""Environment check: what works, what needs a one-time setup step."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

OK, WARN, BAD = "  ok  ", " warn ", " fail "
PYANNOTE_MODEL = "pyannote/speaker-diarization-community-1"


def _hf_token() -> str | None:
    for env in ("PYANNOTE_AUTH_TOKEN", "HF_TOKEN", "HUGGINGFACE_HUB_TOKEN"):
        if os.environ.get(env):
            return os.environ[env]
    for path in (Path.home() / ".cache/huggingface/token", Path.home() / ".huggingface/token"):
        if path.exists():
            token = path.read_text().strip()
            if token:
                return token
    return None


def _check_diarization_access(token: str | None) -> tuple[str, str]:
    """Terms acceptance shows up on file access, not on the metadata endpoint."""
    if not token:
        return WARN, "no Hugging Face token found"
    try:
        import urllib.error
        import urllib.request

        req = urllib.request.Request(
            f"https://huggingface.co/{PYANNOTE_MODEL}/resolve/main/config.yaml",
            headers={"Authorization": f"Bearer {token}"},
            method="HEAD",
        )
        urllib.request.urlopen(req, timeout=10)
        return OK, "model terms accepted, diarization ready"
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            return WARN, "token found, but model terms not accepted yet"
        return WARN, f"HTTP {exc.code} checking model access"
    except Exception as exc:
        return WARN, f"could not reach Hugging Face ({type(exc).__name__})"


def run() -> int:
    rows: list[tuple[str, str, str]] = []

    ff = shutil.which("ffmpeg")
    rows.append((OK if ff else BAD, "ffmpeg", ff or "not found -- brew install ffmpeg"))

    try:
        import mlx.core as mx

        rows.append((OK, "mlx", f"{mx.default_device()}"))
    except Exception as exc:
        rows.append((BAD, "mlx", f"not working: {exc}"))

    try:
        import silero_vad  # noqa: F401

        rows.append((OK, "silero VAD", "installed"))
    except Exception:
        rows.append((WARN, "silero VAD", "missing -- falling back to energy VAD"))

    try:
        import pyannote.audio  # noqa: F401
        import torch

        rows.append((OK, "pyannote + torch", f"torch {torch.__version__}"))
    except Exception:
        rows.append((WARN, "pyannote + torch", 'missing -- pip install "mlx-qwen3-asr[diarize]"'))

    token = _hf_token()
    rows.append((OK if token else WARN, "HF token",
                 "found" if token else "not set (only needed for --diarize)"))
    status, detail = _check_diarization_access(token)
    rows.append((status, "diarization access", detail))

    cache = Path.home() / ".cache/huggingface/hub"
    models = (
        sorted(p.name for p in cache.glob("models--Qwen--Qwen3-ASR*"))
        if cache.exists()
        else []
    )
    rows.append((OK if models else WARN, "model cache",
                 ", ".join(m.replace("models--Qwen--", "") for m in models)
                 or "no Qwen3-ASR model cached yet (downloads on first run)"))

    print("\nqstt environment check\n")
    for status, name, detail in rows:
        print(f"[{status}] {name:<20} {detail}")

    if any(s == WARN and n == "diarization access" for s, n, _ in rows):
        print(
            f"\nTo enable speaker labels (--diarize):\n"
            f"  1. Accept the terms at https://huggingface.co/{PYANNOTE_MODEL}\n"
            f"  2. export PYANNOTE_AUTH_TOKEN=$(cat ~/.cache/huggingface/token)\n"
        )
    failed = sum(1 for s, _, _ in rows if s == BAD)
    print(f"\n{len(rows) - failed}/{len(rows)} checks usable\n")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(run())
