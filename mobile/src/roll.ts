/**
 * Geometry for the cinnamon roll's swirl and its unrolled ribbon, from a thread's real activity (GET
 * /v1/threads/{id}/activity). Port of the approved mockup (docs/design/cinnamon/Cinnamon.dc.html `base()`).
 * Width at each of N time bins = smoothed activity: turns (weighted by output) and running jobs.
 */
export type Activity = {
  start: number;
  now: number;
  turns: [number, number][];
  jobs: [number, number, number][];
  left_at: number | null;
};
export type Pt = [number, number];

export const N = 150;

/** Bin index of a timestamp, 0..n-1. */
export function binOf(a: Activity, ts: number, n = N): number {
  const span = Math.max(1, a.now - a.start);
  return Math.max(0, Math.min(n - 1, Math.floor(((ts - a.start) / span) * (n - 1))));
}

/** Ribbon width per bin. No activity yet: an even, quiet ribbon (nothing is invented). */
export function widths(a: Activity | null, n = N): number[] {
  const act = new Array(n).fill(0);
  if (a) {
    for (const [t, w] of a.turns) act[binOf(a, t, n)] += 1 + Math.min(w, 30) / 12;
    for (const [s, e] of a.jobs) {
      act[binOf(a, s, n)] += 0.7;
      for (let i = binOf(a, s, n); i <= binOf(a, Math.min(e, a.now), n); i++) act[i] += 0.12;
    }
  }
  const sm = act.map((_, i) => {
    let s = 0;
    let ws = 0;
    for (let k = -3; k <= 3; k++) {
      const j = i + k;
      if (j < 0 || j >= n) continue;
      const w = Math.exp((-k * k) / 4);
      s += act[j] * w;
      ws += w;
    }
    return s / ws;
  });
  const mx = Math.max(...sm);
  return sm.map((v, i) => {
    const s = i / (n - 1);
    const taper = Math.min(1, s * 14) * Math.min(1, (1 - s) * 30 + 0.35);
    return (2.6 + 17 * (mx > 0 ? Math.sqrt(v / mx) : 0.35)) * taper;
  });
}

/** First bin since the owner left (the fresh, brighter part); n when unknown. */
export function sinceIndex(a: Activity | null, n = N): number {
  if (!a || !a.left_at || a.left_at <= a.start) return n;
  return Math.min(n, Math.round(((a.left_at - a.start) / Math.max(1, a.now - a.start)) * (n - 1)));
}

/** The swirl on the roll's top face: 3 turns, oldest at the centre, the tail (now) beside the face. */
export function swirlPoints(n = N): Pt[] {
  const k = 20 / (2 * Math.PI);
  const r0 = 3;
  const rmax = 66;
  const thMax = (rmax - r0) / k;
  const L = r0 * thMax + (k * thMax * thMax) / 2;
  const pts: Pt[] = [];
  for (let i = 0; i < n; i++) {
    const l = (i / (n - 1)) * L;
    const th = (-r0 + Math.sqrt(r0 * r0 + 2 * k * l)) / k;
    const r = r0 + k * th;
    const ph = th - thMax + 0.15;
    pts.push([100 + r * Math.cos(ph), 62 + r * Math.sin(ph) * 0.5]);
  }
  return pts;
}

/** The unrolled ribbon, top (oldest) to bottom (now), in a 390×440 box. */
export function ribbonPoints(n = N): Pt[] {
  const pts: Pt[] = [];
  for (let i = 0; i < n; i++) {
    const s = i / (n - 1);
    const A = 100 * Math.pow(1 - s, 0.8);
    pts.push([195 + A * Math.sin(2 * Math.PI * 1.45 * s + 0.35), 18 + s * 382]);
  }
  return pts;
}

export function edges(P: Pt[], W: number[]): { L: Pt[]; R: Pt[] } {
  const L: Pt[] = [];
  const R: Pt[] = [];
  for (let i = 0; i < P.length; i++) {
    const p = P[Math.max(0, i - 1)];
    const q = P[Math.min(P.length - 1, i + 1)];
    let tx = q[0] - p[0];
    let ty = q[1] - p[1];
    const len = Math.hypot(tx, ty) || 1;
    tx /= len;
    ty /= len;
    const w = W[i] / 2;
    L.push([P[i][0] - ty * w, P[i][1] + tx * w]);
    R.push([P[i][0] + ty * w, P[i][1] - tx * w]);
  }
  return { L, R };
}

const f1 = (v: number) => v.toFixed(1);

/** Filled outline of bins a..b. */
export function outline(E: { L: Pt[]; R: Pt[] }, a: number, b: number): string {
  if (b < a) return '';
  const pts = E.L.slice(a, b + 1).concat(E.R.slice(a, b + 1).reverse());
  return 'M' + pts.map((p) => `${f1(p[0])} ${f1(p[1])}`).join(' L') + ' Z';
}

/** Centre line of bins a..b. */
export function line(P: Pt[], a: number, b: number): string {
  let d = '';
  for (let i = a; i <= b; i++) d += `${i === a ? 'M' : ' L'}${f1(P[i][0])} ${f1(P[i][1])}`;
  return d;
}

/** Point at a fractional bin. */
export function pointAt(P: Pt[], f: number): Pt {
  const i = Math.max(0, Math.min(P.length - 1, Math.floor(f)));
  const j = Math.min(P.length - 1, i + 1);
  const u = f - i;
  return [P[i][0] + (P[j][0] - P[i][0]) * u, P[i][1] + (P[j][1] - P[i][1]) * u];
}

/** Swirl widths: thinner than the ribbon so three turns fit on the roll's top. */
export function swirlWidths(a: Activity | null, n = N): number[] {
  return widths(a, n).map((w) => 0.9 + w * 0.34);
}
