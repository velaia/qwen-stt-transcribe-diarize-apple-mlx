// Hand-written IndexedDB blob cache for audio playback. No idb library, no
// Cache API / Service Worker (unavailable on a plain-http LAN origin).
//
// Every IndexedDB call is wrapped in try/catch: on any failure we simply keep
// playing from the network. Caching is a nice-to-have, never a dependency.

const DB_NAME = 'qstt-media-cache';
const DB_VERSION = 1;
const STORE = 'blobs';
const MAX_BYTES = 1024 * 1024 * 1024; // 1 GB LRU cap

interface CacheRecord {
  key: string; // `${id}:${audio_version}`
  blob: Blob;
  size: number;
  lastUsed: number;
}

let dbPromise: Promise<IDBDatabase> | null = null;

function openDb(): Promise<IDBDatabase> {
  if (dbPromise) return dbPromise;
  dbPromise = new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, DB_VERSION);
    req.onupgradeneeded = () => {
      const db = req.result;
      if (!db.objectStoreNames.contains(STORE)) {
        const store = db.createObjectStore(STORE, { keyPath: 'key' });
        store.createIndex('lastUsed', 'lastUsed');
      }
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
  return dbPromise;
}

export function cacheKey(id: string, audioVersion: string | null): string {
  return `${id}:${audioVersion ?? 'none'}`;
}

export async function getCachedBlob(key: string): Promise<Blob | null> {
  try {
    const db = await openDb();
    return await new Promise<Blob | null>((resolve, reject) => {
      const tx = db.transaction(STORE, 'readwrite');
      const store = tx.objectStore(STORE);
      const getReq = store.get(key);
      getReq.onsuccess = () => {
        const rec = getReq.result as CacheRecord | undefined;
        if (!rec) {
          resolve(null);
          return;
        }
        rec.lastUsed = Date.now();
        store.put(rec);
        resolve(rec.blob);
      };
      getReq.onerror = () => reject(getReq.error);
    });
  } catch {
    return null;
  }
}

export async function hasCachedBlob(key: string): Promise<boolean> {
  try {
    const db = await openDb();
    return await new Promise<boolean>((resolve, reject) => {
      const tx = db.transaction(STORE, 'readonly');
      const req = tx.objectStore(STORE).count(key);
      req.onsuccess = () => resolve(req.result > 0);
      req.onerror = () => reject(req.error);
    });
  } catch {
    return false;
  }
}

async function currentTotalBytes(db: IDBDatabase): Promise<number> {
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE, 'readonly');
    const req = tx.objectStore(STORE).getAll();
    req.onsuccess = () => {
      const records = req.result as CacheRecord[];
      resolve(records.reduce((sum, r) => sum + r.size, 0));
    };
    req.onerror = () => reject(req.error);
  });
}

async function evictLru(db: IDBDatabase, neededBytes: number): Promise<void> {
  let total = await currentTotalBytes(db);
  if (total + neededBytes <= MAX_BYTES) return;

  await new Promise<void>((resolve, reject) => {
    const tx = db.transaction(STORE, 'readwrite');
    const store = tx.objectStore(STORE);
    const index = store.index('lastUsed');
    const cursorReq = index.openCursor();
    cursorReq.onsuccess = () => {
      const cursor = cursorReq.result;
      if (!cursor || total + neededBytes <= MAX_BYTES) {
        resolve();
        return;
      }
      const rec = cursor.value as CacheRecord;
      total -= rec.size;
      cursor.delete();
      cursor.continue();
    };
    cursorReq.onerror = () => reject(cursorReq.error);
    tx.oncomplete = () => resolve();
  });
}

/**
 * Fetch the whole media file and store it, reporting progress by reading the
 * response body stream against Content-Length. Resolves with the blob, or
 * null on any failure (network or IndexedDB) — caller should just keep
 * playing from the network in that case.
 */
export async function fetchAndCache(
  url: string,
  key: string,
  onProgress?: (fraction: number) => void
): Promise<Blob | null> {
  try {
    const res = await fetch(url);
    if (!res.ok || !res.body) return null;
    const total = Number(res.headers.get('Content-Length') ?? '0');
    const reader = res.body.getReader();
    const chunks: Uint8Array[] = [];
    let received = 0;

    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      if (value) {
        chunks.push(value);
        received += value.length;
        if (total > 0 && onProgress) onProgress(received / total);
      }
    }

    const blob = new Blob(chunks as BlobPart[], { type: res.headers.get('Content-Type') ?? 'audio/mp4' });

    try {
      const db = await openDb();
      await evictLru(db, blob.size);
      await new Promise<void>((resolve, reject) => {
        const tx = db.transaction(STORE, 'readwrite');
        tx.objectStore(STORE).put({ key, blob, size: blob.size, lastUsed: Date.now() } satisfies CacheRecord);
        tx.oncomplete = () => resolve();
        tx.onerror = () => reject(tx.error);
      });
    } catch {
      // Storage failed (quota, private mode, etc). We still have the blob in
      // memory for this playback session even if we couldn't persist it.
    }

    return blob;
  } catch {
    return null;
  }
}

export async function clearAll(): Promise<void> {
  try {
    const db = await openDb();
    await new Promise<void>((resolve, reject) => {
      const tx = db.transaction(STORE, 'readwrite');
      tx.objectStore(STORE).clear();
      tx.oncomplete = () => resolve();
      tx.onerror = () => reject(tx.error);
    });
  } catch {
    // ignore
  }
}

export async function cacheUsage(): Promise<{ bytes: number; count: number }> {
  try {
    const db = await openDb();
    return await new Promise((resolve, reject) => {
      const tx = db.transaction(STORE, 'readonly');
      const req = tx.objectStore(STORE).getAll();
      req.onsuccess = () => {
        const records = req.result as CacheRecord[];
        resolve({ bytes: records.reduce((s, r) => s + r.size, 0), count: records.length });
      };
      req.onerror = () => reject(req.error);
    });
  } catch {
    return { bytes: 0, count: 0 };
  }
}
