/** Self-check for the app's side of the attention list: `npm test`. Plain node:assert, no framework. */
import assert from 'node:assert/strict';
import { hrefOf, snooze, visible, SNOOZE_MS, type Item } from './attention';

const NOW = 1_800_000_000_000;
const item = (kind: Item['kind'], target: string, tier: Item['tier']): Item =>
  ({ key: `${kind}:${target}`, tier, kind, thread_id: 't1', target, title: kind, detail: '', at: NOW / 1000 });

// each kind opens its own screen; Brain's ids are escaped
assert.equal(hrefOf(item('approval', 'apr_1', 1)), '/approval/apr_1');
assert.equal(hrefOf(item('listen', 'lst 1', 4)), '/listen/lst%201');
for (const k of ['needs', 'failed', 'news'] as const) assert.equal(hrefOf(item(k, 'thr_1', 2)), '/thread/thr_1');

// "Later" hides an item on this phone for four hours, keeps Brain's order, then the item comes back
{
  const items = [item('approval', 'a', 1), item('listen', 'l', 4), item('news', 'n', 5)];
  const s = snooze({}, 'approval:a', NOW);
  assert.equal(s['approval:a'], NOW + SNOOZE_MS);
  assert.deepEqual(visible(items, s, NOW).map((i) => i.key), ['listen:l', 'news:n']);
  assert.deepEqual(visible(items, s, NOW + SNOOZE_MS + 1).map((i) => i.key), ['approval:a', 'listen:l', 'news:n']);
  assert.deepEqual(Object.keys(snooze(s, 'listen:l', NOW + SNOOZE_MS + 1)), ['listen:l'], 'expired snoozes are dropped');
}
// an older Brain without the list reads as nothing to do, not a crash
assert.deepEqual(visible(undefined, {}, NOW), []);
console.log('attention: ok');
