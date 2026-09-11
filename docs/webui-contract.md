# Web UI contract

Shared contract between the backend (`server/`, Python/FastAPI) and the frontend
(`web/`, Svelte 5 + Vite). Both sides build against this document; if something
here turns out to be wrong, change this file first and say so.

## Runtime layout

- One process: `./serve.py` → uvicorn on `0.0.0.0:8765` (LAN, **no auth** by
  decision). It serves the API under `/api` and the built SPA from `web/dist/`
  (any non-`/api` path that is not a file falls back to `index.html`).
- Frontend dev: `cd web && npm run dev` (Vite on 5173) proxies `/api` → `http://127.0.0.1:8765`.
- Data dir `data/` (override with `QSTT_DATA`):

```
data/qstt.db                      SQLite, WAL
data/items/<id>/original.<ext>    the upload, untouched
data/items/<id>/audio.m4a         playback proxy: AAC mono 64 kbps, +faststart
data/items/<id>/result.raw.json   pipeline output, speakers attached, NOT smoothed
data/items/<id>/result.json       smoothed version served to the UI (derived, rebuildable)
```

## Hard rules (inherited from the CLI)

- Languages are only ever English or German. The UI offers `auto | en | de | mixed`, nothing else.
- VAD is always on. There is no UI control to disable it.
- One transcription job runs at a time (FIFO). Parallel decoding was measured
  not to help on this machine.

## Conventions

- IDs: 10 chars `[a-z0-9]`. Any other id → 404 (never touches the filesystem).
- Timestamps: ISO 8601 UTC strings (`2026-09-11T14:03:22Z`).
- Durations/times: seconds as floats.
- Errors: FastAPI default `{"detail": "human readable message"}` with a 4xx/5xx code.
- JSON field names are snake_case.

## Types

```ts
type Status = "queued" | "processing" | "done" | "error" | "cancelled";

// Pipeline stages, in order. "diarize" only when diarization is on.
type Stage = "waiting" | "loading-model" | "decode" | "vad" | "language"
           | "diarize" | "transcribe" | "finalize" | "done" | null;

interface Summary {
  id: string;
  title: string;
  source_filename: string;        // original upload name
  media_kind: "audio" | "video" | null;   // null until probed
  playable_original: boolean;     // original is mp4/m4v/mov/webm with video -> "Show video" toggle
  audio_ready: boolean;           // audio.m4a exists
  audio_version: string | null;   // opaque; changes iff audio.m4a changes. Cache key for IndexedDB.
  media_error: string | null;     // probe/proxy failure message
  duration: number | null;
  language: string | null;        // "English" | "German" once a result exists
  speakers: string[];             // raw labels, e.g. ["SPEAKER_00", "SPEAKER_01"]
  status: Status;
  stage: Stage;
  progress: number | null;        // overall 0..1 for the job, null when unknown
  error: string | null;
  has_result: boolean;            // true while a rerun is in flight if an older result exists
  tags: string[];
  created_at: string;
  updated_at: string;
}

interface Smoothing { max_words: number; max_dur: number; max_gap: number; }

interface Detail extends Summary {
  notes: string;
  options: WebOptions;            // options of the current/last job
  speaker_names: Record<string, string>;  // raw label -> display name
  smoothing: Smoothing;
  stats: Record<string, unknown>; // result.stats (vad, speech_ratio, reject_reasons, ...)
  rejected_count: number;
}

interface WebOptions {
  language: "auto" | "en" | "de" | "mixed";
  diarize: boolean;
  num_speakers: number | null;    // null = let diarization decide
  fast: boolean;                  // 0.6B model
  context: string;                // domain vocabulary
  vad_threshold: number;          // 0.1..0.95, default 0.5
  min_speech_sec: number;         // 0..3, default 0.35
  aggressive_filler: boolean;     // default false
  allow_foreign_ratio: number;    // 0..1, default 0.15
  max_repeats: number;            // 2..20, default 4
  smooth_max_words: number;       // 0..20, default 3
  smooth_max_dur: number;         // 0..10, default 1.2
  smooth_max_gap: number;         // 0..30, default 2.0
}
```

Essentials shown on the upload form: title, language, speakers (off / auto /
exact count). Everything else lives in a collapsed **Advanced** section.
Any omitted option takes its default; unknown keys → 422.

### Mapping WebOptions → `qstt.pipeline.Options`

| Web | Options |
|---|---|
| `language: "auto"` | `language=None, mixed=False` |
| `language: "en" / "de"` | `language=langmod.normalize(...)` |
| `language: "mixed"` | `language=None, mixed=True` |
| `fast: true` | `model="Qwen/Qwen3-ASR-0.6B"` else `DEFAULT_MODEL` |
| `smooth_*` | stored as the item's `smoothing`, **not** passed to the pipeline |
| everything else | same name |
| always | `timestamps=True, vad=True, smooth=False, keep_rejected=True` |

