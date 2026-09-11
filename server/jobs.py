"""Background workers: media preparation and the single-slot transcription queue."""

from __future__ import annotations

import json
import queue
import threading
import time
from pathlib import Path

from qstt import lang as langmod
from qstt.pipeline import DEFAULT_MODEL, Options, Transcriber

from . import media, results
from .db import DB
from .events import EventBus

FAST_MODEL = "Qwen/Qwen3-ASR-0.6B"

WEB_OPTION_DEFAULTS: dict = {
    "language": "auto",
    "diarize": False,
    "num_speakers": None,
    "fast": False,
    "context": "",
    "vad_threshold": 0.5,
    "min_speech_sec": 0.35,
    "aggressive_filler": False,
    "allow_foreign_ratio": 0.15,
    "max_repeats": 4,
    "smooth_max_words": 3,
    "smooth_max_dur": 1.2,
    "smooth_max_gap": 2.0,
}

LIMITS: dict[str, tuple[float, float]] = {
    "vad_threshold": (0.1, 0.95),
    "min_speech_sec": (0.0, 3.0),
    "allow_foreign_ratio": (0.0, 1.0),
    "max_repeats": (2, 20),
    "smooth_max_words": (0, 20),
    "smooth_max_dur": (0.0, 10.0),
    "smooth_max_gap": (0.0, 30.0),
}


class JobCancelled(Exception):
    """Raised from the progress callback to abandon a run between spans."""


def default_web_options() -> dict:
    return dict(WEB_OPTION_DEFAULTS)


def smoothing_from_web_options(opts: dict) -> dict:
    return {
        "max_words": opts.get("smooth_max_words", WEB_OPTION_DEFAULTS["smooth_max_words"]),
        "max_dur": opts.get("smooth_max_dur", WEB_OPTION_DEFAULTS["smooth_max_dur"]),
        "max_gap": opts.get("smooth_max_gap", WEB_OPTION_DEFAULTS["smooth_max_gap"]),
    }


def web_options_to_pipeline(opts: dict) -> Options:
    """WebOptions -> qstt.pipeline.Options, per docs/webui-contract.md.

    Language is constrained to {None, "English", "German"} no matter what --
    `langmod.normalize` never returns anything else, and the web `language`
    field itself is validated upstream to auto|en|de|mixed.
    """
    lang = opts.get("language", "auto")
    if lang == "auto":
        language, mixed = None, False
    elif lang == "mixed":
        language, mixed = None, True
    else:
        language, mixed = langmod.normalize(lang), False

    model = FAST_MODEL if opts.get("fast") else DEFAULT_MODEL
    return Options(
        model=model,
        language=language,
        mixed=mixed,
        diarize=bool(opts.get("diarize", False)),
        num_speakers=opts.get("num_speakers"),
        context=opts.get("context") or "",
        vad_threshold=float(opts.get("vad_threshold", WEB_OPTION_DEFAULTS["vad_threshold"])),
        min_speech_sec=float(opts.get("min_speech_sec", WEB_OPTION_DEFAULTS["min_speech_sec"])),
        aggressive_filler=bool(opts.get("aggressive_filler", False)),
        allow_foreign_ratio=float(
            opts.get("allow_foreign_ratio", WEB_OPTION_DEFAULTS["allow_foreign_ratio"])
        ),
        max_repeats=int(opts.get("max_repeats", WEB_OPTION_DEFAULTS["max_repeats"])),
        # Hard requirements: always timestamped, VAD always on, smoothing
        # deferred (stored separately, applied by server/results.py), rejected
        # segments always kept so the UI can show them.
        timestamps=True,
        vad=True,
        smooth=False,
        keep_rejected=True,
    )


# -- progress mapping ------------------------------------------------------
#
# decode -> 0.02, vad -> 0.05, language -> 0.08, diarize consumes 0.10..0.30
# (only when diarization is on), transcribe uses the span fraction for
# whatever range is left before finalize -> 0.99, then done -> 1.0.

_P_DECODE = 0.02
_P_VAD = 0.05
_P_LANGUAGE = 0.08
_P_DIARIZE_END = 0.30
_P_FINALIZE = 0.99
_P_DONE = 1.0


