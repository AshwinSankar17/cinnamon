/**
 * The world's backdrop at the frame's scale, plus whatever the screen shows beyond it: a wide screen's side margins
 * continue a clean edge column (EDGE), a short screen's top and bottom continue the first and last rows, and corners
 * take both. Each extension is the same drawing, sliced thin and stretched, so walls, sky, sea and floors run on.
 */
import { View } from 'react-native';
import Svg from 'react-native-svg';
import type { Tokens, WorldId } from '@/src/worlds';
import { BACKDROPS, SceneDefs } from './backdrops';
import { EDGE, type Frame } from './frame';

export function SceneBackdrop({ world, f, t, dark }: { world: WorldId; f: Frame; t: Tokens; dark: boolean }) {
  const Backdrop = BACKDROPS[world];
  const sw = 390 * f.s;
  const sh = 844 * f.s;
  const [el, er] = EDGE[world];
  // [screen start, screen size, design start, design size, is the middle]
  const cols = [[0, f.ox, el, 0.5, false], [f.ox, sw, 0, 390, true], [f.ox + sw, f.w - f.ox - sw, er, 0.5, false]] as const;
  const rows = [[0, f.oy, 0.25, 0.5, false], [f.oy, sh, 0, 844, true], [f.oy + sh, f.h - f.oy - sh, 843.25, 0.5, false]] as const;
  const cells = [];
  for (const [x, w, dx, dw, mx] of cols) {
    for (const [y, h, dy, dh, my] of rows) {
      if (w < 0.5 || h < 0.5) continue;
      // edge cells overlap the scene by a pixel so no hairline shows between them
      const l = mx ? x : x === 0 ? 0 : x - 1;
      const tp = my ? y : y === 0 ? 0 : y - 1;
      cells.push(
        <Svg key={`${x}:${y}`} width={w + (mx ? 0 : 1)} height={h + (my ? 0 : 1)} viewBox={`${dx} ${dy} ${dw} ${dh}`}
          preserveAspectRatio="none" style={{ position: 'absolute', left: l, top: tp }}>
          <SceneDefs t={t} />
          <Backdrop t={t} dark={dark} />
        </Svg>,
      );
    }
  }
  return (
    <>
      {cells}
      {f.ox > 24 ? [0, f.ox + sw].map((left) => ( // on a wide screen the margins recede so the scene reads as the stage
        <View key={left} pointerEvents="none" style={{ position: 'absolute', left, top: 0, width: f.ox, height: f.h, backgroundColor: t.bg2, opacity: 0.35 }} />
      )) : null}
    </>
  );
}
