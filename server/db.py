"""SQLite catalog: transcript metadata, tags, and full-text search.

stdlib sqlite3 in WAL mode, one connection shared across threads (routes run
in FastAPI's threadpool; PrepWorker and JobWorker are their own threads). A
single lock serializes every statement -- simplest correct thing on a
single-writer, low-concurrency workload like this one.
"""

from __future__ import annotations

import json
import re
import secrets
import sqlite3
import threading
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

ID_ALPHABET = "abcdefghijklmnopqrstuvwxyz0123456789"
ID_RE = re.compile(r"^[a-z0-9]{10}$")

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS transcripts (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    notes TEXT NOT NULL DEFAULT '',
    source_filename TEXT NOT NULL,
    media_kind TEXT,
    playable_original INTEGER NOT NULL DEFAULT 0,
    audio_ready INTEGER NOT NULL DEFAULT 0,
    audio_version TEXT,
    media_error TEXT,
    duration REAL,
    language TEXT,
    speakers_json TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'queued',
    stage TEXT,
    progress REAL,
    error TEXT,
    has_result INTEGER NOT NULL DEFAULT 0,
    options_json TEXT NOT NULL DEFAULT '{}',
    speaker_names_json TEXT NOT NULL DEFAULT '{}',
    smoothing_json TEXT NOT NULL DEFAULT '{}',
    stats_json TEXT NOT NULL DEFAULT '{}',
    rejected_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tags (
    transcript_id TEXT NOT NULL,
    tag TEXT NOT NULL,
    PRIMARY KEY (transcript_id, tag)
);
CREATE INDEX IF NOT EXISTS idx_tags_tag ON tags(tag);

CREATE VIRTUAL TABLE IF NOT EXISTS transcripts_fts USING fts5(
    transcript_id UNINDEXED,
    title, notes, tags, filename, body,
    tokenize = "unicode61 remove_diacritics 2"
);
"""


def new_id() -> str:
    return "".join(secrets.choice(ID_ALPHABET) for _ in range(10))


def valid_id(item_id: str) -> bool:
    return bool(ID_RE.match(item_id or ""))


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def fold(text: str | None) -> str:
    """Fold text for search: case, diacritics and eszett all collapse away.

    SQLite's unicode61 `remove_diacritics 2` tokenizer folds accented Latin
    letters but not every build folds ß -> ss, so umlaut/eszett-insensitive
    search ("Grüße" found via "grusse" or "grüße") is guaranteed here instead,
    by normalizing both indexed text and query terms through the same
    function before FTS ever sees them.
    """
    if not text:
        return ""
    text = text.replace("ß", "ss").replace("ẞ", "SS")
    text = unicodedata.normalize("NFKD", text)
    return "".join(c for c in text if not unicodedata.combining(c))


class DB:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._migrate()

    # -- schema ------------------------------------------------------------

    def _migrate(self) -> None:
        with self._lock:
            version = self._conn.execute("PRAGMA user_version").fetchone()[0]
            if version < 1:
                self._conn.executescript(_SCHEMA)
                self._conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
                self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def new_id(self) -> str:
        return new_id()

    # -- transcripts ---------------------------------------------------------

    def create(
        self,
        *,
        item_id: str,
        title: str,
        source_filename: str,
        options: dict,
        smoothing: dict,
    ) -> sqlite3.Row:
        ts = now_iso()
        with self._lock:
            self._conn.execute(
                """INSERT INTO transcripts
                   (id, title, notes, source_filename, status, stage, progress,
                    options_json, smoothing_json, created_at, updated_at)
                   VALUES (?, ?, '', ?, 'queued', 'waiting', 0.0, ?, ?, ?, ?)""",
                (item_id, title, source_filename, json.dumps(options),
                 json.dumps(smoothing), ts, ts),
            )
            self._conn.execute(
                "INSERT INTO transcripts_fts (transcript_id, title, notes, tags, filename, body) "
                "VALUES (?, ?, '', '', ?, '')",
                (item_id, fold(title), fold(source_filename)),
            )
            self._conn.commit()
            return self.get(item_id)

    def get(self, item_id: str) -> sqlite3.Row | None:
        with self._lock:
            return self._conn.execute(
                "SELECT * FROM transcripts WHERE id = ?", (item_id,)
            ).fetchone()

    def list(self, *, ids: set[str] | None = None, tag: str | None = None) -> list[sqlite3.Row]:
        with self._lock:
            sql = "SELECT * FROM transcripts"
            clauses = []
            params: list = []
            if tag:
                clauses.append(
                    "id IN (SELECT transcript_id FROM tags WHERE tag = ?)"
                )
                params.append(tag)
            if ids is not None:
                if not ids:
                    return []
                placeholders = ",".join("?" * len(ids))
                clauses.append(f"id IN ({placeholders})")
                params.extend(ids)
            if clauses:
                sql += " WHERE " + " AND ".join(clauses)
            sql += " ORDER BY created_at DESC, rowid DESC"
            return self._conn.execute(sql, params).fetchall()

    def update(self, item_id: str, **fields) -> None:
        if not fields:
            return
        fields["updated_at"] = now_iso()
        cols = ", ".join(f"{k} = ?" for k in fields)
        with self._lock:
            self._conn.execute(
                f"UPDATE transcripts SET {cols} WHERE id = ?",
                (*fields.values(), item_id),
            )
            self._conn.commit()

    def delete(self, item_id: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM transcripts WHERE id = ?", (item_id,))
            self._conn.execute("DELETE FROM tags WHERE transcript_id = ?", (item_id,))
            self._conn.execute(
                "DELETE FROM transcripts_fts WHERE transcript_id = ?", (item_id,)
            )
            self._conn.commit()

    # -- tags ----------------------------------------------------------------

    def set_tags(self, item_id: str, tags: list[str]) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM tags WHERE transcript_id = ?", (item_id,))
            self._conn.executemany(
                "INSERT OR IGNORE INTO tags (transcript_id, tag) VALUES (?, ?)",
                [(item_id, t) for t in tags],
            )
            self._conn.commit()

    def get_tags(self, item_id: str) -> list[str]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT tag FROM tags WHERE transcript_id = ? ORDER BY tag", (item_id,)
            ).fetchall()
            return [r["tag"] for r in rows]

    def tag_counts(self) -> list[tuple[str, int]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT tag, COUNT(*) as n FROM tags GROUP BY tag ORDER BY n DESC, tag ASC"
            ).fetchall()
            return [(r["tag"], r["n"]) for r in rows]

    # -- full text search ------------------------------------------------

    def update_fts(
        self,
        item_id: str,
        *,
        title: str | None = None,
        notes: str | None = None,
        tags: list[str] | None = None,
        filename: str | None = None,
        body: str | None = None,
    ) -> None:
        """Merge-update one or more indexed fields for an item."""
        with self._lock:
            row = self._conn.execute(
                "SELECT title, notes, tags, filename, body FROM transcripts_fts "
                "WHERE transcript_id = ?",
                (item_id,),
            ).fetchone()
            current = dict(row) if row else {
                "title": "", "notes": "", "tags": "", "filename": "", "body": ""
            }
            if title is not None:
                current["title"] = fold(title)
            if notes is not None:
                current["notes"] = fold(notes)
            if tags is not None:
                current["tags"] = fold(" ".join(tags))
            if filename is not None:
                current["filename"] = fold(filename)
            if body is not None:
                current["body"] = fold(body)
            self._conn.execute(
                "DELETE FROM transcripts_fts WHERE transcript_id = ?", (item_id,)
            )
            self._conn.execute(
                "INSERT INTO transcripts_fts "
                "(transcript_id, title, notes, tags, filename, body) VALUES (?, ?, ?, ?, ?, ?)",
                (item_id, current["title"], current["notes"], current["tags"],
                 current["filename"], current["body"]),
            )
            self._conn.commit()

    def search_ids(self, q: str) -> set[str]:
        """FTS5 prefix search: AND of quoted-and-starred tokens. Never raw q in MATCH."""
        tokens = [t for t in fold(q).split() if t]
        if not tokens:
            return set()
        match_expr = " AND ".join('"' + t.replace('"', '""') + '"*' for t in tokens)
        with self._lock:
            rows = self._conn.execute(
                "SELECT transcript_id FROM transcripts_fts WHERE transcripts_fts MATCH ?",
                (match_expr,),
            ).fetchall()
            return {r["transcript_id"] for r in rows}

    # -- job recovery ------------------------------------------------------

    def reset_processing_to_queued(self) -> list[str]:
        """Startup recovery: a job mid-flight when the process died goes back to queued."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT id FROM transcripts WHERE status = 'processing' ORDER BY created_at ASC"
            ).fetchall()
            ids = [r["id"] for r in rows]
            if ids:
                self._conn.executemany(
                    "UPDATE transcripts SET status='queued', stage='waiting', progress=0.0, "
                    "updated_at=? WHERE id=?",
                    [(now_iso(), i) for i in ids],
                )
                self._conn.commit()
            return ids

    def queued_ids_oldest_first(self) -> list[str]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id FROM transcripts WHERE status = 'queued' ORDER BY created_at ASC"
            ).fetchall()
            return [r["id"] for r in rows]

    def all_ids(self) -> list[str]:
        with self._lock:
            rows = self._conn.execute("SELECT id FROM transcripts").fetchall()
            return [r["id"] for r in rows]
