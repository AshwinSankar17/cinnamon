import type { BrainEvent } from './types';

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
