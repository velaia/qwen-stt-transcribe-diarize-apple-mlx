#!/usr/bin/env bash
# One-shot setup for a fresh machine.
set -euo pipefail
cd "$(dirname "$0")"

command -v ffmpeg >/dev/null || { echo "Installing ffmpeg..."; brew install ffmpeg; }

if [ ! -d .venv ]; then
  echo "Creating virtualenv..."
  if command -v uv >/dev/null; then uv venv --python 3.12 .venv
  else python3 -m venv .venv; fi
fi

echo "Installing dependencies..."
if command -v uv >/dev/null; then
  VIRTUAL_ENV="$PWD/.venv" uv pip install -r requirements.txt
else
  ./.venv/bin/pip install -r requirements.txt
fi

echo
echo "Done. Try:"
echo "  ./.venv/bin/python transcribe.py your-audio.m4a"
echo
echo "For speaker labels (--diarize) you also need to:"
echo "  1. Accept terms at https://huggingface.co/pyannote/speaker-diarization-community-1"
echo "  2. export PYANNOTE_AUTH_TOKEN=\$(cat ~/.cache/huggingface/token)"
