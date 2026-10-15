/**
 * One companion per world (spec §7.2), switched on or off from the menu. Drawn from the mockup; each idles with one
 * transform loop while live. They only restate verified counts: Anise lights one arm per running job (max 8),
 * Hazel holds one acorn per finding (none exist until findings land), Nutmeg's ears go up when something waits.
 */
import { useEffect, useRef } from 'react';
import { Animated, Easing, Pressable } from 'react-native';
import Svg, { Circle, Ellipse, G, Path } from 'react-native-svg';
import type { WorldId } from '@/src/worlds';
import { at, type Frame } from './frame';

type Props = { world: WorldId; f: Frame; live: boolean; running: number; needs: boolean; label: string; onPress: () => void };

/** Design-space anchor (feet / centre) and drawing box for each pet: [x, y, boxW, boxH, boxOx, boxOy]. */
const PLACE: Record<WorldId, [number, number, number, number, number, number]> = {
  bakery: [336, 562, 76, 52, -40, -48],
  forest: [318, 650, 96, 92, -36, -84],
  rustic: [334, 656, 104, 62, -52, -50],
  space: [318, 312, 100, 112, -50, -64],
  tide: [180, 436, 70, 46, -38, -24],
};

function useIdle(live: boolean, ms: number) {
  const v = useRef(new Animated.Value(0)).current;
  useEffect(() => {
    if (!live) {
      v.stopAnimation();
      return;
    }
    const loop = Animated.loop(Animated.timing(v, { toValue: 1, duration: ms, easing: Easing.linear, useNativeDriver: true }));
    loop.start();
    return () => loop.stop();
  }, [live, ms, v]);
  return v;
}

