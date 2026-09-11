// One shared EventSource on /api/events. Exposes live job progress per id
// and notifies listeners when the transcript library should refetch.

import type { JobEvent, Status, Stage, TranscriptEvent } from './types';

class EventsStore {
  jobs = $state<Record<string, JobEvent>>({});
  private transcriptListeners = new Set<(evt: TranscriptEvent) => void>();
  private source: EventSource | null = null;

  connect() {
    if (this.source) return;
    const es = new EventSource('/api/events');
    this.source = es;

    es.addEventListener('job', (e) => {
      try {
        const data = JSON.parse((e as MessageEvent).data) as JobEvent;
        this.jobs[data.id] = data;
      } catch {
        // ignore malformed event
      }
    });

    es.addEventListener('transcript', (e) => {
      try {
        const data = JSON.parse((e as MessageEvent).data) as TranscriptEvent;
        for (const fn of this.transcriptListeners) fn(data);
      } catch {
        // ignore malformed event
      }
    });

    es.onerror = () => {
      // EventSource auto-reconnects; nothing to do here.
    };
  }

  onTranscriptEvent(fn: (evt: TranscriptEvent) => void): () => void {
    this.transcriptListeners.add(fn);
    return () => this.transcriptListeners.delete(fn);
  }

  jobFor(id: string): JobEvent | undefined {
    return this.jobs[id];
  }

  /** Merge a freshly-fetched status/stage/progress onto the live map so a
   * page that loaded after the job started still shows something sane until
   * the next SSE tick. */
  seed(id: string, status: Status, stage: Stage, progress: number | null, error: string | null) {
    if (!this.jobs[id]) {
      this.jobs[id] = { id, status, stage, progress, error };
    }
  }
}

export const eventsStore = new EventsStore();
