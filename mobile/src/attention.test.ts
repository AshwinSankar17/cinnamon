/** Self-check for the attention ranking: `npm test`. Plain node:assert, no framework. */
import assert from 'node:assert/strict';
import { attention, snooze, LISTEN_FRESH_MS, SNOOZE_MS } from './attention';
import type { Approval, Job, ListeningSummary, Pending, Thread } from './types';

const NOW = 1_800_000_000_000; // ms
const SINCE = NOW / 1000 - 6 * 3600; // left six hours ago (seconds, as Brain sends it)
const sec = (agoMin: number) => NOW / 1000 - agoMin * 60;
const thread = (id: string, state: Thread['state'], agoMin: number): Thread => ({
  id, repo_id: 'r', title: `T ${id}`, mode: 'readonly' as Thread['mode'], branch: null, provider: 'claude' as Thread['provider'], state,
  pinned: false, archived: false, muted: false, parent_id: null, fork_turn: null, last_seq: 1, updated_at: sec(agoMin) });
const approval = (id: string, tid: string, agoMin: number): Approval => ({
  id, thread_id: tid, action_type: 'shell', summary: `do ${id}`, decision_summary: { verb: `Allow ${id}` } as Approval['decision_summary'],
  state: 'pending', created_at: sec(agoMin), decided_at: null });
const listen = (id: string, tid: string, agoMin: number): ListeningSummary => ({ id, thread_id: tid, title: id, n_items: 4, n_systems: 2, created_at: sec(agoMin) });
const job = (id: string, tid: string, state: string, agoMin: number, exit: number | null = 1): Job => ({
  job_id: id, thread_id: tid, name: `brain.${id}`, label: `train_${id}.sh`, thread_title: `T ${tid}`, state, gpus: 1, elapsed: 60,
  time_limit: null, exit_code: exit, submitted_at: sec(agoMin + 10), updated_at: sec(agoMin) });
const pending = (p: Partial<Pending>): Pending => ({ approvals: [], waits: [], jobs: [], threads_needing_you: [], listening: [], since: { ts: SINCE }, ...p });
const run = (p: Partial<Pending>, threads: Thread[] = [], jobs: Job[] = [], snoozed: Record<string, number> = {}) =>
  attention({ pending: pending(p), threads, jobs, now: NOW, snoozed });

// the order is by what blocks the owner's agents, whatever order Brain lists things in
{
  const threads = [thread('a', 'needs_you', 5), thread('b', 'needs_you', 5), thread('c', 'idle', 30), thread('d', 'idle', 20), thread('e', 'idle', 10)];
  const r = run({ listening: [listen('L1', 'd', 40)], threads_needing_you: [threads[1]], approvals: [approval('A1', 'a', 50)] },
                threads, [job('J1', 'c', 'FAILED', 30)]);
  assert.deepEqual(r.items.map((i) => i.kind), ['approval', 'needs', 'failed', 'listen', 'news']);
  assert.deepEqual(r.items.map((i) => i.tier), [1, 2, 3, 4, 5]);
  assert.equal(r.items[0].href, '/approval/A1');
  assert.equal(r.items[0].title, 'Allow A1');
  assert.equal(r.items[1].href, '/thread/b');
  assert.equal(r.items[4].href, '/thread/e', 'the thread with only news is the news item');
}
// approvals: the longest-blocked first; listening tests: the newest first
{
  const r = run({ approvals: [approval('new', 'a', 5), approval('old', 'a', 90)], listening: [listen('older', 'a', 300), listen('newer', 'a', 30)] });
  assert.deepEqual(r.items.map((i) => i.key), ['approval:old', 'approval:new', 'listen:newer', 'listen:older']);
}
// listening tests past 48 h leave the banner and the list, and are counted as backlog
{
  const r = run({ listening: [listen('fresh', 'a', 60), listen('stale', 'a', LISTEN_FRESH_MS / 60000 + 1), listen('stale2', 'a', 9 * 24 * 60)] });
  assert.deepEqual(r.items.map((i) => i.key), ['listen:fresh']);
  assert.equal(r.backlog.listen, 2);
}
// a thread that needs you because of an approval shows once, as the approval
{
  const t = thread('a', 'needs_you', 5);
  const r = run({ approvals: [approval('A1', 'a', 5)], threads_needing_you: [t] }, [t]);
  assert.deepEqual(r.items.map((i) => i.key), ['approval:A1']);
}
// failures since you left are grouped per thread, name the reason, and re-surface on a new failure
{
  const jobs = [job('J1', 'c', 'FAILED', 30, 1), job('J2', 'c', 'OUT_OF_MEMORY', 20), job('J0', 'c', 'FAILED', 8 * 60), job('J3', 'd', 'COMPLETED', 10),
                job('J4', 'e', 'TIMEOUT', 15), job('J5', 'f', 'CANCELLED', 15)];
  const r = run({}, [], jobs);
  assert.deepEqual(r.items.map((i) => i.key), ['failed:e:J4', 'failed:c:J2']);
  assert.match(r.items[0].title, /train_J4\.sh failed/);
  assert.match(r.items[0].detail, /time limit/);
  assert.match(r.items[1].title, /2 jobs failed/);
  assert.match(r.items[1].detail, /out of memory/);
  assert.equal(r.items[1].href, '/thread/c');
}
// news: only threads updated since you left, not running, not already listed
{
  const threads = [thread('old', 'idle', 7 * 60), thread('run', 'running', 5), thread('new', 'idle', 30), thread('newer', 'waiting', 10)];
  const r = run({}, threads);
  assert.deepEqual(r.items.map((i) => i.key), ['news:newer:1', 'news:new:1']);
}
// "Later" hides an item for four hours on this phone, then it comes back
{
  const p = { approvals: [approval('A1', 'a', 5)], listening: [listen('L1', 'a', 5)] };
  const s = snooze({}, 'approval:A1', NOW);
  assert.equal(s['approval:A1'], NOW + SNOOZE_MS);
  assert.deepEqual(run(p, [], [], s).items.map((i) => i.key), ['listen:L1']);
  assert.deepEqual(attention({ pending: pending(p), threads: [], jobs: [], now: NOW + SNOOZE_MS + 1, snoozed: s }).items.map((i) => i.key), ['approval:A1', 'listen:L1']);
  assert.deepEqual(Object.keys(snooze(s, 'listen:L1', NOW + SNOOZE_MS + 1)), ['listen:L1'], 'expired snoozes are dropped');
}
console.log('attention: ok');
