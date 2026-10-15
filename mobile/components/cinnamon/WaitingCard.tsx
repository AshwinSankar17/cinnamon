/**
 * What needs the owner, at thumb height, in each world's own form (spec §7.2): the ticket that says Order up, a note
 * on twine, a note with a red pin, an incoming transmission, a bottle washed up. Always the same place and the same
 * words; only the material changes. Opens the approval, the listening test or the thread.
 */
import { useEffect, useRef, useState } from 'react';
import { Animated, Easing, Pressable, Text, View } from 'react-native';
import Svg, { Circle, G, Path, Rect } from 'react-native-svg';
import type { Waiting } from '@/src/standup';
import type { Tokens, World } from '@/src/worlds';

type Props = { world: World; t: Tokens; waiting: Waiting; more: number; live: boolean; fonts: { display?: string; mono?: string; ui?: string }; onPress: () => void };

export function WaitingCard({ world, t, waiting, more, live, fonts, onPress }: Props) {
  const [w, setW] = useState(366);
  const ring = useRef(new Animated.Value(0)).current;
  useEffect(() => {
    if (!live) return;
    const a = Animated.sequence([Animated.delay(1600), Animated.timing(ring, { toValue: 1, duration: 1600, easing: Easing.out(Easing.cubic), useNativeDriver: true }),
                                 Animated.timing(ring, { toValue: 0, duration: 0, useNativeDriver: true }),
                                 Animated.timing(ring, { toValue: 1, duration: 1600, easing: Easing.out(Easing.cubic), useNativeDriver: true })]);
    a.start();
    return () => a.stop();
  }, [live, ring, waiting.id]);
  const space = world.id === 'space';
  const ink = space ? '#E4E8F7' : world.id === 'tide' ? t.ink : '#2A1A10';
  const ink2 = space ? '#A8B0CC' : world.id === 'tide' ? t.ink2 : '#5E4636';
  const kicker = space ? '#A9B8FF' : world.id === 'tide' ? t.ink2 : '#8A6F5A';
  const h = 80;
  const zig = 'M0 4Q0 0 4 0H' + (w - 4) + 'Q' + w + ' 0 ' + w + ' 4V72' +
    Array.from({ length: Math.floor(w / 12) }, (_, k) => `L${w - 6 - k * 12} 80L${w - 12 - k * 12} 72`).join('') + 'L0 72Z';
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={`${world.need.toLowerCase()}: ${waiting.title}. ${waiting.detail}${more ? `. ${more} more waiting` : ''}`}
      onPress={onPress}
      onLayout={(e) => setW(Math.max(200, Math.round(e.nativeEvent.layout.width)))}
      style={({ pressed }) => ({ height: h, transform: [{ scale: pressed ? 0.97 : 1 }] })}
    >
      <Svg width={w} height={h} style={{ position: 'absolute' }}>
        {world.id === 'bakery' ? (
          <G>
            <Path d={zig} fill={t.sPaper} stroke="rgba(0,0,0,.08)" />
            <Rect width={w} height={6} rx={3} fill="#D2564A" />
          </G>
        ) : null}
        {world.id === 'forest' ? (
          <G>
            <Rect width={w} height={h} rx={14} fill={t.sTag} />
            <Path d={`M14 24H${w - 14}M14 56H${w - 26}`} stroke={t.sTagLine} strokeWidth={1.2} opacity={0.3} />
            <Circle cx={w / 3} cy={8} r={3} fill={t.sTagLine} />
            <Circle cx={(2 * w) / 3} cy={8} r={3} fill={t.sTagLine} />
          </G>
        ) : null}
        {world.id === 'rustic' ? (
          <G>
            <Rect width={w} height={h} rx={3} fill={t.sCard} />
            <Path d={`M0 18H${w}M0 36H${w}M0 54H${w}M0 72H${w}`} stroke={t.sCardLine} strokeWidth={0.8} />
            <Circle cx={w / 2} cy={4} r={6} fill={t.sRed} />
          </G>
        ) : null}
        {space ? (
          <G>
            <Rect width={w} height={h} rx={22} fill="rgba(14,18,40,.88)" stroke="rgba(169,184,255,.55)" strokeWidth={1.5} />
            <G transform={`translate(${w - 28} 40)`}>
              <Path d="M-4 4a6 6 0 0 1 0 -8M-9 9a13 13 0 0 1 0 -18M-14 14a20 20 0 0 1 0 -28" fill="none" stroke="#A9B8FF" strokeWidth={2} strokeLinecap="round" />
            </G>
          </G>
        ) : null}
        {world.id === 'tide' ? (
          <G>
            <Path d={`M14 10Q14 0 28 0H${w - 66}Q${w - 50} 0 ${w - 44} 14L${w - 36} 26H${w - 16}Q${w - 8} 26 ${w - 8} 34V46Q${w - 8} 54 ${w - 16} 54H${w - 36}L${w - 44} 66Q${w - 50} 80 ${w - 66} 80H28Q14 80 14 70Z`}
              fill="rgba(220,250,245,.42)" stroke="rgba(255,255,255,.85)" strokeWidth={1.6} />
            <Rect x={w - 16} y={28} width={14} height={24} rx={4} fill="#B9874F" />
          </G>
        ) : null}
      </Svg>
      <View style={{ flex: 1, flexDirection: 'row', alignItems: 'center', gap: 14, paddingLeft: world.id === 'tide' ? 30 : 18, paddingRight: 20 }}>
        <View style={{ width: 46, height: 46, borderRadius: 23, backgroundColor: t.needs, alignItems: 'center', justifyContent: 'center' }}>
          <Animated.View
            style={{ position: 'absolute', width: 58, height: 58, borderRadius: 29, borderWidth: 1.5, borderColor: t.needs,
                     opacity: ring.interpolate({ inputRange: [0, 1], outputRange: [0.8, 0] }),
                     transform: [{ scale: ring.interpolate({ inputRange: [0, 1], outputRange: [0.85, 1.25] }) }] }}
          />
          <Svg width={16} height={16} viewBox="0 0 24 24">
            <Path d={waiting.kind === 'listen' ? 'M7 4.5v15l13-7.5z' : 'M12 4v9M12 18h.01'} fill={waiting.kind === 'listen' ? '#1A120D' : 'none'}
              stroke="#1A120D" strokeWidth={waiting.kind === 'listen' ? 0 : 2.6} strokeLinecap="round" />
          </Svg>
        </View>
        <View style={{ flex: 1, gap: 2 }}>
          <Text numberOfLines={1} style={{ fontFamily: fonts.mono, fontSize: 10, letterSpacing: 0.6, color: kicker }}>
            {world.need}{more ? `  ·  +${more} MORE` : ''}
          </Text>
          <Text numberOfLines={1} style={{ fontFamily: fonts.display, fontSize: 19 * world.displayScale, color: ink }}>{waiting.title}</Text>
          <Text numberOfLines={1} style={{ fontFamily: fonts.ui, fontSize: 12, color: ink2 }}>{waiting.detail}</Text>
        </View>
      </View>
    </Pressable>
  );
}
