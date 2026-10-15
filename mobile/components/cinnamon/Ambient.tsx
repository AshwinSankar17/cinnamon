/**
 * Each world's one ambient life (spec §7.2): flour motes in the bakery window light, pollen or fireflies under the oak,
 * the hearth's flicker, a few twinkling stars, rising bubbles. One shared clock, transform/opacity only, and nothing
 * moves unless `live`.
 */
import { useEffect, useMemo, useRef } from 'react';
import { Animated, Easing, View } from 'react-native';
import Svg, { Path } from 'react-native-svg';
import type { WorldId } from '@/src/worlds';
import { at, type Frame } from './frame';

const rnd = (i: number) => {
  const x = Math.sin(i * 127.1 + 311.7) * 43758.5453;
  return x - Math.floor(x);
};

type P = { x: number; y: number; size: number; phase: number; speed: number };

function particles(world: WorldId, dark: boolean): P[] {
  if (world === 'bakery') return Array.from({ length: 9 }, (_, i) => ({ x: 250 + rnd(i) * 110, y: 430 + rnd(i + 40) * 90, size: 2.2, phase: rnd(i + 3), speed: 1 }));
  if (world === 'forest') return Array.from({ length: dark ? 12 : 8 }, (_, i) => ({ x: 30 + rnd(i) * 330, y: 330 + rnd(i + 40) * 300, size: dark ? 3 : 2.5, phase: rnd(i + 3), speed: 1 + rnd(i + 9) }));
  if (world === 'space') return Array.from({ length: 8 }, (_, i) => ({ x: rnd(i * 3 + 1) * 390, y: rnd(i * 3 + 70) * 600, size: 2, phase: rnd(i + 8), speed: 1 }));
  if (world === 'tide') return Array.from({ length: 10 }, (_, i) => ({ x: i % 2 ? 150 + rnd(i) * 90 : 20 + rnd(i) * 350, y: 560 + rnd(i + 40) * 40, size: 4 + rnd(i + 4) * 6, phase: rnd(i + 3), speed: 1 }));
  return [];
}

export function Ambient({ world, dark, f, live }: { world: WorldId; dark: boolean; f: Frame; live: boolean }) {
  const clock = useRef(new Animated.Value(0)).current;
  useEffect(() => {
    if (!live) {
      clock.stopAnimation();
      return;
    }
    const period = world === 'tide' ? 14000 : world === 'rustic' ? 2200 : 9000;
    const loop = Animated.loop(Animated.timing(clock, { toValue: 1, duration: period, easing: Easing.linear, useNativeDriver: true }));
    loop.start();
    return () => loop.stop();
  }, [live, world, clock]);
  const ps = useMemo(() => particles(world, dark), [world, dark]);
  if (!live) return null;

  if (world === 'rustic') {
    const p = at(f, 286, 520);
    return (
      <Animated.View
        pointerEvents="none"
        style={{ position: 'absolute', left: p.left, top: p.top, width: 70 * f.s, height: 72 * f.s, transformOrigin: 'bottom',
                 transform: [{ scaleY: clock.interpolate({ inputRange: [0, 0.3, 0.55, 0.8, 1], outputRange: [1, 1.07, 0.95, 1.04, 1] }) }],
                 opacity: clock.interpolate({ inputRange: [0, 0.4, 0.7, 1], outputRange: [0.85, 1, 0.8, 0.85] }) }}
      >
        <Svg width={70 * f.s} height={72 * f.s} viewBox="286 520 70 72">
          <Path d="M290 590C292 560 306 556 302 528C318 548 312 566 322 572C326 556 334 548 332 532C348 556 352 578 350 590Z" fill="#E8742A" />
          <Path d="M302 590C304 572 312 566 312 552C322 566 320 578 326 582C330 572 334 566 334 556C342 572 342 584 340 590Z" fill="#FFC14A" />
        </Svg>
      </Animated.View>
    );
  }

  return (
    <View pointerEvents="none" style={{ position: 'absolute', left: 0, top: 0, width: f.w, height: f.h }}>
      {ps.map((q, i) => {
        const pos = at(f, q.x, q.y);
        const ph = Animated.modulo(Animated.add(Animated.multiply(clock, q.speed), q.phase), 1);
        const size = q.size * f.s;
        let style: object;
        if (world === 'tide') {
          style = { opacity: ph.interpolate({ inputRange: [0, 0.1, 0.9, 1], outputRange: [0, 0.9, 0.9, 0] }),
                    transform: [{ translateY: ph.interpolate({ inputRange: [0, 1], outputRange: [0, -330 * f.s] }) }, // they pop below the header, not through it
                                { translateX: ph.interpolate({ inputRange: [0, 1], outputRange: [0, 10 * f.s] }) }] };
        } else if (world === 'space') {
          style = { opacity: ph.interpolate({ inputRange: [0, 0.5, 1], outputRange: [0.9, 0.1, 0.9] }) };
        } else if (world === 'bakery') {
          style = { opacity: ph.interpolate({ inputRange: [0, 0.4, 1], outputRange: [0, 0.8, 0] }),
                    transform: [{ translateX: ph.interpolate({ inputRange: [0, 1], outputRange: [0, -30 * f.s] }) },
                                { translateY: ph.interpolate({ inputRange: [0, 1], outputRange: [0, 40 * f.s] }) }] };
        } else {
          style = { opacity: ph.interpolate({ inputRange: [0, 0.3, 0.75, 1], outputRange: [0, 0.9, 0.5, 0] }),
                    transform: [{ translateX: ph.interpolate({ inputRange: [0, 0.55, 1], outputRange: [0, 16 * f.s, -8 * f.s] }) },
                                { translateY: ph.interpolate({ inputRange: [0, 1], outputRange: [0, -38 * f.s] }) }] };
        }
        const color = world === 'forest' ? (dark ? '#F6E27A' : '#FFF7D6') : world === 'bakery' ? '#FFF3DA' : world === 'space' ? '#FFF4D6' : 'rgba(255,255,255,.12)';
        return (
          <Animated.View
            key={i}
            style={[{ position: 'absolute', left: pos.left, top: pos.top, width: size, height: size, borderRadius: size,
                      backgroundColor: color, borderWidth: world === 'tide' ? 1.2 : 0, borderColor: 'rgba(255,255,255,.75)' }, style]}
          />
        );
      })}
    </View>
  );
}
