/**
 * The world's backdrop at the frame's scale, and whatever the screen shows beyond it. Past each edge the scene
 * continues as its own mirror image, which meets the original exactly at the seam: the oak's crown, the cabin's
 * planks, the sea and the sky simply carry on, on any screen shape, with no stretched strips or hard edges. The
 * mirrored tiles are drawn plain (no furniture), so no window, hearth or stump appears twice.
 */
import Svg, { G } from 'react-native-svg';
import type { Tokens, WorldId } from '@/src/worlds';
import { BACKDROPS, SceneDefs } from './backdrops';
import type { Frame } from './frame';

export function SceneBackdrop({ world, f, t, dark }: { world: WorldId; f: Frame; t: Tokens; dark: boolean }) {
  const Backdrop = BACKDROPS[world];
  const cols = [...(f.ox > 0 ? [-1] : []), 0, ...(f.ox + 390 * f.s < f.w ? [1] : [])];
  const rows = [...(f.oy > 0 ? [-1] : []), 0, ...(f.oy + 844 * f.s < f.h ? [1] : [])];
  return (
    <Svg width={f.w} height={f.h} viewBox={`${-f.ox / f.s} ${-f.oy / f.s} ${f.w / f.s} ${f.h / f.s}`} style={{ position: 'absolute', left: 0, top: 0 }}>
      <SceneDefs t={t} />
      {cols.flatMap((i) => rows.map((j) => (
        // i, j = -1 / 0 / 1: the tile left of / on / right of (above / on / below) the scene, mirrored about the shared edge
        <G key={`${i}${j}`} transform={`translate(${i === 1 ? 780 : 0} ${j === 1 ? 1688 : 0}) scale(${i ? -1 : 1} ${j ? -1 : 1})`}>
          <Backdrop t={t} dark={dark} plain={!!(i || j)} />
        </G>
      )))}
    </Svg>
  );
}
