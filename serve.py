#!/usr/bin/env python3
"""Run the qstt web UI backend, or import an existing CLI transcript into it.

    ./serve.py                                  # start on 0.0.0.0:8765
    ./serve.py --port 9000 --data /tmp/qstt-dev
    ./serve.py import transcripts/episode.json --media episode.mp3
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

from server import jobs, media, results
from server.config import DEFAULT_HOST, DEFAULT_PORT, load_settings
from server.db import DB


def cmd_run(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(
        prog="serve.py", description="Run the qstt web UI backend."
    )
    ap.add_argument("--host", default=DEFAULT_HOST)
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--data", default=None, help="data directory (default: $QSTT_DATA or ./data)")
    args = ap.parse_args(argv)

    import uvicorn

    from server.app import create_app

    app = create_app(data_dir=args.data)
    # Keep graceful shutdown short so an open SSE stream doesn't block Ctrl-C.
    config = uvicorn.Config(app, host=args.host, port=args.port, timeout_graceful_shutdown=1)
    uvicorn.Server(config).run()
    return 0


def cmd_import(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(
        prog="serve.py import",
        description="Import an existing CLI JSON transcript into the web UI catalog.",
    )
    ap.add_argument("result_json", type=Path)
    ap.add_argument("--media", default=None, help="media file (default: the path recorded "
                                                    "in the transcript's 'source' field)")
    ap.add_argument("--title", default=None, help="default: the JSON file's stem")
    ap.add_argument("--data", default=None)
    args = ap.parse_args(argv)

    if not args.result_json.exists():
        print(f"error: no such file: {args.result_json}", file=sys.stderr)
        return 1

    from qstt import writers

    result = writers.load_result(args.result_json)

    settings = load_settings(args.data)
    settings.items_dir.mkdir(parents=True, exist_ok=True)
    db = DB(settings.db_path)

    item_id = db.new_id()
    while db.get(item_id) is not None:
        item_id = db.new_id()
    item_dir = settings.items_dir / item_id
    item_dir.mkdir(parents=True, exist_ok=True)

    media_path = Path(args.media) if args.media else Path(result.source)
    ext = media_path.suffix.lstrip(".").lower() or "bin"
    dest = item_dir / f"original.{ext}"
    source_filename = media_path.name
    media_present = media_path.exists()
    if media_present:
        try:
            os.link(media_path, dest)
        except OSError:
            shutil.copy2(media_path, dest)
    else:
        print(f"warning: media file not found: {media_path}", file=sys.stderr)

    title = args.title or args.result_json.stem

    web_opts = jobs.default_web_options()
    web_opts["diarize"] = bool(result.speakers)
    smoothing = jobs.smoothing_from_web_options(web_opts)

    db.create(item_id=item_id, title=title, source_filename=source_filename,
              options=web_opts, smoothing=smoothing)
    db.update_fts(item_id, filename=source_filename)
    results.write_raw_result(item_dir, result)

    probed_duration = media_kind = None
    playable_original = False
    audio_ready = False
    audio_version = None
    media_error = None
    if media_present:
        try:
            p = media.probe(dest)
            media_kind, playable_original = p.media_kind, p.playable_original
            probed_duration = p.duration
            audio_dst = item_dir / "audio.m4a"
            media.make_proxy(dest, audio_dst)
            audio_ready = True
            audio_version = media.audio_version(audio_dst)
        except media.MediaError as exc:
            media_error = str(exc)
            print(f"warning: {exc}", file=sys.stderr)

    smoothed, json_text = results.build_smoothed(item_dir / "result.raw.json", smoothing)
    results.write_result_files(item_dir, json_text)
    db.update_fts(item_id, body=results.plain_text_body(smoothed))

    db.update(
        item_id,
        status="done", stage="done", progress=1.0, error=None,
        has_result=1,
        duration=probed_duration if probed_duration is not None else smoothed.duration,
        language=smoothed.language,
        speakers_json=json.dumps(smoothed.speakers),
        stats_json=json.dumps(smoothed.stats),
        rejected_count=len(smoothed.rejections),
        media_kind=media_kind,
        playable_original=int(bool(playable_original)),
        audio_ready=int(audio_ready),
        audio_version=audio_version,
        media_error=media_error,
    )
    print(f"imported {item_id}: {title!r} ({len(smoothed.segments)} segments)")
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == "import":
        return cmd_import(argv[1:])
    return cmd_run(argv)


if __name__ == "__main__":
    sys.exit(main())
