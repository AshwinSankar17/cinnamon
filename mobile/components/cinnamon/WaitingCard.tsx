/**
 * What needs the owner, at thumb height, in each world's own form (spec §7.2): the ticket that says Order up, a note
 * on twine, a note with a red pin, an incoming transmission, a bottle washed up. Always the same place and the same
 * words; only the material changes. It carries the top of the ranked list (src/attention.ts) and gets louder with
 * the rank: only an approval or a thread asking for you pulses. With nothing to do it turns into the companion's quiet
 * status line. Long-press is "Later" (four hours, this phone only).
 */
import { useEffect, useRef, useState } from 'react';
import { Animated, Easing, Pressable, Text, View } from 'react-native';
import Svg, { Circle, G, Path, Rect } from 'react-native-svg';
import type { Item } from '@/src/attention';
import { itemDetail, itemTitle } from '@/src/standup';
import type { Tokens, World } from '@/src/worlds';

type Props = { world: World; t: Tokens; item: Item | null; calm: { title: string; detail: string }; more: number; live: boolean; showcase: boolean; petName: string | null;
                fonts: { display?: string; mono?: string; ui?: string }; onPress: () => void; onLater: () => void };

const KICKER: Record<Item['kind'], string | null> = { approval: null, needs: null, failed: 'JOB FAILED', listen: 'READY TO LISTEN', news: 'NEWS' };
const ICON: Record<Item['kind'], string> = { approval: 'M12 4v9M12 18h.01', needs: 'M12 4v9M12 18h.01', failed: 'M7 7l10 10M17 7L7 17', listen: 'M7 4.5v15l13-7.5z', news: 'M12 12h.01' };

