/** The standup's sentences, pure functions of Brain state (split out so they can be checked without the app). */
import type { Item } from './attention';
import type { Standup } from './standup';
import type { Job } from './types';
import type { WorldId } from './worlds';

const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

/** One sentence about the focus thread, from its state and its jobs only. */
export function headline(s: Standup, showcase: boolean): string {
  const t = s.focus;
  if (!t) return 'Nothing is running yet.';
  const name = showcase ? 'Your agent' : t.title;
  const jobs = s.runningJobs.filter((j) => j.thread_id === t.id);
  const label = (j: Job) => (showcase ? 'a job' : j.label || j.name);
  if (t.state === 'needs_you') return `${name} needs you.`;
  if (t.state === 'paused') return 'Brain is paused.';
  if (t.state === 'running') return jobs.length ? `${name} is working while ${label(jobs[0])} runs.` : `${name} is working.`;
  if (jobs.length === 1) return `${name} is waiting on ${label(jobs[0])}.`;
  if (jobs.length > 1) return `${name} is waiting on ${jobs.length} jobs.`;
  return `${name} is resting.`;
}

/** Since-you-left counts from Brain, plus how much else is waiting. */
export function subline(s: Standup, showcase: boolean): string {
  const f = s.since.jobs_finished ?? 0;
  const x = s.since.jobs_failed ?? 0;
  const parts: string[] = [];
  if (showcase) parts.push(f || x ? 'Busy while you were away.' : 'Quiet while you were away.');
  else if (f || x) parts.push(`Since you left: ${plural(f, 'job')} finished${x ? `, ${x} failed` : ''}.`);
  else parts.push('Nothing finished since you left.');
  const n = actionable(s).length;
  if (n > 1) parts.push(`${n} things are waiting for you.`);
  else if (n === 1) parts.push('One thing is waiting for you.');
  return parts.join(' ');
}

/** What needs the owner to act (approvals, questions, failures, listening); news is the rest. */
export const actionable = (s: Standup) => s.items.filter((i) => i.tier <= 4);

const GENERIC: Record<Item['kind'], string> = { approval: 'An approval for you', needs: 'A thread needs you', failed: 'A job failed',
                                                listen: 'A listening test', news: 'A thread has news' };
export const itemTitle = (i: Item, showcase: boolean) => (showcase ? GENERIC[i.kind] : i.title);
export const itemDetail = (i: Item, showcase: boolean) => (showcase ? '' : i.detail);

/** The companion's briefing when tapped: its opener in its own voice, then a status line. Counts only, from Brain. */
export function briefing(world: WorldId, s: Standup, showcase: boolean): { intro: string; status: string } {
  const n = actionable(s).length;
  const r = s.runningJobs.length;
  const f = s.since.jobs_finished ?? 0;
  const status = showcase ? (r ? 'Jobs are running.' : 'No jobs running.')
    : [r ? `${plural(r, 'job')} running` : 'No jobs running', f ? `${f} finished since you left` : ''].filter(Boolean).join(' · ') + '.';
  const intro = {
    bakery: n ? `${plural(n, 'order')} up for you.` : 'No orders up. You are all caught up.',
    forest: n ? `I gathered ${plural(n, 'thing')} for you.` : 'Nothing to gather. All clear.',
    rustic: n ? `Mrrp. ${plural(n, 'note')} pinned for you.` : 'Mrrp. Nothing pinned. Back to my nap.',
    space: n ? `${plural(n, 'transmission')} incoming.` : 'No transmissions. All quiet out here.',
    tide: n ? `Blub. ${plural(n, 'bottle')} washed up for you.` : 'Blub. No bottles today.',
  }[world];
  return { intro, status };
}
