// Typed client for the /api surface described in docs/webui-contract.md.

import type {
  Detail,
  DownloadFormat,
  HealthResponse,
  OptionsResponse,
  Result,
  Smoothing,
  Summary,
  TagCount,
  WebOptions
} from './types';

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function asJson<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      if (body && typeof body.detail === 'string') detail = body.detail;
    } catch {
      // ignore — keep statusText
    }
    throw new ApiError(res.status, detail);
  }
  return (await res.json()) as T;
}

export function health(): Promise<HealthResponse> {
  return fetch('/api/health').then((res) => asJson<HealthResponse>(res));
}

export function getOptions(): Promise<OptionsResponse> {
  return fetch('/api/options').then((res) => asJson<OptionsResponse>(res));
}

export function listTranscripts(q?: string, tag?: string): Promise<Summary[]> {
  const params = new URLSearchParams();
  if (q) params.set('q', q);
  if (tag) params.set('tag', tag);
  const qs = params.toString();
  return fetch(`/api/transcripts${qs ? `?${qs}` : ''}`).then((res) => asJson<Summary[]>(res));
}

export function getTranscript(id: string): Promise<Detail> {
  return fetch(`/api/transcripts/${id}`).then((res) => asJson<Detail>(res));
}

export function getResult(id: string): Promise<Result> {
  return fetch(`/api/transcripts/${id}/result`).then((res) => asJson<Result>(res));
}

export function patchTranscript(
  id: string,
  patch: { title?: string; notes?: string; tags?: string[]; speaker_names?: Record<string, string> }
): Promise<Detail> {
  return fetch(`/api/transcripts/${id}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(patch)
  }).then((res) => asJson<Detail>(res));
}

export function deleteTranscript(id: string): Promise<void> {
  return fetch(`/api/transcripts/${id}`, { method: 'DELETE' }).then((res) => {
    if (!res.ok && res.status !== 204) throw new ApiError(res.status, res.statusText);
  });
}

export function rerun(id: string, options: WebOptions): Promise<Summary> {
  return fetch(`/api/transcripts/${id}/rerun`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ options })
  }).then((res) => asJson<Summary>(res));
}

export function resmooth(id: string, smoothing: Smoothing): Promise<Detail> {
  return fetch(`/api/transcripts/${id}/resmooth`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(smoothing)
  }).then((res) => asJson<Detail>(res));
}

export function cancel(id: string): Promise<Summary> {
  return fetch(`/api/transcripts/${id}/cancel`, { method: 'POST' }).then((res) => asJson<Summary>(res));
}

export function tags(): Promise<TagCount[]> {
  return fetch('/api/tags').then((res) => asJson<TagCount[]>(res));
}

export function mediaAudioUrl(id: string): string {
  return `/api/transcripts/${id}/media/audio`;
}

export function mediaOriginalUrl(id: string): string {
  return `/api/transcripts/${id}/media/original`;
}

export function downloadUrl(id: string, fmt: DownloadFormat): string {
  return `/api/transcripts/${id}/download/${fmt}`;
}

/**
 * Upload via XHR (not fetch) so we get upload progress events.
 */
export function uploadTranscript(
  file: File,
  opts: { title?: string; options?: WebOptions },
  onProgress?: (fraction: number) => void
): { promise: Promise<Summary>; abort: () => void } {
  const xhr = new XMLHttpRequest();
  const form = new FormData();
  form.append('file', file);
  if (opts.title) form.append('title', opts.title);
  if (opts.options) form.append('options', JSON.stringify(opts.options));

  const promise = new Promise<Summary>((resolve, reject) => {
    xhr.open('POST', '/api/transcripts');
    xhr.upload.onprogress = (evt) => {
      if (evt.lengthComputable && onProgress) onProgress(evt.loaded / evt.total);
    };
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        try {
          resolve(JSON.parse(xhr.responseText));
        } catch (e) {
          reject(e);
        }
      } else {
        let detail = xhr.statusText;
        try {
          const body = JSON.parse(xhr.responseText);
          if (body && typeof body.detail === 'string') detail = body.detail;
        } catch {
          // ignore
        }
        reject(new ApiError(xhr.status, detail));
      }
    };
    xhr.onerror = () => reject(new ApiError(0, 'Network error during upload'));
    xhr.onabort = () => reject(new ApiError(0, 'Upload cancelled'));
    xhr.send(form);
  });

  return { promise, abort: () => xhr.abort() };
}
