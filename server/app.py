"""FastAPI application factory."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, PlainTextResponse

from .api import router as api_router
from .config import ensure_pyannote_token, load_settings
from .db import DB
from .events import EventBus
from .jobs import JobWorker, PrepWorker, recover

WEB_DIST = Path(__file__).resolve().parent.parent / "web" / "dist"


def create_app(data_dir: str | Path | None = None, engine_factory=None) -> FastAPI:
    settings = load_settings(data_dir)
    settings.items_dir.mkdir(parents=True, exist_ok=True)
    ensure_pyannote_token()

    db = DB(settings.db_path)
    event_bus = EventBus()
    prep_worker = PrepWorker(db, settings.items_dir, event_bus)
    job_worker = JobWorker(db, settings.items_dir, event_bus, engine_factory=engine_factory)

    recover(db, settings.items_dir, prep_worker, job_worker)

    prep_worker.start()
    job_worker.start()

    app = FastAPI(title="qstt")
    app.state.settings = settings
    app.state.db = db
    app.state.events = event_bus
    app.state.prep_worker = prep_worker
    app.state.job_worker = job_worker

    app.include_router(api_router, prefix="/api")

    if WEB_DIST.is_dir():
        @app.get("/{full_path:path}")
        async def spa(full_path: str):
            # A client-side ".." in the URL is normalized away before it ever
            # reaches us, so a mistyped /api/... path lands here looking like
            # a plain path -- never let the SPA fallback swallow it.
            if full_path == "api" or full_path.startswith("api/"):
                raise HTTPException(404, "not found")
            candidate = (WEB_DIST / full_path).resolve()
            try:
                candidate.relative_to(WEB_DIST.resolve())
            except ValueError:
                candidate = WEB_DIST / "index.html"
            if full_path and candidate.is_file():
                return FileResponse(candidate)
            return FileResponse(WEB_DIST / "index.html")
    else:
        @app.get("/")
        async def hint():
            return PlainTextResponse(
                "web/dist not found. Build the frontend first: cd web && npm run build"
            )

    return app
