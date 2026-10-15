/**
 * The standup's data: which thread the roll is, what is waiting for the owner, and the sentences on screen. Every
 * sentence is built from Brain state (thread state, jobs, pending items); nothing is invented (spec §2 "verified only").
 * The last good standup is cached so the home still renders, marked "as of", when Brain is unreachable.
 */
import { api } from './api';
import { snooze, visible, type Item } from './attention';
import type { Activity } from './roll';
import { getMeta, setMeta } from './store';
import type { Job, Since, Thread } from './types';
import type { WorldId } from './worlds';

export type Standup = {
  focus: Thread | null;
  threads: Thread[];
  items: Item[]; // Brain's ranked list minus "Later": items[0] is the waiting card, the list is the companion's briefing
  runningJobs: Job[];
  since: Since;
  activity: Activity | null;
  asOf: number;
  stale?: boolean;
};

const ACTIVE = /pending|running|configuring|completing|requeued|suspended/i;
const ts = (v: string | number) => (typeof v === 'number' ? (v < 1e12 ? v * 1000 : v) : Date.parse(v) || 0);

/** The roll is the thread of the top item Brain ranked (so the headline and the waiting card agree), else the working one. */
function pickFocus(threads: Thread[], jobs: Job[], top: Item | undefined): Thread | null {
  const ranked = top && threads.find((t) => t.id === top.thread_id);
  if (ranked) return ranked;
  const order = (t: Thread) =>
    t.state === 'needs_you' ? 0 : t.state === 'running' ? 1 : jobs.some((j) => j.thread_id === t.id) ? 2 : t.state === 'waiting' ? 3 : 4;
  return [...threads].sort((a, b) => order(a) - order(b) || ts(b.updated_at) - ts(a.updated_at))[0] ?? null;
}

export { headline, subline, briefing, actionable, itemTitle, itemDetail } from './standup-text';

export async function loadStandup(leftAtMs: number | null): Promise<Standup> {
  try {
    const [pending, threads, snoozed] = await Promise.all([api.pending(leftAtMs), api.threads({ archived: 0 }), getSnoozed()]);
    const runningJobs = pending.jobs.filter((j) => ACTIVE.test(j.state));
    const items = visible(pending.attention, snoozed, Date.now());
    const focus = pickFocus(threads, runningJobs, items[0]);
    const recent = [...threads].sort((a, b) => ts(b.updated_at) - ts(a.updated_at));
    const shown = focus ? [focus, ...recent.filter((t) => t.id !== focus.id)].slice(0, 3) : recent.slice(0, 3);
    const activity = focus ? await api.activity(focus.id, leftAtMs).catch(() => null) : null;
    const s: Standup = { focus, threads: shown, items, runningJobs,
                         since: pending.since, activity, asOf: Date.now() };
    setMeta('standup_cache', JSON.stringify(s)).catch(() => undefined);
    return s;
  } catch (e) {
    const cached = await getMeta('standup_cache').catch(() => null);
    if (cached) {
      const c = JSON.parse(cached) as Standup; // a cache from before the ranking has no items
      return { ...c, items: c.items ?? [], stale: true };
    }
    throw e;
  }
}

/** "Later": hide one item on this phone for four hours. Decides nothing on Brain. */
export async function later(key: string): Promise<void> {
  await setMeta('snoozed', JSON.stringify(snooze(await getSnoozed(), key, Date.now())));
}

/** Bring back everything this phone set aside. */
export const clearSnoozed = (): Promise<void> => setMeta('snoozed', '{}');

export const getSnoozed = (): Promise<Record<string, number>> =>
  getMeta('snoozed').then((v) => (v ? JSON.parse(v) : {})).catch(() => ({}));
