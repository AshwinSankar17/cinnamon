/** Formatting helpers. Timestamps on the wire may be ISO strings, unix seconds or unix ms. */

export function toMs(ts: string | number | null | undefined): number | null {
  if (ts === null || ts === undefined || ts === '') return null;
  if (typeof ts === 'number') return ts < 1e12 ? ts * 1000 : ts;
  if (/^\d+(\.\d+)?$/.test(ts)) return toMs(Number(ts));
  const t = Date.parse(ts);
  return Number.isNaN(t) ? null : t;
}

export function hhmm(ts: string | number | null | undefined): string {
  const ms = toMs(ts);
  if (ms === null) return '--:--';
  const d = new Date(ms);
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
}

export function duration(ms: number): string {
  const s = Math.max(0, Math.round(ms / 1000));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  if (h) return `${h}h ${m}m`;
  if (m) return `${m}m ${sec}s`;
  return `${sec}s`;
}

/** "6h", "3m", "2d" since a timestamp. */
export function ago(ts: string | number | null | undefined, now = Date.now()): string {
  const ms = toMs(ts);
  if (ms === null) return '';
  const s = Math.max(0, Math.round((now - ms) / 1000));
  if (s < 60) return 'now';
  if (s < 3600) return `${Math.floor(s / 60)}m`;
  if (s < 86400) return `${Math.floor(s / 3600)}h`;
  return `${Math.floor(s / 86400)}d`;
}

export function sinceLabel(ms: number): string {
  const m = Math.max(0, Math.round(ms / 60000));
  const h = Math.floor(m / 60);
  return h ? `${h}h ${m % 60}m` : `${m}m`;
}

export function kTokens(n: number | undefined): string {
  if (n === undefined || n === null) return '0';
  if (n < 1000) return String(n);
  return `${(n / 1000).toFixed(n < 100_000 ? 1 : 0)}k`;
}

export function plural(n: number, one: string, many = `${one}s`): string {
  return `${n} ${n === 1 ? one : many}`;
}

const WORDS = ['no', 'one', 'two', 'three', 'four', 'five', 'six', 'seven', 'eight', 'nine', 'ten', 'eleven', 'twelve'];
/** Prose numbers: "two jobs", "fourteen files" up to twelve, digits after. */
export function word(n: number): string {
  return n >= 0 && n < WORDS.length ? WORDS[n] : String(n);
}
