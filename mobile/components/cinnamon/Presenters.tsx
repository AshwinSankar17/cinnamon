/**
 * How a world shows the owner's threads (spec §7.2): order tickets on the rail, tags swinging from the branch,
 * recipe cards on the cork board, satellites on the roll's orbit, bubbles (newest highest). Each item is a button
 * to its thread; the drawing is decorative, the label is the accessible name.
 */
import { useEffect, useRef } from 'react';
import { Animated, Easing, Pressable } from 'react-native';
import Svg, { Circle, Path, Rect, Text as SvgText } from 'react-native-svg';
import type { Tokens, WorldId } from '@/src/worlds';
import type { Thread } from '@/src/types';
import { stateLabel } from '@/src/theme';
import { at, type Frame } from './frame';

type Props = {
  world: WorldId;
  t: Tokens;
  f: Frame;
  threads: Thread[];
  live: boolean;
  showcase: boolean;
  fonts: { display?: string; mono?: string; hand?: string; ui?: string };
  onThread: (id: string) => void;
};

const BAND = { needs_you: '', running: '#7FA86A', waiting: '#6C8EAD', idle: '#B8A898', paused: '#B8A898' } as const;
const clip = (s: string, n: number) => (s.length > n ? s.slice(0, n - 1).trimEnd() + '…' : s);
/** A title on two lines of at most n characters, breaking between words. */
function wrap2(s: string, n: number): [string, string] {
  const words = s.split(/\s+/);
  let a = '';
  while (words.length && (a ? a.length + 1 : 0) + words[0].length <= n) a = a ? `${a} ${words.shift()}` : (words.shift() as string);
  if (!a) return [clip(s, n), ''];
  return [a, clip(words.join(' '), n)];
}

function useLoop(live: boolean, ms: number, delay = 0) {
  const v = useRef(new Animated.Value(0)).current;
  useEffect(() => {
    if (!live) {
      v.stopAnimation();
      return;
    }
    const loop = Animated.loop(Animated.sequence([
      Animated.delay(delay),
      Animated.timing(v, { toValue: 1, duration: ms / 2, easing: Easing.inOut(Easing.sin), useNativeDriver: true }),
      Animated.timing(v, { toValue: 0, duration: ms / 2, easing: Easing.inOut(Easing.sin), useNativeDriver: true }),
    ]));
    loop.start();
    return () => loop.stop();
  }, [live, ms, delay, v]);
  return v;
}

function Item({ f, x, y, w, h, rotate = 0, motion, label, onPress, children }: {
  f: Frame; x: number; y: number; w: number; h: number; rotate?: number;
  motion?: { v: Animated.Value; kind: 'swing' | 'bob' }; label: string; onPress: () => void; children: React.ReactNode;
}) {
  const p = at(f, x, y);
  const anim = motion
    ? motion.kind === 'swing'
      ? [{ rotate: motion.v.interpolate({ inputRange: [0, 1], outputRange: ['0deg', '2.5deg'] }) }]
      : [{ translateY: motion.v.interpolate({ inputRange: [0, 1], outputRange: [0, -8 * f.s] }) }]
    : [];
  return (
    <Animated.View
      style={{ position: 'absolute', left: p.left, top: p.top, width: w * f.s, height: h * f.s,
               transform: [{ rotate: `${rotate}deg` }, ...anim], transformOrigin: motion?.kind === 'swing' ? 'top' : 'center' }}
    >
      <Pressable accessibilityRole="button" accessibilityLabel={label} onPress={onPress} style={{ flex: 1 }}>
        <Svg width={w * f.s} height={h * f.s} viewBox={`0 0 ${w} ${h}`}>{children}</Svg>
      </Pressable>
    </Animated.View>
  );
}

