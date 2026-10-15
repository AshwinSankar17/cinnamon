/**
 * The cinnamon roll: Cinnamon's main character. Its swirl is the focus thread's real history (src/roll.ts), its
 * face is the project's state (spec §7.2): needs you, working, proud (unused until findings exist), asleep.
 * Drawn in a 224×224 box (design units: the roll spans x 0..200, y 0..170). Motion is transform/opacity only and
 * runs only when `live`.
 */
import { forwardRef, useEffect, useImperativeHandle, useMemo, useRef } from 'react';
import { Animated, Easing, View } from 'react-native';
import Svg, { Circle, Ellipse, G, Path, Text as SvgText } from 'react-native-svg';
import { edges, line, outline, sinceIndex, swirlPoints, swirlWidths, N, type Activity } from '@/src/roll';

export type Mood = 'needs' | 'working' | 'proud' | 'asleep';
export type Accessory = 'icing' | 'leaf' | 'helmet' | null;
export type RollHandle = { land: () => void };

const VB = { x: -12, y: -38, w: 224, h: 236 };
const FACE: Record<Mood, string> = {
  needs: 'M64 86Q72 80 82 84M118 84Q128 80 136 86M95 120Q100 126 105 120Q100 116 95 120Z',
  working: 'M90 118Q100 126 110 118',
  proud: 'M66 104Q76 92 86 104M114 104Q124 92 134 104M86 116Q100 132 114 116',
  asleep: 'M68 104Q76 110 84 104M116 104Q124 110 132 104M94 120Q100 123 106 120',
};
const LOOK: Record<Mood, [number, number]> = { needs: [2.2, 2.6], working: [-1.5, -3], proud: [0, -2], asleep: [0, -2] };
const SWIRL = swirlPoints();

type Props = {
  mood: Mood;
  activity: Activity | null;
  accessory: Accessory;
  live: boolean;
  /** Rendered width in px. */
  width: number;
  helmetGlass?: string;
  helmetRim?: string;
  helmetCollar?: string;
  zColor?: string;
};

