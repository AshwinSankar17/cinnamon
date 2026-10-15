/** Map the mockup's 390×844 design space onto the screen, covering it (like preserveAspectRatio="xMidYMid slice"). */
export type Frame = { s: number; ox: number; oy: number; w: number; h: number };

export function frameFor(w: number, h: number): Frame {
  const s = Math.max(w / 390, h / 844);
  return { s, ox: (w - 390 * s) / 2, oy: (h - 844 * s) / 2, w, h };
}

/** Screen position of a design-space point. */
export function at(f: Frame, x: number, y: number): { left: number; top: number } {
  return { left: f.ox + x * f.s, top: f.oy + y * f.s };
}

/** Per-world placement of the roll (base point in design units, and its scale), from the mockup's `hero` table. */
export const HERO: Record<string, [number, number, number]> = {
  bakery: [190, 568, 1],
  forest: [150, 592, 0.9],
  rustic: [160, 604, 0.92],
  space: [200, 552, 0.74],
  tide: [195, 606, 0.86],
};