export function Presenters({ world, t, f, threads, live, showcase, fonts, onThread }: Props) {
  const m0 = useLoop(live, 5000, 0);
  const m1 = useLoop(live, 5600, 700);
  const m2 = useLoop(live, 6200, 1400);
  const motions = [m0, m1, m2];
  return (
    <>
      {threads.slice(0, 3).map((th, i) => {
        const name = showcase ? ['Study one', 'Study two', 'Study three'][i] : th.title;
        const state = stateLabel[th.state].toLowerCase();
        const band = BAND[th.state] || t.needs;
        const label = `${name}, ${state}. Open thread.`;
        const go = () => onThread(th.id);
        if (world === 'bakery') {
          const zig = 'M0 0H110V64' + Array.from({ length: 9 }, (_, k) => `L${104 - k * 12} 72L${98 - k * 12} 64`).join('') + 'L2 72L0 64Z';
          return (
            <Item key={th.id} f={f} x={16 + i * 124} y={216} w={110} h={76} rotate={[-2, 1.5, -1][i]} label={label} onPress={go}>
              <Path d={zig} fill={t.sPaper} stroke="rgba(0,0,0,.06)" />
              <Rect width={110} height={5} fill={band} />
              {wrap2(name, 14).map((ln, k) => <SvgText key={k} x={10} y={23 + k * 15} fontSize={12.5} fontFamily={fonts.display} fontWeight="600" fill="#2A1A10">{ln}</SvgText>)}
              <SvgText x={10} y={55} fontSize={9.5} fontFamily={fonts.mono} fill="#6A5444">{state}</SvgText>
            </Item>
          );
        }
        if (world === 'forest') {
          const x = [70, 174, 276][i] - 50;
          const y = [384, 398, 388][i] - 26;
          return (
            <Item key={th.id} f={f} x={x} y={y} w={100} h={78} label={label} onPress={go} motion={{ v: motions[i], kind: 'swing' }}>
              <Path d="M50 0V26" stroke={t.sTagLine} strokeWidth={1.5} />
              <Path d="M4 26H96L100 32V70L96 76H4L0 70V32Z" fill={t.sTag} />
              <Circle cx={50} cy={32} r={2.5} fill={t.sTagLine} />
              <Circle cx={12} cy={34} r={3.5} fill={band} />
              {wrap2(name, 14).map((ln, k) => <SvgText key={k} x={50} y={46 + k * 12} fontSize={11} textAnchor="middle" fontFamily={fonts.display} fill="#2A1D12">{ln}</SvgText>)}
              <SvgText x={50} y={71} fontSize={8.5} textAnchor="middle" fontFamily={fonts.mono} fill="#5A4128">{state}</SvgText>
            </Item>
          );
        }
        if (world === 'rustic') {
          return (
            <Item key={th.id} f={f} x={[34, 122, 70][i]} y={[222, 230, 280][i]} w={86} h={58} rotate={[-3, 2.5, -1.5][i]} label={label} onPress={go}>
              <Rect width={86} height={58} rx={2} fill={t.sCard} />
              <Path d="M0 12H86" stroke={t.sRed} strokeWidth={1.5} />
              <Path d="M0 26H86M0 38H86M0 50H86" stroke={t.sCardLine} strokeWidth={0.8} />
              <Circle cx={43} cy={4} r={4} fill={band} />
              {wrap2(name, 11).map((ln, k) => <SvgText key={k} x={6} y={24 + k * 14} fontSize={13.5} fontFamily={fonts.hand} fontWeight="700" fill="#2B1D14">{ln}</SvgText>)}
              <SvgText x={6} y={53} fontSize={11} fontFamily={fonts.hand} fill="#5A4636">{state}</SvgText>
            </Item>
          );
        }
        if (world === 'space') {
          const [sx, sy] = [[40, 440], [358, 420], [300, 600]][i];
          const lx = [-32, -100, -64][i];
          const ly = [12, -52, 12][i];
          return (
            <Item key={th.id} f={f} x={sx + Math.min(lx, -14)} y={sy + Math.min(ly, -10)} w={128} h={64} label={label} onPress={go}
              motion={{ v: motions[i], kind: 'bob' }}>
              {(() => {
                const ox = -Math.min(lx, -14);
                const oy = -Math.min(ly, -10);
                return (
                  <>
                    <Rect x={ox - 14} y={oy - 3} width={9} height={6} fill={t.sPanel} />
                    <Rect x={ox + 5} y={oy - 3} width={9} height={6} fill={t.sPanel} />
                    <Rect x={ox - 5} y={oy - 5} width={10} height={10} rx={2} fill={t.sSat} />
                    <Circle cx={ox} cy={oy - 8} r={2.5} fill={band} />
                    <Rect x={ox + lx} y={oy + ly} width={112} height={34} rx={10} fill={t.sLabel} />
                    <SvgText x={ox + lx + 10} y={oy + ly + 15} fontSize={11} fontFamily={fonts.ui} fontWeight="600" fill={t.ink}>{clip(name, 16)}</SvgText>
                    <SvgText x={ox + lx + 10} y={oy + ly + 27} fontSize={8.5} fontFamily={fonts.mono} fill={t.ink2}>{state}</SvgText>
                  </>
                );
              })()}
            </Item>
          );
        }
        const [bx, by, br] = [[205, 296, 46], [312, 374, 38], [92, 392, 40]][i];
        const words = name.split(' ');
        const cut = Math.ceil(words.length / 2);
        return (
          <Item key={th.id} f={f} x={bx - br} y={by - br} w={br * 2} h={br * 2} label={label} onPress={go} motion={{ v: motions[i], kind: 'bob' }}>
            <Circle cx={br} cy={br} r={br - 1} fill={t.sBub} stroke={t.sBubRim} strokeWidth={1.5} />
            <Path d={`M${br * 0.45} ${br * 0.65}A${br * 0.7} ${br * 0.7} 0 0 1 ${br * 0.85} ${br * 0.34}`} fill="none" stroke="#fff" strokeWidth={2.5} strokeLinecap="round" opacity={0.8} />
            <SvgText x={br} y={br - 2} fontSize={11.5} textAnchor="middle" fontFamily={fonts.display} fontWeight="600" fill={t.ink}>{clip(words.slice(0, cut).join(' '), 11)}</SvgText>
            <SvgText x={br} y={br + 12} fontSize={11.5} textAnchor="middle" fontFamily={fonts.display} fontWeight="600" fill={t.ink}>{clip(words.slice(cut).join(' '), 11)}</SvgText>
            <SvgText x={br} y={br + 26} fontSize={8.5} textAnchor="middle" fontFamily={fonts.mono} fill={t.ink2}>{state}</SvgText>
          </Item>
        );
      })}
    </>
  );
}