def _progress_fraction(stage: str, fraction: float | None, diarize: bool) -> float | None:
    if stage == "waiting":
        return 0.0
    if stage == "loading-model":
        return None
    if stage == "decode":
        return _P_DECODE
    if stage == "vad":
        return _P_VAD
    if stage == "language":
        return _P_LANGUAGE
    if stage == "diarize":
        return _P_DIARIZE_END
    if stage == "transcribe":
        start = _P_DIARIZE_END if diarize else _P_LANGUAGE
        span = _P_FINALIZE - start
        return start + span * (fraction if fraction is not None else 0.0)
    if stage == "finalize":
        return _P_FINALIZE
    if stage == "done":
        return _P_DONE
    return None


def find_original(item_dir: Path) -> Path:
    matches = sorted(Path(item_dir).glob("original.*"))
    if not matches:
        raise FileNotFoundError(f"no original file in {item_dir}")
    return matches[0]


# -- prep worker -------------------------------------------------------------


class PrepWorker(threading.Thread):
    """Probes the upload and builds the playback proxy. Independent of transcription."""

    def __init__(self, db: DB, items_dir: Path, events: EventBus):
        super().__init__(daemon=True, name="qstt-prep")
        self.db = db
        self.items_dir = Path(items_dir)
        self.events = events
        self._queue: queue.Queue[str] = queue.Queue()

    def enqueue(self, item_id: str) -> None:
        self._queue.put(item_id)

    def run(self) -> None:
        while True:
            item_id = self._queue.get()
            try:
                self._process(item_id)
            except Exception as exc:  # never let a bad file kill the worker
                try:
                    self.db.update(item_id, media_error=f"prep failed: {exc}")
                    self.events.transcript(item_id, "updated")
                except Exception:
                    pass

    def _process(self, item_id: str) -> None:
        row = self.db.get(item_id)
        if row is None:
            return
        item_dir = self.items_dir / item_id
        try:
            original = find_original(item_dir)
        except FileNotFoundError as exc:
            self.db.update(item_id, media_error=str(exc))
            self.events.transcript(item_id, "updated")
            return

        try:
            p = media.probe(original)
            self.db.update(
                item_id,
                media_kind=p.media_kind,
                playable_original=int(p.playable_original),
                duration=p.duration,
                media_error=None,
            )
            dst = item_dir / "audio.m4a"
            media.make_proxy(original, dst)
            self.db.update(item_id, audio_ready=1, audio_version=media.audio_version(dst))
        except media.MediaError as exc:
            self.db.update(item_id, media_error=str(exc))
        self.events.transcript(item_id, "updated")


# -- job worker ---------------------------------------------------------------