## Result JSON (`GET /api/transcripts/{id}/result`)

Exactly the shape of `qstt.writers.to_json` (see a real one in
`transcripts_diarized/*.json`), after smoothing, **with raw speaker labels**
(the frontend maps labels through `speaker_names` for display):

```ts
interface Result {
  source: string; duration_sec: number; language: string; speakers: string[];
  stats: Record<string, unknown>;
  segments: { start: number; end: number; speaker: string | null; language: string;
              text: string; words: { text: string; start: number; end: number; speaker?: string }[] }[];
  rejected: { start: number; end: number; text: string; reason: string }[];
}
```

`words` can be empty (e.g. alignment failed); the UI then falls back to
segment-level highlighting. Word `text` is a bare token without punctuation;
the segment `text` is the punctuated source. The frontend renders segment text
and maps word tokens onto it in order (case-insensitive forward search, same
idea as `qstt.pipeline._map_to_source`).

Served gzip-compressed with a strong `ETag`; `If-None-Match` → 304.
404 while no result exists yet.

## Endpoints

| Method | Path | Body | Response |
|---|---|---|---|
| GET | `/api/health` | | `{ok: true, model_loaded: string\|null, queue: number, diarization_available: boolean}` |
| GET | `/api/options` | | `{defaults: WebOptions, languages: [{value, label}], limits: {field: [min, max]}, diarization_available: boolean}` |
| GET | `/api/transcripts?q=&tag=` | | `Summary[]`, newest first. `q` = FTS5 prefix search over title, notes, tags, filename, transcript text |
| POST | `/api/transcripts` | multipart: `file` (required), `options` (JSON string, optional), `title` (optional; default = filename stem) | `201 Summary` (status `queued`) |
| GET | `/api/transcripts/{id}` | | `Detail` |
| PATCH | `/api/transcripts/{id}` | `{title?, notes?, tags?: string[], speaker_names?: Record<string,string>}` | `Detail`. Tags are trimmed, lower-cased, deduped, max 32 chars. `speaker_names` replaces the map; empty-string values are dropped |
| DELETE | `/api/transcripts/{id}` | | `204`. Cancels a running job, removes the row and folder |
| GET | `/api/transcripts/{id}/result` | | `Result` (see above) |
| POST | `/api/transcripts/{id}/rerun` | `{options: WebOptions}` | `202 Summary`. Keeps the old result until the new one finishes, then swaps atomically. Resets `speaker_names` (labels may change between runs). `409` if a job for this item is already queued/processing |
| POST | `/api/transcripts/{id}/resmooth` | `Smoothing` | `Detail`. Synchronous (<1 s); rebuilds `result.json` from `result.raw.json`. Keeps `speaker_names` |
| POST | `/api/transcripts/{id}/cancel` | | `Summary` |
| GET | `/api/transcripts/{id}/media/audio` | | `audio/mp4`. Honors `Range` (206). `Cache-Control: private, max-age=31536000, immutable`. 404 until `audio_ready` |
| GET | `/api/transcripts/{id}/media/original` | | Original file with its content type. Honors `Range` |
| GET | `/api/transcripts/{id}/download/{fmt}` | `fmt` ∈ `txt, md, srt, vtt, json` | Rendered by `qstt.writers` with `speaker_names` applied and `source` set to the title. `Content-Disposition: attachment` with an RFC 5987 `filename*` of `<title>.<fmt>` |
| GET | `/api/tags` | | `[{tag, count}]` sorted by count desc |
| GET | `/api/events` | | SSE stream (below) |

## Server-sent events (`GET /api/events`)

```
event: job
data: {"id":"...","status":"processing","stage":"transcribe","progress":0.42,"error":null}

event: transcript
data: {"id":"...","action":"created" | "updated" | "deleted"}
```

- `job` fires on every status/stage change and at most ~4×/s for progress.
- `transcript` fires whenever a Summary field changes (upload, media ready,
  job finished, patch, delete). The library refetches the list on it.
- A `: keepalive` comment every 15 s. Clients use `EventSource` (auto-reconnect).

## Media and caching (frontend)

- Play from `/api/transcripts/{id}/media/audio` immediately (Range streaming).
- In the background, fetch the whole file into **IndexedDB** keyed by
  `${id}:${audio_version}` (the Cache API and Service Workers are unavailable
  on a plain-http LAN origin). Swap the element's `src` to the blob URL only
  while paused, preserving `currentTime`. LRU cap 1 GB; a "clear cache" control.
- Deep link: `#/t/{id}?t=123.4` opens the transcript at that time.
