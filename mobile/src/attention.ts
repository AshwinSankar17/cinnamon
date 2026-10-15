/**
 * The app's side of what needs the owner. Brain ranks it (`attention` on /v1/pending, runtime/brain/core.py): approval,
 * failed turn, jobs failed since the thread was last opened, listening test, unread reply. The home's waiting card,
 * the companion's briefing and the inbox all read that one list. "Later" is the only thing decided here: a
 * per-phone snooze that hides an item for four hours and decides nothing on Brain.
 */
export type Tier = 1 | 2 | 3 | 4 | 5;
export type Item = {
  key: string; tier: Tier; kind: 'approval' | 'needs' | 'failed' | 'listen' | 'news';
  thread_id: string; target: string; title: string; detail: string; at: number;
};

export const SNOOZE_MS = 4 * 3600_000;

export function hrefOf(i: Item): string {
  const id = encodeURIComponent(i.target);
  return i.kind === 'approval' ? `/approval/${id}` : i.kind === 'listen' ? `/listen/${id}` : `/thread/${id}`;
}

/** Brain's list minus what this phone said "Later" to. */
export const visible = (items: Item[] | undefined, snoozed: Record<string, number>, now: number): Item[] =>
  (items ?? []).filter((i) => !(snoozed[i.key] > now));

/** Hide one item on this phone for SNOOZE_MS; expired snoozes are dropped. */
export function snooze(snoozed: Record<string, number>, key: string, now: number): Record<string, number> {
  const keep = Object.fromEntries(Object.entries(snoozed).filter(([, until]) => until > now));
  return { ...keep, [key]: now + SNOOZE_MS };
}
