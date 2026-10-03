/**
 * Local SQLite mirror (expo-sqlite). Events are keyed by (thread_id, seq), so REST catch-up and
 * WebSocket replay can overlap freely: duplicates are dropped on insert and in mergeEvents().
 */
import * as SQLite from 'expo-sqlite';
import type { BrainEvent, Thread } from './types';

let dbPromise: Promise<SQLite.SQLiteDatabase> | null = null;

const SCHEMA = `
PRAGMA journal_mode = WAL;
CREATE TABLE IF NOT EXISTS threads (
  id TEXT PRIMARY KEY NOT NULL,
  repo_id TEXT NOT NULL,
  title TEXT NOT NULL,
  state TEXT NOT NULL,
  provider TEXT NOT NULL,
  pinned INTEGER NOT NULL DEFAULT 0,
  archived INTEGER NOT NULL DEFAULT 0,
  muted INTEGER NOT NULL DEFAULT 0,
  last_seq INTEGER NOT NULL DEFAULT 0,
  updated_at TEXT,
  json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
  thread_id TEXT NOT NULL,
  seq INTEGER NOT NULL,
  type TEXT NOT NULL,
  turn_id TEXT,
  ts TEXT,
  json TEXT NOT NULL,
  PRIMARY KEY (thread_id, seq)
);
CREATE TABLE IF NOT EXISTS meta (
  k TEXT PRIMARY KEY NOT NULL,
  v TEXT
);
`;

export function db(): Promise<SQLite.SQLiteDatabase> {
  if (!dbPromise) {
    dbPromise = (async () => {
      const d = await SQLite.openDatabaseAsync('brain.db');
      await d.execAsync(SCHEMA);
      return d;
    })();
  }
  return dbPromise;
}

// ---------------------------------------------------------------------------------------------
// Threads

export async function upsertThreads(threads: Thread[]): Promise<void> {
  if (!threads.length) return;
  const d = await db();
  await d.withExclusiveTransactionAsync(async (tx) => {
    for (const t of threads) {
      await tx.runAsync(
        `INSERT INTO threads (id, repo_id, title, state, provider, pinned, archived, muted, last_seq, updated_at, json)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
         ON CONFLICT(id) DO UPDATE SET repo_id=excluded.repo_id, title=excluded.title, state=excluded.state,
           provider=excluded.provider, pinned=excluded.pinned, archived=excluded.archived, muted=excluded.muted,
           last_seq=MAX(threads.last_seq, excluded.last_seq), updated_at=excluded.updated_at, json=excluded.json`,
        [
          t.id,
          t.repo_id,
          t.title,
          t.state,
          t.provider,
          t.pinned ? 1 : 0,
          t.archived ? 1 : 0,
          t.muted ? 1 : 0,
          t.last_seq ?? 0,
          String(t.updated_at ?? ''),
          JSON.stringify(t),
        ],
      );
    }
  });
}

export async function getThreads(includeArchived = false): Promise<Thread[]> {
  const d = await db();
  const rows = await d.getAllAsync<{ json: string }>(
    `SELECT json FROM threads ${includeArchived ? '' : 'WHERE archived = 0'} ORDER BY updated_at DESC`,
  );
  return rows.map((r) => JSON.parse(r.json) as Thread);
}

export async function getThread(id: string): Promise<Thread | null> {
  const d = await db();
  const r = await d.getFirstAsync<{ json: string }>('SELECT json FROM threads WHERE id = ?', [id]);
  return r ? (JSON.parse(r.json) as Thread) : null;
}

// ---------------------------------------------------------------------------------------------
// Events

/** Inserts events, ignoring any (thread_id, seq) already stored. Returns how many were new. */
export async function upsertEvents(events: BrainEvent[]): Promise<number> {
  if (!events.length) return 0;
  const d = await db();
  let added = 0;
  await d.withExclusiveTransactionAsync(async (tx) => {
    for (const e of events) {
      const r = await tx.runAsync(
        'INSERT OR IGNORE INTO events (thread_id, seq, type, turn_id, ts, json) VALUES (?, ?, ?, ?, ?, ?)',
        [e.thread_id, e.seq, e.type, e.turn_id ?? null, String(e.ts ?? ''), JSON.stringify(e)],
      );
      added += r.changes;
    }
  });
  return added;
}

export async function getEvents(threadId: string, limit = 2000): Promise<BrainEvent[]> {
  const d = await db();
  // newest `limit` events, returned oldest-first
  const rows = await d.getAllAsync<{ json: string }>(
    'SELECT json FROM (SELECT seq, json FROM events WHERE thread_id = ? ORDER BY seq DESC LIMIT ?) ORDER BY seq ASC',
    [threadId, limit],
  );
  return rows.map((r) => JSON.parse(r.json) as BrainEvent);
}

/** Highest contiguous-or-not seq we hold for a thread (0 if none). */
export async function lastSeq(threadId: string): Promise<number> {
  const d = await db();
  const r = await d.getFirstAsync<{ m: number | null }>('SELECT MAX(seq) AS m FROM events WHERE thread_id = ?', [
    threadId,
  ]);
  return r?.m ?? 0;
}

export async function lastSeqByThread(): Promise<Record<string, number>> {
  const d = await db();
  const rows = await d.getAllAsync<{ thread_id: string; m: number }>(
    'SELECT thread_id, MAX(seq) AS m FROM events GROUP BY thread_id',
  );
  return Object.fromEntries(rows.map((r) => [r.thread_id, r.m]));
}

/** In-memory merge used by screens: dedupe by (thread_id, seq), keep seq order. */
export function mergeEvents(existing: BrainEvent[], incoming: BrainEvent[]): BrainEvent[] {
  if (!incoming.length) return existing;
  const map = new Map<string, BrainEvent>();
  for (const e of existing) map.set(`${e.thread_id}:${e.seq}`, e);
  let changed = false;
  for (const e of incoming) {
    const k = `${e.thread_id}:${e.seq}`;
    if (!map.has(k)) {
      map.set(k, e);
      changed = true;
    }
  }
  if (!changed) return existing;
  return [...map.values()].sort((a, b) => a.seq - b.seq);
}

// ---------------------------------------------------------------------------------------------
// Meta (small key/value: last-active time for "since you left", etc.)

export async function getMeta(k: string): Promise<string | null> {
  const d = await db();
  const r = await d.getFirstAsync<{ v: string | null }>('SELECT v FROM meta WHERE k = ?', [k]);
  return r?.v ?? null;
}

export async function setMeta(k: string, v: string): Promise<void> {
  const d = await db();
  await d.runAsync('INSERT INTO meta (k, v) VALUES (?, ?) ON CONFLICT(k) DO UPDATE SET v = excluded.v', [k, v]);
}

export async function wipeLocal(): Promise<void> {
  const d = await db();
  await d.execAsync('DELETE FROM threads; DELETE FROM events; DELETE FROM meta;');
}
