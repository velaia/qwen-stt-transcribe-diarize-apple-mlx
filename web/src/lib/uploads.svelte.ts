// Shared upload queue: files the user has picked/dropped but not yet
// confirmed (pending options), and uploads currently in flight (progress
// bars shown as rows in the Library until the job is queued).

import { uploadTranscript } from './api';
import type { Summary, WebOptions } from './types';

export interface PendingUpload {
  clientId: string;
  file: File;
  progress: number; // 0..1
  status: 'uploading' | 'done' | 'error';
  error: string | null;
  summary: Summary | null;
  abort: () => void;
}

class UploadsStore {
  /** Files picked/dropped, awaiting an options decision before sending. */
  staged = $state<File[]>([]);
  /** Uploads in flight or just finished, shown as rows until the library refetches. */
  active = $state<PendingUpload[]>([]);

  stage(files: File[]) {
    this.staged = [...this.staged, ...files];
  }

  clearStaged() {
    this.staged = [];
  }

  removeStaged(index: number) {
    this.staged = this.staged.filter((_, i) => i !== index);
  }

  start(file: File, options: WebOptions, title?: string) {
    const clientId = `${Date.now()}-${Math.random().toString(36).slice(2)}`;
    const entry: PendingUpload = {
      clientId,
      file,
      progress: 0,
      status: 'uploading',
      error: null,
      summary: null,
      abort: () => {}
    };
    this.active = [...this.active, entry];

    const { promise, abort } = uploadTranscript(
      file,
      { title, options },
      (fraction) => {
        entry.progress = fraction;
      }
    );
    entry.abort = abort;

    promise
      .then((summary) => {
        entry.status = 'done';
        entry.summary = summary;
        // Drop the row a moment after the real one should have arrived via
        // the transcripts list / SSE refetch.
        setTimeout(() => this.dismiss(clientId), 2500);
      })
      .catch((err) => {
        entry.status = 'error';
        entry.error = err instanceof Error ? err.message : String(err);
      });
  }

  dismiss(clientId: string) {
    this.active = this.active.filter((u) => u.clientId !== clientId);
  }
}

export const uploadsStore = new UploadsStore();
