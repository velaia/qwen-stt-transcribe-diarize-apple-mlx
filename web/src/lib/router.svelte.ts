// Tiny hash router: #/ (library), #/t/{id} (transcript), optional ?t=seconds.

export type Route =
  | { name: 'library' }
  | { name: 'transcript'; id: string; t: number | null };

function parseHash(hash: string): Route {
  const raw = hash.replace(/^#/, '') || '/';
  const [path, query] = raw.split('?');
  const parts = path.split('/').filter(Boolean);

  if (parts[0] === 't' && parts[1]) {
    let t: number | null = null;
    if (query) {
      const params = new URLSearchParams(query);
      const raw = params.get('t');
      if (raw !== null) {
        const n = Number(raw);
        if (Number.isFinite(n)) t = n;
      }
    }
    return { name: 'transcript', id: parts[1], t };
  }

  return { name: 'library' };
}

class Router {
  route = $state<Route>(parseHash(typeof location !== 'undefined' ? location.hash : ''));

  constructor() {
    if (typeof window !== 'undefined') {
      window.addEventListener('hashchange', () => {
        this.route = parseHash(location.hash);
      });
    }
  }

  goLibrary() {
    location.hash = '#/';
  }

  goTranscript(id: string, t?: number) {
    location.hash = t != null ? `#/t/${id}?t=${t}` : `#/t/${id}`;
  }

  /** Update the ?t= param without pushing a new history-visible navigation
   * (used while seeking, so the URL stays a shareable deep link). */
  setTime(id: string, t: number) {
    history.replaceState(null, '', `#/t/${id}?t=${t.toFixed(1)}`);
  }
}

export const router = new Router();
