"""All /api routes."""

from __future__ import annotations

import asyncio
import hashlib
import json
import mimetypes
import re
import shutil
from pathlib import Path
from typing import Literal
from urllib.parse import quote

import anyio
from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, Response, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from qstt import writers

from . import jobs, results
from .config import diarization_available
from .db import DB, valid_id

router = APIRouter()

UPLOAD_CHUNK = 1024 * 1024
EXT_RE = re.compile(r"^[A-Za-z0-9]{1,10}$")


# -- request / response models ------------------------------------------------


class WebOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    language: Literal["auto", "en", "de", "mixed"] = "auto"
    diarize: bool = False
    num_speakers: int | None = None
    fast: bool = False
    context: str = ""
    vad_threshold: float = Field(0.5, ge=0.1, le=0.95)
    min_speech_sec: float = Field(0.35, ge=0.0, le=3.0)
    aggressive_filler: bool = False
    allow_foreign_ratio: float = Field(0.15, ge=0.0, le=1.0)
    max_repeats: int = Field(4, ge=2, le=20)
    smooth_max_words: int = Field(3, ge=0, le=20)
    smooth_max_dur: float = Field(1.2, ge=0.0, le=10.0)
    smooth_max_gap: float = Field(2.0, ge=0.0, le=30.0)


class SmoothingBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_words: int = Field(3, ge=0, le=20)
    max_dur: float = Field(1.2, ge=0.0, le=10.0)
    max_gap: float = Field(2.0, ge=0.0, le=30.0)


class PatchBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = None
    notes: str | None = None
    tags: list[str] | None = None
    speaker_names: dict[str, str] | None = None


class RerunBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    options: WebOptions


# -- helpers -------------------------------------------------------------


def _db(request: Request) -> DB:
    return request.app.state.db


def _items_dir(request: Request) -> Path:
    return request.app.state.settings.items_dir


def _row_or_404(db: DB, item_id: str):
    if not valid_id(item_id):
        raise HTTPException(404, "not found")
    row = db.get(item_id)
    if row is None:
        raise HTTPException(404, "not found")
    return row


