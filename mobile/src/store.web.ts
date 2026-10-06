/**
 * PWA local mirror: IndexedDB with the same API as store.ts (expo-sqlite). Events are keyed by
 * [thread_id, seq], so REST catch-up and WebSocket replay can overlap freely.
 */
import type { BrainEvent, Thread } from './types';

export { mergeEvents } from './merge';

let dbPromise: Promise<IDBDatabase> | null = null;

export function db(): Promise<IDBDatabase> {
  if (!dbPromise) {
    dbPromise = new Promise((resolve, reject) => {
      const req = indexedDB.open('brain', 1);
      req.onupgradeneeded = () => {
        const d = req.result;
        d.createObjectStore('threads', { keyPath: 'id' });
        d.createObjectStore('events', { keyPath: ['thread_id', 'seq'] }).createIndex('thread', 'thread_id');
        d.createObjectStore('meta');
      };
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => reject(req.error);
    });
  }
  return dbPromise;
}

function done(tx: IDBTransaction): Promise<void> {
  return new Promise((resolve, reject) => {
    tx.oncomplete = () => resolve();
    tx.onerror = tx.onabort = () => reject(tx.error);
  });
}

function result<T>(req: IDBRequest<T>): Promise<T> {
  return new Promise((resolve, reject) => {
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

export async function upsertThreads(threads: Thread[]): Promise<void> {
  if (!threads.length) return;
  const tx = (await db()).transaction('threads', 'readwrite');
  const st = tx.objectStore('threads');
  for (const t of threads) {
    const prev = (await result(st.get(t.id))) as Thread | undefined;
    st.put({ ...t, last_seq: Math.max(prev?.last_seq ?? 0, t.last_seq ?? 0) });
  }
  await done(tx);
}

export async function getThreads(includeArchived = false): Promise<Thread[]> {
  const all = (await result((await db()).transaction('threads').objectStore('threads').getAll())) as Thread[];
  return all
    .filter((t) => includeArchived || !t.archived)
    .sort((a, b) => String(b.updated_at ?? '').localeCompare(String(a.updated_at ?? '')));
}

export async function getThread(id: string): Promise<Thread | null> {
  return ((await result((await db()).transaction('threads').objectStore('threads').get(id))) as Thread) ?? null;
}

/** Inserts events, ignoring any (thread_id, seq) already stored. Returns how many were new. */
export async function upsertEvents(events: BrainEvent[]): Promise<number> {
  if (!events.length) return 0;
  const tx = (await db()).transaction('events', 'readwrite');
  const st = tx.objectStore('events');
  let added = 0;
  for (const e of events) {
    if ((await result(st.count([e.thread_id, e.seq]))) === 0) {
      st.put(e);
      added++;
    }
  }
  await done(tx);
  return added;
}

async function threadEvents(threadId: string): Promise<BrainEvent[]> {
  const ix = (await db()).transaction('events').objectStore('events').index('thread');
  return ((await result(ix.getAll(threadId))) as BrainEvent[]).sort((a, b) => a.seq - b.seq);
}

export async function getEvents(threadId: string, limit = 2000): Promise<BrainEvent[]> {
  return (await threadEvents(threadId)).slice(-limit); // newest `limit`, oldest-first
}

export async function lastSeq(threadId: string): Promise<number> {
  const evs = await threadEvents(threadId);
  return evs.length ? evs[evs.length - 1].seq : 0;
}

export async function lastSeqByThread(): Promise<Record<string, number>> {
  const all = (await result((await db()).transaction('events').objectStore('events').getAll())) as BrainEvent[];
  const out: Record<string, number> = {};
  for (const e of all) out[e.thread_id] = Math.max(out[e.thread_id] ?? 0, e.seq);
  return out;
}

export async function getMeta(k: string): Promise<string | null> {
  return ((await result((await db()).transaction('meta').objectStore('meta').get(k))) as string) ?? null;
}

export async function setMeta(k: string, v: string): Promise<void> {
  const tx = (await db()).transaction('meta', 'readwrite');
  tx.objectStore('meta').put(v, k);
  await done(tx);
}

export async function wipeLocal(): Promise<void> {
  const tx = (await db()).transaction(['threads', 'events', 'meta'], 'readwrite');
  for (const n of ['threads', 'events', 'meta']) tx.objectStore(n).clear();
  await done(tx);
}
