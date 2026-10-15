/** The standup's sentences, pure functions of Brain state (split out so they can be checked without the app). */
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
  if (s.waitingCount > 1) parts.push(`${s.waitingCount} things are waiting for you.`);
  else if (s.waitingCount === 1) parts.push('One thing is waiting for you.');
  return parts.join(' ');
}

/** What the companion says when tapped: counts only. */
export function petLine(world: WorldId, s: Standup, showcase: boolean): string {
  const n = s.runningJobs.length;
  const jobs = showcase ? (n ? 'Jobs are running' : 'No jobs running') : n ? `${plural(n, 'job')} running` : 'No jobs running';
  const wait = s.waitingCount ? ' Something is waiting for you.' : '';
  switch (world) {
    case 'bakery':
      return (n ? `${showcase ? 'Something' : plural(n, 'job')} in the oven.` : 'Nothing in the oven.') + (s.waitingCount ? ' Your order is up.' : '');
    case 'forest':
      return `${jobs}. No findings to stash yet.${wait}`;
    case 'rustic':
      return `Mrrp. ${jobs}.${wait}`;
    case 'space':
      return `${showcase ? 'My arms are lit' : `${plural(Math.min(8, n), 'arm')} lit`}: ${jobs.toLowerCase()}.${wait}`;
    case 'tide':
      return `Blub. ${jobs}.${s.waitingCount ? ' A bottle washed up for you.' : ''}`;
  }
}