def _row_to_summary(row, db: DB) -> dict:
    return {
        "id": row["id"],
        "title": row["title"],
        "source_filename": row["source_filename"],
        "media_kind": row["media_kind"],
        "playable_original": bool(row["playable_original"]),
        "audio_ready": bool(row["audio_ready"]),
        "audio_version": row["audio_version"],
        "media_error": row["media_error"],
        "duration": row["duration"],
        "language": row["language"],
        "speakers": json.loads(row["speakers_json"] or "[]"),
        "status": row["status"],
        "stage": row["stage"],
        "progress": row["progress"],
        "error": row["error"],
        "has_result": bool(row["has_result"]),
        "tags": db.get_tags(row["id"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _row_to_detail(row, db: DB) -> dict:
    d = _row_to_summary(row, db)
    d.update({
        "notes": row["notes"],
        "options": json.loads(row["options_json"] or "{}"),
        "speaker_names": json.loads(row["speaker_names_json"] or "{}"),
        "smoothing": json.loads(row["smoothing_json"] or "{}"),
        "stats": json.loads(row["stats_json"] or "{}"),
        "rejected_count": row["rejected_count"],
    })
    return d


def _normalize_tags(tags: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for t in tags:
        t = t.strip().lower()[:32]
        if t and t not in seen:
            seen.add(t)
            out.append(t)
    return out


def _sanitize_ext(filename: str) -> str:
    ext = Path(filename or "").suffix.lstrip(".").lower()
    return ext if EXT_RE.match(ext) else "bin"


_FMT_MEDIA_TYPES = {
    "txt": "text/plain; charset=utf-8",
    "md": "text/markdown; charset=utf-8",
    "srt": "application/x-subrip; charset=utf-8",
    "vtt": "text/vtt; charset=utf-8",
    "json": "application/json",
}


def _ascii_fallback(title: str, fmt: str) -> str:
    safe = title.encode("ascii", "ignore").decode("ascii")
    safe = re.sub(r'[\\/:*?"<>|]', "_", safe).strip() or "transcript"
    return f"{safe}.{fmt}"


# -- health / options ----------------------------------------------------


@router.get("/health")
def health(request: Request):
    jw = request.app.state.job_worker
    return {
        "ok": True,
        "model_loaded": jw.loaded_model(),
        "queue": jw.queue_size(),
        "diarization_available": diarization_available(),
    }


@router.get("/options")
def options_info():
    return {
        "defaults": jobs.default_web_options(),
        "languages": [
            {"value": "auto", "label": "Auto"},
            {"value": "en", "label": "English"},
            {"value": "de", "label": "German"},
            {"value": "mixed", "label": "Mixed"},
        ],
        "limits": {k: list(v) for k, v in jobs.LIMITS.items()},
        "diarization_available": diarization_available(),
    }


# -- transcripts: list / create --------------------------------------------


@router.get("/transcripts")
def list_transcripts(request: Request, q: str | None = None, tag: str | None = None):
    db = _db(request)
    ids = None
    if q:
        ids = db.search_ids(q)
        if not ids:
            return []
    rows = db.list(ids=ids, tag=tag)
    return [_row_to_summary(r, db) for r in rows]


@router.post("/transcripts", status_code=201)
async def create_transcript(
    request: Request,
    file: UploadFile = File(...),
    options: str | None = Form(None),
    title: str | None = Form(None),
):
    db = _db(request)
    settings = request.app.state.settings
    events = request.app.state.events

    if options:
        try:
            opts_data = json.loads(options)
        except json.JSONDecodeError as exc:
            raise HTTPException(422, f"options is not valid JSON: {exc}") from exc
        try:
            web_opts = WebOptions.model_validate(opts_data)
        except ValidationError as exc:
            raise HTTPException(422, exc.errors()) from exc
    else:
        web_opts = WebOptions()

    orig_name = file.filename or "upload"
    ext = _sanitize_ext(orig_name)
    item_title = (title or Path(orig_name).stem or "untitled").strip() or "untitled"

    item_id = db.new_id()
    while db.get(item_id) is not None:
        item_id = db.new_id()
    item_dir = settings.items_dir / item_id
    item_dir.mkdir(parents=True, exist_ok=True)
    dest = item_dir / f"original.{ext}"

    size = 0
    try:
        with open(dest, "wb") as f:
            while True:
                chunk = await file.read(UPLOAD_CHUNK)
                if not chunk:
                    break
                await anyio.to_thread.run_sync(f.write, chunk)
                size += len(chunk)
    finally:
        await file.close()

    if size == 0:
        shutil.rmtree(item_dir, ignore_errors=True)
        raise HTTPException(422, "uploaded file is empty")

    opts_dict = web_opts.model_dump()
    smoothing = jobs.smoothing_from_web_options(opts_dict)
    db.create(item_id=item_id, title=item_title, source_filename=orig_name,
              options=opts_dict, smoothing=smoothing)
    db.update_fts(item_id, filename=orig_name)

    request.app.state.prep_worker.enqueue(item_id)
    request.app.state.job_worker.enqueue(item_id)
    events.transcript(item_id, "created")

    return _row_to_summary(db.get(item_id), db)


# -- transcripts: detail / patch / delete ----------------------------------


@router.get("/transcripts/{item_id}")
def get_transcript(item_id: str, request: Request):
    db = _db(request)
    row = _row_or_404(db, item_id)
    return _row_to_detail(row, db)


@router.patch("/transcripts/{item_id}")
def patch_transcript(item_id: str, body: PatchBody, request: Request):
    db = _db(request)
    events = request.app.state.events
    _row_or_404(db, item_id)

    updates: dict = {}
    fts_updates: dict = {}
    if body.title is not None:
        updates["title"] = body.title
        fts_updates["title"] = body.title
    if body.notes is not None:
        updates["notes"] = body.notes
        fts_updates["notes"] = body.notes
    if body.tags is not None:
        tags = _normalize_tags(body.tags)
        db.set_tags(item_id, tags)
        fts_updates["tags"] = tags
    if body.speaker_names is not None:
        cleaned = {k: v for k, v in body.speaker_names.items() if v}
        updates["speaker_names_json"] = json.dumps(cleaned)

    if updates:
        db.update(item_id, **updates)
    if fts_updates:
        db.update_fts(item_id, **fts_updates)
    events.transcript(item_id, "updated")
    return _row_to_detail(db.get(item_id), db)


@router.delete("/transcripts/{item_id}", status_code=204)
def delete_transcript(item_id: str, request: Request):
    db = _db(request)
    events = request.app.state.events
    _row_or_404(db, item_id)

    row = db.get(item_id)
    if row["status"] == "processing":
        request.app.state.job_worker.cancel(item_id)
    db.delete(item_id)
    shutil.rmtree(_items_dir(request) / item_id, ignore_errors=True)
    events.transcript(item_id, "deleted")
    return Response(status_code=204)


# -- result / rerun / resmooth / cancel -----------------------------------


@router.get("/transcripts/{item_id}/result")
def get_result(item_id: str, request: Request):
    db = _db(request)
    _row_or_404(db, item_id)
    result_path = _items_dir(request) / item_id / "result.json"
    if not result_path.exists():
        raise HTTPException(404, "no result yet")

    json_bytes = result_path.read_bytes()
    etag = '"' + hashlib.sha1(json_bytes).hexdigest() + '"'
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers={"ETag": etag})

    headers = {"ETag": etag, "Cache-Control": "no-cache"}
    accept_encoding = request.headers.get("accept-encoding", "")
    gz_path = _items_dir(request) / item_id / "result.json.gz"
    if "gzip" in accept_encoding and gz_path.exists():
        headers["Content-Encoding"] = "gzip"
        headers["Vary"] = "Accept-Encoding"
        return Response(
            content=gz_path.read_bytes(), media_type="application/json", headers=headers
        )
    return Response(content=json_bytes, media_type="application/json", headers=headers)


@router.post("/transcripts/{item_id}/rerun", status_code=202)
def rerun(item_id: str, body: RerunBody, request: Request):
    db = _db(request)
    events = request.app.state.events
    row = _row_or_404(db, item_id)
    if row["status"] in ("queued", "processing"):
        raise HTTPException(409, "a job for this item is already queued or processing")

    opts_dict = body.options.model_dump()
    smoothing = jobs.smoothing_from_web_options(opts_dict)
    db.update(
        item_id,
        options_json=json.dumps(opts_dict),
        smoothing_json=json.dumps(smoothing),
        status="queued", stage="waiting", progress=0.0, error=None,
        speaker_names_json="{}",
    )
    request.app.state.job_worker.enqueue(item_id)
    events.transcript(item_id, "updated")
    return _row_to_summary(db.get(item_id), db)


@router.post("/transcripts/{item_id}/resmooth")
def resmooth(item_id: str, body: SmoothingBody, request: Request):
    db = _db(request)
    events = request.app.state.events
    _row_or_404(db, item_id)
    item_dir = _items_dir(request) / item_id
    raw_path = item_dir / "result.raw.json"
    if not raw_path.exists():
        raise HTTPException(404, "no result yet")

    smoothing = body.model_dump()
    result, json_text = results.build_smoothed(raw_path, smoothing)
    results.write_result_files(item_dir, json_text)
    db.update_fts(item_id, body=results.plain_text_body(result))
    db.update(
        item_id,
        smoothing_json=json.dumps(smoothing),
        stats_json=json.dumps(result.stats),
        speakers_json=json.dumps(result.speakers),
        rejected_count=len(result.rejections),
    )
    events.transcript(item_id, "updated")
    return _row_to_detail(db.get(item_id), db)


@router.post("/transcripts/{item_id}/cancel")
def cancel_transcript(item_id: str, request: Request):
    db = _db(request)
    events = request.app.state.events
    row = _row_or_404(db, item_id)

    if row["status"] == "queued":
        db.update(item_id, status="cancelled", stage=None, progress=None)
        events.job(item_id, status="cancelled", stage=None, progress=None, error=None)
        events.transcript(item_id, "updated")
    elif row["status"] == "processing":
        request.app.state.job_worker.cancel(item_id)
    return _row_to_summary(db.get(item_id), db)


# -- media -----------------------------------------------------------------


@router.get("/transcripts/{item_id}/media/audio")
def media_audio(item_id: str, request: Request):
    db = _db(request)
    row = _row_or_404(db, item_id)
    path = _items_dir(request) / item_id / "audio.m4a"
    if not row["audio_ready"] or not path.exists():
        raise HTTPException(404, "audio not ready")
    return FileResponse(
        path,
        media_type="audio/mp4",
        headers={"Cache-Control": "private, max-age=31536000, immutable"},
    )


@router.get("/transcripts/{item_id}/media/original")
def media_original(item_id: str, request: Request):
    db = _db(request)
    row = _row_or_404(db, item_id)
    item_dir = _items_dir(request) / item_id
    matches = sorted(item_dir.glob("original.*"))
    if not matches:
        raise HTTPException(404, "original not found")
    content_type = mimetypes.guess_type(row["source_filename"])[0] or "application/octet-stream"
    return FileResponse(matches[0], media_type=content_type)


# -- downloads ---------------------------------------------------------------


@router.get("/transcripts/{item_id}/download/{fmt}")
def download(item_id: str, fmt: str, request: Request):
    db = _db(request)
    row = _row_or_404(db, item_id)
    if fmt not in writers.WRITERS:
        raise HTTPException(404, f"unknown format {fmt!r}")

    result_path = _items_dir(request) / item_id / "result.json"
    if not result_path.exists():
        raise HTTPException(404, "no result yet")

    result = writers.load_result(result_path)
    speaker_names = json.loads(row["speaker_names_json"] or "{}")
    title = row["title"] or item_id
    text = results.render_download(result, fmt, title, speaker_names)

    ascii_name = _ascii_fallback(title, fmt)
    utf8_name = quote(f"{title}.{fmt}")
    headers = {
        "Content-Disposition": (
            f'attachment; filename="{ascii_name}"; filename*=UTF-8\'\'{utf8_name}'
        )
    }
    return Response(content=text, media_type=_FMT_MEDIA_TYPES[fmt], headers=headers)


# -- tags --------------------------------------------------------------------


@router.get("/tags")
def list_tags(request: Request):
    db = _db(request)
    return [{"tag": t, "count": n} for t, n in db.tag_counts()]


# -- events (SSE) --------------------------------------------------------


@router.get("/events")
async def events_stream(request: Request):
    bus = request.app.state.events
    loop = asyncio.get_running_loop()
    q = bus.subscribe(loop)

    async def gen():
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event, data = await asyncio.wait_for(q.get(), timeout=15)
                    yield f"event: {event}\ndata: {json.dumps(data)}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            bus.unsubscribe(loop, q)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