export function Pet({ world, f, live, running, needs, label, onPress }: Props) {
  const [x, y, w, h, bx, by] = PLACE[world];
  const idle = useIdle(live, world === 'tide' ? 16000 : world === 'space' ? 6500 : world === 'rustic' ? 3600 : world === 'forest' ? 2600 : 3400);
  const p = at(f, x + bx, y + by);
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  let transform: any[] = [];
  if (world === 'bakery') transform = [{ translateY: idle.interpolate({ inputRange: [0, 0.7, 0.76, 0.82, 0.88, 0.94, 1], outputRange: [0, 0, -9, 0, -5, 0, 0].map((v) => v * f.s) }) }];
  if (world === 'forest') transform = [{ rotate: idle.interpolate({ inputRange: [0, 0.6, 0.7, 0.8, 1], outputRange: ['0deg', '0deg', '-3deg', '2deg', '0deg'] }) }];
  if (world === 'rustic') transform = [{ scaleY: idle.interpolate({ inputRange: [0, 0.5, 1], outputRange: [1, 1.04, 1] }) }];
  if (world === 'space') transform = [{ translateY: idle.interpolate({ inputRange: [0, 0.5, 1], outputRange: [0, -10 * f.s, 0] }) }];
  if (world === 'tide')
    transform = [
      { translateX: idle.interpolate({ inputRange: [0, 0.48, 0.5, 0.98, 1], outputRange: [-140, 140, 140, -140, -140].map((v) => v * f.s) }) },
      { scaleX: idle.interpolate({ inputRange: [0, 0.49, 0.5, 0.99, 1], outputRange: [1, 1, -1, -1, 1] }) },
    ];
  const lit = Math.min(8, running);
  return (
    <Animated.View style={{ position: 'absolute', left: p.left, top: p.top, width: w * f.s, height: h * f.s, transform, transformOrigin: 'bottom' }}>
      <Pressable accessibilityRole="button" accessibilityLabel={label} onPress={onPress} style={{ flex: 1 }}>
        <Svg width={w * f.s} height={h * f.s} viewBox={`${bx} ${by} ${w} ${h}`}>
          {world === 'bakery' ? (
            <G>
              <Path d="M14 -18L36 -27L31 -13Z" fill="#7A5236" />
              <Ellipse cx={2} cy={-18} rx={18} ry={13} fill="#A8774E" />
              <Ellipse cx={-4} cy={-13} rx={12} ry={8} fill="#EEDCC3" />
              <Path d="M-2 -25C10 -29 21 -22 18 -11C10 -13 2 -16 -2 -25Z" fill="#7E5434" />
              <Circle cx={-14} cy={-32} r={10} fill="#9A6A44" />
              <Path d="M-23.5 -35C-21 -44 -8 -45 -5 -36C-10 -38.5 -17 -38.5 -23.5 -35Z" fill="#6E4A2E" />
              <Circle cx={-17} cy={-28.5} r={3.6} fill="#EEDCC3" />
              <Circle cx={-17} cy={-33} r={2.3} fill="#1A120D" />
              <Circle cx={-17.8} cy={-33.8} r={0.75} fill="#fff" />
              <Path d="M-24 -32.5L-32 -30.5L-24 -28.3Z" fill="#E7A93E" />
              <Path d="M-3 -6V0M4 -6V0" stroke="#C78A4A" strokeWidth={1.8} strokeLinecap="round" />
            </G>
          ) : null}
          {world === 'forest' ? (
            <G>
              <Path d="M10 -8C40 -8 50 -40 38 -62C28 -80 4 -78 6 -60C8 -48 26 -50 26 -36C26 -22 12 -18 6 -14Z" fill="#B0602A" />
              <Path d="M30 -56C34 -46 30 -34 22 -28" fill="none" stroke="#D88A4E" strokeWidth={3} strokeLinecap="round" />
              <Ellipse cx={0} cy={-20} rx={15} ry={20} fill="#C06F33" />
              <Ellipse cx={-5} cy={-18} rx={8} ry={13} fill="#F2DDBA" />
              <Ellipse cx={7} cy={-8} rx={12} ry={9} fill="#B5652D" />
              <Circle cx={-6} cy={-44} r={12} fill="#C06F33" />
              <Path d="M-13 -53L-15 -65L-6 -56ZM-1 -55L3 -66L5 -54Z" fill="#B0602A" />
              <Ellipse cx={-14} cy={-40.5} rx={6.5} ry={4.8} fill="#F2DDBA" />
              <Circle cx={-9} cy={-47} r={2.5} fill="#1A120D" />
              <Circle cx={-9.8} cy={-47.8} r={0.8} fill="#fff" />
              <Circle cx={-20} cy={-41.5} r={1.7} fill="#3A2216" />
              <Ellipse cx={-20} cy={-27} rx={3.5} ry={3} fill="#B5652D" />
              <Ellipse cx={-10} cy={-27} rx={3.5} ry={3} fill="#B5652D" />
            </G>
          ) : null}
          {world === 'rustic' ? (
            <G>
              <Ellipse cx={2} cy={1} rx={44} ry={7} fill="rgba(0,0,0,.18)" />
              <Ellipse cx={6} cy={-16} rx={35} ry={17} fill="#D98A47" />
              <Path d="M-2 -32C0 -24 -2 -18 -6 -12M10 -33C12 -24 10 -16 6 -9M22 -30C24 -22 22 -16 18 -10" fill="none" stroke="#B86A30" strokeWidth={3} strokeLinecap="round" />
              <Path d="M-30 -2C-26 6 34 8 40 -6" fill="none" stroke="#C8783C" strokeWidth={7} strokeLinecap="round" />
              <Circle cx={-22} cy={-20} r={13.5} fill="#D98A47" />
              <Path d={needs ? 'M-33 -27L-34 -42L-23 -31ZM-18 -31L-12 -44L-10 -28Z' : 'M-34 -28L-37 -38L-26 -31ZM-20 -32L-15 -40L-12 -29Z'} fill="#D98A47" />
              <Path d="M-30 -30L-30.5 -36L-26 -31ZM-17 -32L-14 -37L-14 -30Z" fill="#F2A6A0" />
              <Path d={needs ? 'M-30 -21Q-27 -18 -24 -21M-19 -22.5a2 2.4 0 1 0 .1 0' : 'M-31 -20Q-28 -17 -25 -20M-21 -21Q-18 -18 -15 -21'} fill="none" stroke="#4A2A14" strokeWidth={1.5} strokeLinecap="round" />
              <Path d="M-24 -15L-22 -13L-20 -15Z" fill="#E88A86" />
            </G>
          ) : null}
          {world === 'space' ? (
            <G>
              {Array.from({ length: 8 }, (_, i) => {
                const x0 = -21 + i * 6;
                const sp = i - 3.5;
                const ex = x0 + sp * 6;
                const ey = 34 - Math.abs(sp) * 1.5;
                const on = i < lit;
                return (
                  <G key={i}>
                    <Path d={`M${x0} 2C${x0 + sp * 2} 14 ${x0 + sp * 6 + (i % 2 ? 6 : -6)} 22 ${ex} ${ey}`} fill="none" stroke="#8E7BFF" strokeWidth={6} strokeLinecap="round" />
                    <Circle cx={ex} cy={ey} r={on ? 4.4 : 3.2} fill={on ? '#FFD27A' : '#B8ABFF'} />
                  </G>
                );
              })}
              <Path d="M-28 4C-31 -42 31 -42 28 4C18 8 -18 8 -28 4Z" fill="#8E7BFF" />
              <Path d="M-18 -26C-12 -34 -2 -36 6 -34" fill="none" stroke="#B8ABFF" strokeWidth={4} strokeLinecap="round" />
              <Ellipse cx={0} cy={-12} rx={11.5} ry={12.5} fill="#fff" />
              <Circle cx={2} cy={-10} r={6.5} fill="#1A1440" />
              <Circle cx={4.5} cy={-13} r={2.2} fill="#fff" />
              <Ellipse cx={-17} cy={-2} rx={4} ry={2.4} fill="#F2A6D8" opacity={0.8} />
              <Ellipse cx={17} cy={-2} rx={4} ry={2.4} fill="#F2A6D8" opacity={0.8} />
              <Path d="M-5 2Q0 6 5 2" fill="none" stroke="#2A1F66" strokeWidth={1.6} strokeLinecap="round" />
              <Path d="M0 -34C2 -42 8 -46 10 -50" fill="none" stroke="#8E7BFF" strokeWidth={2.6} strokeLinecap="round" />
              <Circle cx={10.5} cy={-52} r={4.2} fill="#5EE6C8" />
            </G>
          ) : null}
          {world === 'tide' ? (
            <G>
              <Path d="M-18 0L-33 -12L-30 0L-33 12Z" fill="#E8792A" />
              <Ellipse cx={0} cy={0} rx={20} ry={14.5} fill="#F59A3E" />
              <Path d="M-7 -13.5C-3 -5 -3 5 -7 13.5M7 -13C10 -5 10 5 7 13" fill="none" stroke="#fff" strokeWidth={3.6} />
              <Path d="M-6 -13C0 -23 9 -21 11 -12Z" fill="#E8792A" />
              <Circle cx={12} cy={-3} r={3.3} fill="#1A120D" />
              <Circle cx={12.8} cy={-4} r={1.1} fill="#fff" />
              <Path d="M19 3Q17 5.5 14.5 4.5" fill="none" stroke="#7A3A12" strokeWidth={1.3} strokeLinecap="round" />
            </G>
          ) : null}
        </Svg>
      </Pressable>
    </Animated.View>
  );
}

/** Where the companion's speech bubble sits (design units, top-left). */
export const SAY_AT: Record<WorldId, [number, number]> = {
  bakery: [150, 450],
  forest: [110, 486],
  rustic: [120, 470],
  space: [96, 232],
  tide: [60, 340],
};
