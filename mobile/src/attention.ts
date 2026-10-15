/**
 * What needs the owner, ranked: one list feeds the waiting card (its top item) and the companion's briefing (all of it).
 * Ranks follow what blocks the owner's agents: an approval, a thread asking for you, jobs that failed since you left,
 * a fresh listening test, then threads with news. Built only from Brain state; "Later" is a per-phone snooze that
 * decides nothing on Brain.
 */
import type { Job, Pending, Thread } from './types';

export type Tier = 1 | 2 | 3 | 4 | 5;
export type Item = { key: string; tier: Tier; kind: 'approval' | 'needs' | 'failed' | 'listen' | 'news'; title: string; detail: string; href: string; at: number };
export type Attention = { items: Item[]; backlog: { listen: number } };
export type AttentionInput = { pending: Pending; threads: Thread[]; jobs: Job[]; now: number; snoozed: Record<string, number> };

export const LISTEN_FRESH_MS = 48 * 3600_000; // older tests are usually superseded: the inbox keeps them, the banner does not
export const SNOOZE_MS = 4 * 3600_000;
const FAILED = ['FAILED', 'TIMEOUT', 'OUT_OF_MEMORY', 'NODE_FAIL'];
const ms = (v: string | number | undefined | null) => (typeof v === 'number' ? (v < 1e12 ? v * 1000 : v) : Date.parse(v ?? '') || 0);
const enc = encodeURIComponent;

function reason(j: Job): string {
  if (j.state === 'OUT_OF_MEMORY') return 'out of memory';
  if (j.state === 'TIMEOUT') return 'hit its time limit';
  if (j.state === 'NODE_FAIL') return 'node failure';
  return j.exit_code != null ? `exit ${j.exit_code}` : 'failed';
}

export function attention({ pending, threads, jobs, now, snoozed }: AttentionInput): Attention {
  const since = ms(pending.since.ts) || now - 12 * 3600_000;
  const title = new Map(threads.map((t) => [t.id, t.title]));
  const items: Item[] = [];

  for (const a of [...pending.approvals].sort((x, y) => ms(x.created_at) - ms(y.created_at)))
    items.push({ key: `approval:${a.id}`, tier: 1, kind: 'approval', title: a.decision_summary?.verb ?? a.summary,
                 detail: title.get(a.thread_id) ?? 'A thread', href: `/approval/${enc(a.id)}`, at: ms(a.created_at) });

  for (const t of pending.threads_needing_you)
    if (!pending.approvals.some((a) => a.thread_id === t.id))
      items.push({ key: `needs:${t.id}:${t.last_seq}`, tier: 2, kind: 'needs', title: `${t.title} needs you`, detail: 'Open the thread',
                   href: `/thread/${enc(t.id)}`, at: ms(t.updated_at) });

  const failed = new Map<string, Job[]>();
  for (const j of jobs)
    if (j.thread_id && FAILED.includes(j.state) && ms(j.updated_at) > since) failed.set(j.thread_id, [...(failed.get(j.thread_id) ?? []), j]);
  const groups = [...failed.values()].map((g) => g.sort((x, y) => ms(y.updated_at) - ms(x.updated_at)));
  for (const g of groups.sort((x, y) => ms(y[0].updated_at) - ms(x[0].updated_at))) {
    const j = g[0];
    const where = j.thread_title ?? title.get(j.thread_id!) ?? 'A thread';
    items.push({ key: `failed:${j.thread_id}:${j.job_id}`, tier: 3, kind: 'failed',
                 title: g.length === 1 ? `${j.label || j.name} failed` : `${g.length} jobs failed in ${where}`,
                 detail: g.length === 1 ? `${where} · ${reason(j)}` : `Latest: ${reason(j)}`, href: `/thread/${enc(j.thread_id!)}`, at: ms(j.updated_at) });
  }

  const tests = [...(pending.listening ?? [])].sort((x, y) => ms(y.created_at) - ms(x.created_at));
  const fresh = tests.filter((l) => now - ms(l.created_at) <= LISTEN_FRESH_MS);
  for (const l of fresh)
    items.push({ key: `listen:${l.id}`, tier: 4, kind: 'listen', title: `Listen: ${l.title}`,
                 detail: `${l.n_items} ${l.n_items === 1 ? 'sentence' : 'sentences'}, blind · ${title.get(l.thread_id) ?? 'a thread'}`,
                 href: `/listen/${enc(l.id)}`, at: ms(l.created_at) });

  const listed = new Set([...pending.approvals.map((a) => a.thread_id), ...pending.threads_needing_you.map((t) => t.id), ...failed.keys(),
                          ...fresh.map((l) => l.thread_id)]);
  for (const t of [...threads].sort((x, y) => ms(y.updated_at) - ms(x.updated_at)))
    if (ms(t.updated_at) > since && t.state !== 'running' && t.state !== 'needs_you' && !listed.has(t.id))
      items.push({ key: `news:${t.id}:${t.last_seq}`, tier: 5, kind: 'news', title: `${t.title} has news`, detail: 'Updated since you left',
                   href: `/thread/${enc(t.id)}`, at: ms(t.updated_at) });

  return { items: items.filter((i) => !(snoozed[i.key] > now)), backlog: { listen: tests.length - fresh.length } };
}

/** Hide one item on this phone for SNOOZE_MS; expired snoozes are dropped. */
export function snooze(snoozed: Record<string, number>, key: string, now: number): Record<string, number> {
  const keep = Object.fromEntries(Object.entries(snoozed).filter(([, until]) => until > now));
  return { ...keep, [key]: now + SNOOZE_MS };
}
