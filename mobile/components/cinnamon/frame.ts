/** Map the mockup's 390×844 design space onto the screen. The screen's free space is what lies between the header and
 *  the bottom tray; each world's content (its threads, the roll, the companion) must sit inside it, whatever the phone.
 *  So the scene is scaled to fit that content into the free space, never larger than covering the screen, and placed
 *  so the backdrop leaves as little uncovered as possible. What it cannot cover (a short or wide screen) is filled by
 *  continuing the backdrop as its own mirror image (SceneBackdrop). */
import type { WorldId } from '@/src/worlds';

export type Frame = { s: number; ox: number; oy: number; w: number; h: number };

/** Per world, in design units: the content's top and bottom (threads down to the lowest character) and its left and
 *  right extent. Everything outside may be cropped or covered by the header and tray. */
export const BAND: Record<WorldId, { top: number; bottom: number; left: number; right: number; calm: number }> = {
  bakery: { top: 206, bottom: 596, left: 10, right: 380, calm: 320 },
  forest: { top: 352, bottom: 668, left: 14, right: 382, calm: 190 },
  rustic: { top: 198, bottom: 664, left: 16, right: 388, calm: 196 },
  space: { top: 240, bottom: 618, left: 4, right: 386, calm: 226 },
  tide: { top: 246, bottom: 628, left: 10, right: 380, calm: 240 },
};
// calm: down to which design y the backdrop is plain enough (wall, sky, water) for the header to sit on.

/** `top`/`bottom`: the free space between the header and the tray, in screen pixels. */
export function frameFor(world: WorldId, w: number, h: number, top: number, bottom: number): Frame {
  const b = BAND[world];
  const fit = Math.max(0.1, bottom - top) / (b.bottom - b.top);
  // never larger than covering the screen, nor more than 10% past fitting its height (a wide desktop column would
  // otherwise zoom a world with a short content band, like the forest, and crop its tree)
  const s = Math.min(fit, w / (b.right - b.left), Math.max(w / 390, h / 844), (h / 844) * 1.1);
  const ox = (w - 390 * s) / 2;
  // Any oy in [lo, hi] keeps the content inside the free space (top below the header, bottom above the tray); within
  // it, prefer an oy that leaves no backdrop uncovered (h - 844s <= oy <= 0), nearest the content's centred position.
  const lo = top - b.top * s;
  const hi = bottom - b.bottom * s;
  const want = Math.min(0, Math.max(h - 844 * s, (lo + hi) / 2));
  const oy = Math.min(hi, Math.max(lo, want));
  return { s, ox, oy, w, h };
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
