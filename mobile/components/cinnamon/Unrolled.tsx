/**
 * The roll, unrolled: the focus thread's history laid flat (spec §7.2). Same activity as the swirl, drawn as a ribbon:
 * thicker where the agent was busy, flecked where jobs failed, darker since the owner left, glowing at now.
 */
import { useEffect, useMemo, useRef } from 'react';
import { Animated, Easing, Pressable, Text, View, useWindowDimensions } from 'react-native';
import Svg, { Circle, Path, Text as SvgText } from 'react-native-svg';
import { binOf, edges, line, N, outline, pointAt, ribbonPoints, sinceIndex, widths, type Activity } from '@/src/roll';
import { useWorld, useWorldFont } from '@/src/world';

const RIBBON = ribbonPoints();

export function Unrolled({ open, onClose, activity, title, width }: { open: boolean; onClose: () => void; activity: Activity | null; title: string; width: number }) {
  const { tokens: t, world, showcase } = useWorld();
  const display = useWorldFont('display');
  const ui = useWorldFont('ui');
  const mono = useWorldFont('mono');
  const { height: H } = useWindowDimensions();
  const W = width; // the app column, which on desktop is narrower than the window
  const sheet = useRef(new Animated.Value(0)).current;
  useEffect(() => {
    Animated.timing(sheet, { toValue: open ? 1 : 0, duration: 500, easing: Easing.bezier(0.32, 0.72, 0, 1), useNativeDriver: true }).start();
  }, [open, sheet]);

  const g = useMemo(() => {
    if (!activity) return null;
    const Wd = widths(activity);
    const E = edges(RIBBON, Wd);
    const since = sinceIndex(activity);
    const flecks = activity.jobs.filter((j) => j[2]).map((j) => pointAt(RIBBON, binOf(activity, j[0])));
    const days: { label: string; y: number; x: number }[] = [];
    const d0 = new Date(activity.start * 1000);
    d0.setHours(24, 0, 0, 0);
    for (let d = d0.getTime() / 1000; d < activity.now && days.length < 10; d += 86400) {
      const p = pointAt(RIBBON, binOf(activity, d));
      days.push({ label: new Date(d * 1000).toLocaleDateString(undefined, { weekday: 'short', day: 'numeric' }).toUpperCase(), x: Math.min(p[0] - 22, 60), y: p[1] + 3 });
    }
    return { body: outline(E, 0, N - 1), fresh: since < N ? outline(E, since, N - 1) : '', trace: since < N - 1 ? line(RIBBON, since, N - 2) : '',
             flecks, days, tip: RIBBON[N - 1] };
  }, [activity]);

  const sheetH = Math.min(H * 0.86, 660);
  const svgW = W - 32;
  const svgH = Math.min(sheetH - 130, (svgW * 440) / 390);
  return (
    <View pointerEvents={open ? 'auto' : 'none'} style={{ position: 'absolute', left: 0, right: 0, top: 0, bottom: 0 }}>
      <Animated.View style={{ position: 'absolute', left: 0, right: 0, top: 0, bottom: 0, backgroundColor: 'rgba(10,6,4,.42)', opacity: sheet }}>
        <Pressable accessibilityLabel="Close history" onPress={onClose} style={{ flex: 1 }} />
      </Animated.View>
      <Animated.View
        accessibilityViewIsModal
        style={{ position: 'absolute', left: 0, right: 0, bottom: 0, height: sheetH, backgroundColor: t.raise, borderTopLeftRadius: 30, borderTopRightRadius: 30,
                 padding: 16, paddingTop: 10, gap: 4, transform: [{ translateY: sheet.interpolate({ inputRange: [0, 1], outputRange: [sheetH + 40, 0] }) }] }}
      >
        <View style={{ width: 40, height: 5, borderRadius: 3, backgroundColor: t.line, alignSelf: 'center' }} />
        <View style={{ flexDirection: 'row', alignItems: 'center' }}>
          <Text style={{ flex: 1, fontFamily: display, fontSize: 26 * world.displayScale, color: t.ink }}>Unrolled</Text>
          <Pressable accessibilityRole="button" accessibilityLabel="Done" onPress={onClose} style={{ minHeight: 44, paddingHorizontal: 8, justifyContent: 'center' }}>
            <Text style={{ fontFamily: ui, fontSize: 14, color: t.ink }}>Done</Text>
          </Pressable>
        </View>
        <Text style={{ fontFamily: ui, fontSize: 13, lineHeight: 18, color: t.ink2 }}>
          {showcase ? 'The roll laid flat: thicker where the agent was busy.' : `${title}, laid flat: thicker where the agent was busy, flecked where jobs failed, darker since you left.`}
        </Text>
        {g ? (
          <Svg width={svgW} height={svgH} viewBox="0 0 390 440" accessibilityLabel={`${title} history`}>
            {g.days.map((d) => <SvgText key={d.label + d.y} x={d.x} y={d.y} textAnchor="end" fontSize={9} fontFamily={mono} fill={t.ink3}>{d.label}</SvgText>)}
            <Path d={g.body} fill="#D9A066" />
            {g.fresh ? <Path d={g.fresh} fill="#8A4320" /> : null}
            {g.trace ? <Path d={g.trace} fill="none" stroke="#F3C98E" strokeWidth={1.2} strokeLinecap="round" /> : null}
            {g.flecks.map((p, i) => <Circle key={i} cx={p[0]} cy={p[1]} r={1.9} fill="#3B1E14" opacity={0.8} />)}
            <Circle cx={g.tip[0]} cy={g.tip[1]} r={12} fill="#FF8A3D" opacity={0.35} />
            <Circle cx={g.tip[0]} cy={g.tip[1]} r={5} fill="#FF8A3D" />
            <SvgText x={g.tip[0] + 14} y={g.tip[1] + 4} fontSize={9.5} fontFamily={mono} fill={t.ink2}>NOW</SvgText>
          </Svg>
        ) : (
          <Text style={{ fontFamily: ui, fontSize: 14, color: t.ink2, paddingVertical: 24 }}>No history yet.</Text>
        )}
      </Animated.View>
    </View>
  );
}
