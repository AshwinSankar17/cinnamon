/**
 * The standup's data: which thread the roll is, what is waiting for the owner, and the sentences on screen. Every
 * sentence is built from Brain state (thread state, jobs, pending items); nothing is invented (spec §2 "verified only").
 * The last good standup is cached so the home still renders, marked "as of", when Brain is unreachable.
 */
import { api } from './api';
import type { Activity } from './roll';
import { getMeta, setMeta } from './store';
import type { Job, Since, Thread } from './types';
import type { WorldId } from './worlds';

export type Waiting =
  | { kind: 'approval'; id: string; title: string; detail: string }
  | { kind: 'listen'; id: string; title: string; detail: string }
  | { kind: 'thread'; id: string; title: string; detail: string };

export type Standup = {
  focus: Thread | null;
  threads: Thread[];
  waiting: Waiting | null;
  waitingCount: number;
  runningJobs: Job[];
  since: Since;
  activity: Activity | null;
  asOf: number;
  stale?: boolean;
};

const ACTIVE = /pending|running|configuring|completing|requeued|suspended/i;
const ts = (v: string | number) => (typeof v === 'number' ? (v < 1e12 ? v * 1000 : v) : Date.parse(v) || 0);

function pickFocus(threads: Thread[], jobs: Job[]): Thread | null {
  const order = (t: Thread) =>
    t.state === 'needs_you' ? 0 : t.state === 'running' ? 1 : jobs.some((j) => j.thread_id === t.id) ? 2 : t.state === 'waiting' ? 3 : 4;
  return [...threads].sort((a, b) => order(a) - order(b) || ts(b.updated_at) - ts(a.updated_at))[0] ?? null;
}

export { headline, subline, petLine } from './standup-text';

export async function loadStandup(leftAtMs: number | null): Promise<Standup> {
  try {
    const [pending, threads] = await Promise.all([api.pending(leftAtMs), api.threads({ archived: 0 })]);
    const runningJobs = pending.jobs.filter((j) => ACTIVE.test(j.state));
    const focus = pickFocus(threads, runningJobs);
    const recent = [...threads].sort((a, b) => ts(b.updated_at) - ts(a.updated_at));
    const shown = focus ? [focus, ...recent.filter((t) => t.id !== focus.id)].slice(0, 3) : recent.slice(0, 3);
    const byId = new Map(threads.map((t) => [t.id, t]));
    const items: Waiting[] = [
      ...pending.approvals.map((a): Waiting => ({ kind: 'approval', id: a.id, title: a.decision_summary?.verb ?? a.summary,
        detail: byId.get(a.thread_id)?.title ?? 'A thread' })),
      ...(pending.listening ?? []).map((l): Waiting => ({ kind: 'listen', id: l.id, title: `Listen: ${l.title}`,
        detail: `${l.n_items} ${l.n_items === 1 ? 'sentence' : 'sentences'}, blind` })),
      ...pending.threads_needing_you
        .filter((t) => !pending.approvals.some((a) => a.thread_id === t.id))
        .map((t): Waiting => ({ kind: 'thread', id: t.id, title: `${t.title} needs you`, detail: 'Open the thread' })),
    ];
    const activity = focus ? await api.activity(focus.id, leftAtMs).catch(() => null) : null;
    const s: Standup = { focus, threads: shown, waiting: items[0] ?? null, waitingCount: items.length, runningJobs,
                         since: pending.since, activity, asOf: Date.now() };
    setMeta('standup_cache', JSON.stringify(s)).catch(() => undefined);
    return s;
  } catch (e) {
    const cached = await getMeta('standup_cache').catch(() => null);
    if (cached) return { ...(JSON.parse(cached) as Standup), stale: true };
    throw e;
  }
}