export const Roll = forwardRef<RollHandle, Props>(function Roll(
  { mood, activity, accessory, live, width, helmetGlass = 'rgba(255,255,255,.22)', helmetRim = '#fff', helmetCollar = '#f4f3fc', zColor = '#5E4636' },
  ref,
) {
  const height = (width * VB.h) / VB.w;
  const k = width / VB.w;
  const geo = useMemo(() => {
    const W = swirlWidths(activity);
    const E = edges(SWIRL, W);
    const since = sinceIndex(activity);
    return { body: outline(E, 0, N - 1), trace: since < N - 1 ? line(SWIRL, since, N - 2) : '', tip: SWIRL[N - 1] };
  }, [activity]);

  const breathe = useRef(new Animated.Value(0)).current;
  const steam = useRef(new Animated.Value(0)).current;
  const tip = useRef(new Animated.Value(0)).current;
  const drop = useRef(new Animated.Value(0)).current;
  const squash = useRef(new Animated.Value(0)).current;

  useEffect(() => {
    if (!live) {
      breathe.stopAnimation();
      steam.stopAnimation();
      tip.stopAnimation();
      return;
    }
    const loops = [
      Animated.loop(Animated.sequence([
        Animated.timing(breathe, { toValue: 1, duration: 2100, easing: Easing.inOut(Easing.sin), useNativeDriver: true }),
        Animated.timing(breathe, { toValue: 0, duration: 2100, easing: Easing.inOut(Easing.sin), useNativeDriver: true }),
      ])),
      Animated.loop(Animated.timing(steam, { toValue: 1, duration: 2800, easing: Easing.linear, useNativeDriver: true })),
      Animated.loop(Animated.sequence([
        Animated.timing(tip, { toValue: 1, duration: 1200, easing: Easing.inOut(Easing.sin), useNativeDriver: true }),
        Animated.timing(tip, { toValue: 0, duration: 1200, easing: Easing.inOut(Easing.sin), useNativeDriver: true }),
      ])),
    ];
    loops.forEach((l) => l.start());
    return () => loops.forEach((l) => l.stop());
  }, [live, breathe, steam, tip]);

  useImperativeHandle(ref, () => ({
    land: () => {
      drop.setValue(1);
      squash.setValue(0);
      Animated.sequence([
        Animated.timing(drop, { toValue: 0, duration: 520, easing: Easing.in(Easing.quad), useNativeDriver: true }),
        Animated.timing(squash, { toValue: 1, duration: 110, easing: Easing.out(Easing.quad), useNativeDriver: true }),
        Animated.spring(squash, { toValue: 0, friction: 4, tension: 120, useNativeDriver: true }),
      ]).start();
    },
  }));

  const open = mood === 'needs' || mood === 'working';
  const look = LOOK[mood];
  const showSteam = (mood === 'working' || mood === 'needs') && accessory !== 'helmet';

  const breatheStyle = {
    transform: [
      { translateY: drop.interpolate({ inputRange: [0, 1], outputRange: [0, -260] }) },
      { scaleX: squash.interpolate({ inputRange: [0, 1], outputRange: [1, 1.08] }) },
      { scaleY: Animated.add(squash.interpolate({ inputRange: [0, 1], outputRange: [1, 0.86] }),
                             breathe.interpolate({ inputRange: [0, 1], outputRange: [0, -0.015] })) },
    ],
    transformOrigin: 'bottom' as const,
  };

  return (
    <View style={{ width, height }} pointerEvents="none">
      <Animated.View style={[{ position: 'absolute', left: 0, top: 0, width, height }, breatheStyle]}>
        <Svg width={width} height={height} viewBox={`${VB.x} ${VB.y} ${VB.w} ${VB.h}`}>
          <Ellipse cx={100} cy={152} rx={80} ry={10} fill="rgba(30,15,6,.22)" />
          <Path d="M22 66C22 124 52 148 100 148C148 148 178 124 178 66Z" fill="#C98A4B" />
          <Path d="M28 90C36 126 66 140 100 140C134 140 164 126 172 90M24 76C30 118 60 134 100 134" fill="none"
            stroke="#AD6F38" strokeWidth={2.2} opacity={0.55} strokeLinecap="round" />
          <Ellipse cx={100} cy={64} rx={78} ry={42} fill="#E9B275" />
          <Ellipse cx={96} cy={58} rx={66} ry={32} fill="#F3C98E" opacity={0.55} />
          <Path d={geo.body} fill="#8A4320" />
          {geo.trace ? <Path d={geo.trace} fill="none" stroke="#C1601F" strokeWidth={3.2} strokeLinecap="round" opacity={0.95} /> : null}
          <Circle cx={geo.tip[0]} cy={geo.tip[1]} r={3.4} fill="#FF8A3D" />
          {accessory === 'icing' ? (
            <Path d="M36 60C52 44 70 82 92 52C108 32 124 80 146 50C156 40 166 52 168 60" fill="none" stroke="#FFF6EA"
              strokeWidth={6} strokeLinecap="round" strokeLinejoin="round" />
          ) : null}
          {accessory === 'leaf' ? (
            <G transform="translate(58 28) rotate(-24)">
              <Path d="M0 0C10 -18 34 -18 42 -6C30 6 10 8 0 0Z" fill="#5E9A45" />
              <Path d="M2 -1C14 -6 26 -8 40 -6" fill="none" stroke="#3F6E36" strokeWidth={1.6} />
            </G>
          ) : null}
          <Ellipse cx={62} cy={114} rx={9} ry={4.6} fill="#E8806A" opacity={0.45} />
          <Ellipse cx={138} cy={114} rx={9} ry={4.6} fill="#E8806A" opacity={0.45} />
          {open ? (
            <G>
              <Ellipse cx={76} cy={102} rx={7} ry={9} fill="#2B150A" />
              <Ellipse cx={124} cy={102} rx={7} ry={9} fill="#2B150A" />
              <Circle cx={76 + look[0]} cy={102 + look[1]} r={2.6} fill="#fff" />
              <Circle cx={124 + look[0]} cy={102 + look[1]} r={2.6} fill="#fff" />
            </G>
          ) : null}
          <Path d={FACE[mood]} fill="none" stroke="#2B150A" strokeWidth={2.6} strokeLinecap="round" strokeLinejoin="round" />
          {mood === 'asleep' ? (
            <G>
              <SvgText x={160} y={20} fontSize={20} fontWeight="700" fill={zColor}>z</SvgText>
              <SvgText x={176} y={0} fontSize={15} fontWeight="700" fill={zColor}>z</SvgText>
            </G>
          ) : null}
          {accessory === 'helmet' ? (
            <G>
              <Circle cx={100} cy={86} r={100} fill={helmetGlass} stroke={helmetRim} strokeWidth={4} />
              <Path d="M38 40C56 10 96 -4 132 4" fill="none" stroke="#fff" strokeWidth={6} strokeLinecap="round" opacity={0.55} />
              <Path d="M18 150C40 176 160 176 182 150" fill="none" stroke={helmetCollar} strokeWidth={12} strokeLinecap="round" />
            </G>
          ) : null}
        </Svg>
        {live ? (
          <Animated.View
            style={{
              position: 'absolute',
              left: (geo.tip[0] - VB.x) * k - 7 * k,
              top: (geo.tip[1] - VB.y) * k - 7 * k,
              width: 14 * k,
              height: 14 * k,
              borderRadius: 7 * k,
              backgroundColor: 'rgba(255,138,61,.55)',
              opacity: tip.interpolate({ inputRange: [0, 1], outputRange: [0.55, 0.15] }),
              transform: [{ scale: tip.interpolate({ inputRange: [0, 1], outputRange: [1, 1.6] }) }],
            }}
          />
        ) : null}
        {showSteam && live
          ? [74, 102, 130].map((x, i) => {
              const ph = Animated.modulo(Animated.add(steam, i / 3), 1);
              return (
                <Animated.View
                  key={x}
                  style={{
                    position: 'absolute',
                    left: (x - 10 - VB.x) * k,
                    top: (-28 - VB.y) * k,
                    opacity: ph.interpolate({ inputRange: [0, 0.2, 1], outputRange: [0, 0.8, 0] }),
                    transform: [{ translateY: ph.interpolate({ inputRange: [0, 1], outputRange: [14 * k, -10 * k] }) }],
                  }}
                >
                  <Svg width={20 * k} height={40 * k} viewBox="0 0 20 40">
                    <Path d="M10 38C2 24 18 14 10 0" fill="none" stroke="rgba(255,255,255,.75)" strokeWidth={3} strokeLinecap="round" />
                  </Svg>
                </Animated.View>
              );
            })
          : null}
      </Animated.View>
    </View>
  );
});

/** Where the roll's base sits in design units (the bottom-centre of the 0..200 × 0..150 body). */
export const ROLL_BASE = { x: (100 - VB.x) / VB.w, y: (150 - VB.y) / VB.h };