class JobWorker(threading.Thread):
    """FIFO, single-slot transcription queue. Keeps one loaded model, cached by id."""

    def __init__(
        self,
        db: DB,
        items_dir: Path,
        events: EventBus,
        engine_factory=None,
    ):
        super().__init__(daemon=True, name="qstt-job")
        self.db = db
        self.items_dir = Path(items_dir)
        self.events = events
        self.engine_factory = engine_factory or Transcriber
        self._queue: queue.Queue[str] = queue.Queue()
        self._engine = None
        self._engine_model: str | None = None
        self._lock = threading.Lock()
        self._current_id: str | None = None
        self._cancel_events: dict[str, threading.Event] = {}

    def enqueue(self, item_id: str) -> None:
        self._queue.put(item_id)

    def queue_size(self) -> int:
        return self._queue.qsize()

    def loaded_model(self) -> str | None:
        return self._engine_model

    def cancel(self, item_id: str) -> bool:
        """Request cancellation of the currently-processing item. No-op otherwise."""
        with self._lock:
            if item_id == self._current_id:
                ev = self._cancel_events.get(item_id)
                if ev is not None:
                    ev.set()
                return True
        return False

    def run(self) -> None:
        while True:
            item_id = self._queue.get()
            try:
                self._handle(item_id)
            except Exception as exc:  # one bad item must never stop the queue
                try:
                    msg = f"internal error: {exc}"
                    self.db.update(item_id, status="error", stage=None, progress=None, error=msg)
                    self._emit(item_id, status="error", stage=None, progress=None, error=msg)
                    self.events.transcript(item_id, "updated")
                except Exception:
                    pass

    def _emit(self, item_id: str, *, status: str, stage: str | None,
              progress: float | None, error: str | None) -> None:
        self.events.job(item_id, status=status, stage=stage, progress=progress, error=error)

    def _handle(self, item_id: str) -> None:
        row = self.db.get(item_id)
        # Deleted, cancelled while queued, or a stale duplicate entry: cancelling
        # a queued item leaves its id in the queue, and a rerun enqueues it again.
        if row is None or row["status"] != "queued":
            return

        item_dir = self.items_dir / item_id
        web_opts = json.loads(row["options_json"] or "{}")
        pipeline_opts = web_options_to_pipeline(web_opts)
        diarize = pipeline_opts.diarize

        cancel_event = threading.Event()
        with self._lock:
            self._current_id = item_id
            self._cancel_events[item_id] = cancel_event

        self.db.update(item_id, status="processing", stage="waiting", progress=0.0, error=None)
        self._emit(item_id, status="processing", stage="waiting", progress=0.0, error=None)
        self.events.transcript(item_id, "updated")

        state = {"stage": "waiting", "last_emit": time.monotonic()}

        def progress_cb(stage: str, fraction: float | None) -> None:
            if cancel_event.is_set():
                raise JobCancelled()
            frac = _progress_fraction(stage, fraction, diarize)
            self.db.update(item_id, stage=stage, progress=frac)
            now = time.monotonic()
            changed = stage != state["stage"]
            if changed or (now - state["last_emit"]) >= 0.25:
                self._emit(item_id, status="processing", stage=stage, progress=frac, error=None)
                state["last_emit"] = now
                state["stage"] = stage

        try:
            if self._engine is None or self._engine_model != pipeline_opts.model:
                self.db.update(item_id, stage="loading-model", progress=None)
                self._emit(item_id, status="processing", stage="loading-model",
                           progress=None, error=None)
                self._engine = None  # drop the old one before loading the new one
                self._engine = self.engine_factory(pipeline_opts)
                self._engine_model = pipeline_opts.model
            else:
                self._engine.opts = pipeline_opts

            original = find_original(item_dir)
            result = self._engine.run(original, progress=progress_cb)
        except JobCancelled:
            self.db.update(item_id, status="cancelled", stage=None, progress=None, error=None)
            self._emit(item_id, status="cancelled", stage=None, progress=None, error=None)
            self.events.transcript(item_id, "updated")
            return
        except Exception as exc:
            self.db.update(item_id, status="error", stage=None, progress=None, error=str(exc))
            self._emit(item_id, status="error", stage=None, progress=None, error=str(exc))
            self.events.transcript(item_id, "updated")
            return
        finally:
            with self._lock:
                self._current_id = None
                self._cancel_events.pop(item_id, None)

        self._finish_success(item_id, item_dir, result)

    def _finish_success(self, item_id: str, item_dir: Path, result) -> None:
        # Re-read: the item may have been deleted, or its smoothing changed,
        # while the model was running.
        row = self.db.get(item_id)
        if row is None or not item_dir.exists():
            return
        results.write_raw_result(item_dir, result)
        smoothing = json.loads(row["smoothing_json"] or "{}") or results.DEFAULT_SMOOTHING
        smoothed, json_text = results.build_smoothed(item_dir / "result.raw.json", smoothing)
        results.write_result_files(item_dir, json_text)
        self.db.update_fts(item_id, body=results.plain_text_body(smoothed))
        self.db.update(
            item_id,
            status="done", stage="done", progress=1.0, error=None,
            has_result=1,
            duration=smoothed.duration,
            language=smoothed.language,
            speakers_json=json.dumps(smoothed.speakers),
            stats_json=json.dumps(smoothed.stats),
            rejected_count=len(smoothed.rejections),
            speaker_names_json="{}",  # labels may change between runs
        )
        self._emit(item_id, status="done", stage="done", progress=1.0, error=None)
        self.events.transcript(item_id, "updated")


# -- startup recovery ---------------------------------------------------------


def recover(db: DB, items_dir: Path, prep_worker: PrepWorker, job_worker: JobWorker) -> None:
    """Resume after a restart: processing -> queued, re-enqueue queued work,
    and re-run prep for anything still missing its audio proxy."""
    db.reset_processing_to_queued()
    for item_id in db.queued_ids_oldest_first():
        job_worker.enqueue(item_id)
    items_dir = Path(items_dir)
    for item_id in db.all_ids():
        if not (items_dir / item_id / "audio.m4a").exists():
            prep_worker.enqueue(item_id)