export function WaitingCard({ world, t, item, calm, more, live, showcase, petName, fonts, onPress, onLater }: Props) {
  const [w, setW] = useState(366);
  const ring = useRef(new Animated.Value(0)).current;
  useEffect(() => {
    if (!live || !item || item.tier > 2) return; // only what blocks an agent pulses
    const a = Animated.sequence([Animated.delay(1600), Animated.timing(ring, { toValue: 1, duration: 1600, easing: Easing.out(Easing.cubic), useNativeDriver: true }),
                                 Animated.timing(ring, { toValue: 0, duration: 0, useNativeDriver: true }),
                                 Animated.timing(ring, { toValue: 1, duration: 1600, easing: Easing.out(Easing.cubic), useNativeDriver: true })]);
    a.start();
    return () => a.stop();
  }, [live, ring, item?.key, item?.tier]);
  const space = world.id === 'space';
  const ink = space ? '#E4E8F7' : world.id === 'tide' ? t.ink : '#2A1A10';
  const ink2 = space ? '#A8B0CC' : world.id === 'tide' ? t.ink2 : '#5E4636';
  const kicker = space ? '#A9B8FF' : world.id === 'tide' ? t.ink2 : '#8A6F5A';
  const h = item ? 66 : 52;
  const dot = !item ? null : item.tier <= 3 ? t.needs : item.tier === 4 ? t.accent : t.finding;
  const glyph = item && item.tier <= 3 ? '#1A120D' : t.raise;
  const kick = [petName?.toUpperCase(), item ? KICKER[item.kind] ?? world.need : null].filter(Boolean).join('  ·  ');
  const title = item ? itemTitle(item, showcase) : calm.title;
  const detail = item ? itemDetail(item, showcase) : calm.detail;
  const zig = 'M0 4Q0 0 4 0H' + (w - 4) + 'Q' + w + ' 0 ' + w + ' 4V' + (h - 7) +
    Array.from({ length: Math.floor(w / 12) }, (_, k) => `L${w - 6 - k * 12} ${h}L${w - 12 - k * 12} ${h - 7}`).join('') + `L0 ${h - 7}Z`;
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={item ? `${kick.toLowerCase()}: ${title}. ${detail}${more ? `. ${more} more waiting` : ''}` : `${calm.title} ${calm.detail}`}
      accessibilityHint={item ? 'Long-press for Later' : 'Opens your list'}
      accessibilityActions={item ? [{ name: 'activate' }, { name: 'longpress', label: 'Later' }] : undefined}
      onAccessibilityAction={(e) => (e.nativeEvent.actionName === 'longpress' ? onLater() : onPress())}
      onPress={onPress}
      onLongPress={item ? onLater : undefined}
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
            <Path d={`M14 20H${w - 14}M14 ${h - 14}H${w - 26}`} stroke={t.sTagLine} strokeWidth={1.2} opacity={0.3} />
            <Circle cx={w / 3} cy={5} r={2.5} fill={t.sTagLine} />
            <Circle cx={(2 * w) / 3} cy={5} r={2.5} fill={t.sTagLine} />
          </G>
        ) : null}
        {world.id === 'rustic' ? (
          <G>
            <Rect width={w} height={h} rx={3} fill={t.sCard} />
            <Path d={`M0 16H${w}M0 32H${w}M0 48H${w}`} stroke={t.sCardLine} strokeWidth={0.8} />
            <Circle cx={w - 14} cy={9} r={6} fill={t.sRed} />
          </G>
        ) : null}
        {space ? (
          <G>
            <Rect width={w} height={h} rx={20} fill="rgba(14,18,40,.88)" stroke="rgba(169,184,255,.55)" strokeWidth={1.5} />
            <G transform={`translate(${w - 28} ${h / 2})`}>
              <Path d="M-4 4a6 6 0 0 1 0 -8M-9 9a13 13 0 0 1 0 -18M-14 14a20 20 0 0 1 0 -28" fill="none" stroke="#A9B8FF" strokeWidth={2} strokeLinecap="round" />
            </G>
          </G>
        ) : null}
        {world.id === 'tide' ? (
          <G>
            <G transform={`scale(1 ${h / 80})`}>
              <Path d={`M14 10Q14 0 28 0H${w - 66}Q${w - 50} 0 ${w - 44} 14L${w - 36} 26H${w - 16}Q${w - 8} 26 ${w - 8} 34V46Q${w - 8} 54 ${w - 16} 54H${w - 36}L${w - 44} 66Q${w - 50} 80 ${w - 66} 80H28Q14 80 14 70Z`}
                fill="rgba(220,250,245,.42)" stroke="rgba(255,255,255,.85)" strokeWidth={1.6} />
              <Rect x={w - 16} y={28} width={14} height={24} rx={4} fill="#B9874F" />
            </G>
          </G>
        ) : null}
      </Svg>
      <View style={{ flex: 1, flexDirection: 'row', alignItems: 'center', gap: 12, paddingLeft: world.id === 'tide' ? 28 : 14, paddingRight: space ? 50 : world.id === 'tide' ? 52 : 26 }}>
        {item && dot ? (
          <View style={{ width: 38, height: 38, borderRadius: 19, backgroundColor: dot, alignItems: 'center', justifyContent: 'center' }}>
            <Animated.View
              style={{ position: 'absolute', width: 48, height: 48, borderRadius: 24, borderWidth: 1.5, borderColor: dot,
                       opacity: ring.interpolate({ inputRange: [0, 1], outputRange: [item.tier <= 2 ? 0.8 : 0, 0] }),
                       transform: [{ scale: ring.interpolate({ inputRange: [0, 1], outputRange: [0.85, 1.25] }) }] }}
            />
            <Svg width={16} height={16} viewBox="0 0 24 24">
              <Path d={ICON[item.kind]} fill={item.kind === 'listen' ? glyph : 'none'} stroke={glyph}
                strokeWidth={item.kind === 'listen' ? 0 : item.kind === 'news' ? 7 : 2.6} strokeLinecap="round" />
            </Svg>
          </View>
        ) : null}
        <View style={{ flex: 1, gap: 1 }}>
          {item ? (
            <Text numberOfLines={1} style={{ fontFamily: fonts.mono, fontSize: 10, letterSpacing: 0.6, color: kicker }}>
              {kick}{more ? `  ·  +${more}` : ''}
            </Text>
          ) : null}
          <Text numberOfLines={1} style={{ fontFamily: item ? fonts.display : fonts.ui, fontSize: item ? 16.5 * world.displayScale : 13.5, color: item ? ink : ink2 }}>{title}</Text>
          {detail ? <Text numberOfLines={1} style={{ fontFamily: fonts.ui, fontSize: 11.5, color: ink2 }}>{detail}</Text> : null}
        </View>
      </View>
    </Pressable>
  );
}
