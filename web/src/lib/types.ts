// Mirrors docs/webui-contract.md. Keep field names snake_case to match the API.

export type Status = 'queued' | 'processing' | 'done' | 'error' | 'cancelled';

export type Stage =
  | 'waiting'
  | 'loading-model'
  | 'decode'
  | 'vad'
  | 'language'
  | 'diarize'
  | 'transcribe'
  | 'finalize'
  | 'done'
  | null;

export interface Summary {
  id: string;
  title: string;
  source_filename: string;
  media_kind: 'audio' | 'video' | null;
  playable_original: boolean;
  audio_ready: boolean;
  audio_version: string | null;
  media_error: string | null;
  duration: number | null;
  language: string | null;
  speakers: string[];
  status: Status;
  stage: Stage;
  progress: number | null;
  error: string | null;
  has_result: boolean;
  tags: string[];
  created_at: string;
  updated_at: string;
}

export interface Smoothing {
  max_words: number;
  max_dur: number;
  max_gap: number;
}

export interface WebOptions {
  language: 'auto' | 'en' | 'de' | 'mixed';
  diarize: boolean;
  num_speakers: number | null;
  fast: boolean;
  context: string;
  vad_threshold: number;
  min_speech_sec: number;
  aggressive_filler: boolean;
  allow_foreign_ratio: number;
  max_repeats: number;
  smooth_max_words: number;
  smooth_max_dur: number;
  smooth_max_gap: number;
}

export interface Detail extends Summary {
  notes: string;
  options: WebOptions;
  speaker_names: Record<string, string>;
  smoothing: Smoothing;
  stats: Record<string, unknown>;
  rejected_count: number;
}

export interface Word {
  text: string;
  start: number;
  end: number;
  speaker?: string;
}

export interface Segment {
  start: number;
  end: number;
  speaker: string | null;
  language: string;
  text: string;
  words: Word[];
}

export interface Rejected {
  start: number;
  end: number;
  text: string;
  reason: string;
}

export interface Result {
  source: string;
  duration_sec: number;
  language: string;
  speakers: string[];
  stats: Record<string, unknown>;
  segments: Segment[];
  rejected: Rejected[];
}

export interface OptionsResponse {
  defaults: WebOptions;
  languages: { value: string; label: string }[];
  limits: Record<string, [number, number]>;
  diarization_available: boolean;
}

export interface HealthResponse {
  ok: true;
  model_loaded: string | null;
  queue: number;
  diarization_available: boolean;
}

export interface TagCount {
  tag: string;
  count: number;
}

export type DownloadFormat = 'txt' | 'md' | 'srt' | 'vtt' | 'json';

export interface JobEvent {
  id: string;
  status: Status;
  stage: Stage;
  progress: number | null;
  error: string | null;
}

export interface TranscriptEvent {
  id: string;
  action: 'created' | 'updated' | 'deleted';
}
