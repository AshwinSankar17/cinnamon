/**
 * The companion as the owner's assistant: tap it and it reads out the list. Its opener is in its own voice; every row
 * is an item from the ranked list (src/attention.ts), so it never says anything Brain did not report. A row opens the
 * thing; "Later" hides it on this phone for four hours. Dismissing decides nothing.
 */
import { useEffect, useRef } from 'react';
import { Animated, Easing, Pressable, ScrollView, Text, View } from 'react-native';
import type { Item } from '@/src/attention';
import { briefing, itemDetail, itemTitle, type Standup } from '@/src/standup';
import type { Tokens, World } from '@/src/worlds';

type Props = {
  world: World; t: Tokens; s: Standup; showcase: boolean; bottom: number; maxHeight: number;
  fonts: { display?: string; mono?: string; ui?: string; uiSemi?: string };
  onClose: () => void; onOpen: (href: string) => void; onLater: (key: string) => void;
};

export function Briefing({ world, t, s, showcase, bottom, maxHeight, fonts, onClose, onOpen, onLater }: Props) {
  const v = useRef(new Animated.Value(0)).current;
  useEffect(() => {
    Animated.timing(v, { toValue: 1, duration: 420, easing: Easing.out(Easing.exp), useNativeDriver: true }).start();
  }, [v]);
  const { intro, status } = briefing(world.id, s, showcase);
  const firstNews = s.items.findIndex((i) => i.tier === 5);
  const dot = (i: Item) => (i.tier <= 3 ? t.needs : i.tier === 4 ? t.accent : t.finding);
  const label = (text: string) => (
    <Text style={{ fontFamily: fonts.mono, fontSize: 10, letterSpacing: 0.6, color: t.ink3, marginTop: 12, marginBottom: 2 }}>{text}</Text>
  );
  return (
    <View style={{ position: 'absolute', left: 0, right: 0, top: 0, bottom: 0 }}>
      <Pressable accessibilityRole="button" accessibilityLabel="Close the list" onPress={onClose}
        style={{ position: 'absolute', left: 0, right: 0, top: 0, bottom: 0, backgroundColor: 'rgba(0,0,0,.18)' }} />
      <Animated.View accessibilityViewIsModal
        style={{ position: 'absolute', left: 12, right: 12, bottom, maxHeight, backgroundColor: t.raise, borderRadius: 22, borderWidth: 1, borderColor: t.line,
                 paddingHorizontal: 16, paddingTop: 14, paddingBottom: 12, shadowColor: '#000', shadowOpacity: 0.22, shadowRadius: 18, shadowOffset: { width: 0, height: 10 },
                 opacity: v, transform: [{ translateY: v.interpolate({ inputRange: [0, 1], outputRange: [24, 0] }) }] }}>
        <Text accessibilityRole="header" style={{ fontFamily: fonts.ui, fontSize: 14, lineHeight: 20, color: t.ink }}>
          <Text style={{ fontFamily: fonts.uiSemi }}>{world.pet.name}</Text>{` · ${intro}`}
        </Text>
        <ScrollView style={{ flexGrow: 0 }} contentContainerStyle={{ paddingBottom: 2 }}>
          {s.items.length && s.items[0].tier <= 4 ? label('TO DO') : null}
          {s.items.map((i, k) => (
            <View key={i.key}>
              {k === firstNews ? label('UPDATES') : null}
              <View style={{ flexDirection: 'row', alignItems: 'center', gap: 10, borderTopWidth: k && k !== firstNews ? 1 : 0, borderTopColor: t.line }}>
                <Pressable accessibilityRole="button" accessibilityLabel={`${itemTitle(i, showcase)}. ${itemDetail(i, showcase)}`} onPress={() => onOpen(i.href)}
                  style={({ pressed }) => ({ flex: 1, flexDirection: 'row', alignItems: 'center', gap: 10, minHeight: 50, opacity: pressed ? 0.6 : 1 })}>
                  <View style={{ width: 10, height: 10, borderRadius: 5, backgroundColor: dot(i) }} />
                  <View style={{ flex: 1 }}>
                    <Text numberOfLines={1} style={{ fontFamily: fonts.uiSemi, fontSize: 14.5, color: t.ink }}>{itemTitle(i, showcase)}</Text>
                    {itemDetail(i, showcase) ? <Text numberOfLines={1} style={{ fontFamily: fonts.ui, fontSize: 12, color: t.ink2 }}>{itemDetail(i, showcase)}</Text> : null}
                  </View>
                </Pressable>
                <Pressable accessibilityRole="button" accessibilityLabel={`Later: ${itemTitle(i, showcase)}`} onPress={() => onLater(i.key)} hitSlop={8}
                  style={{ paddingHorizontal: 10, paddingVertical: 6, borderRadius: 14, borderWidth: 1, borderColor: t.line }}>
                  <Text style={{ fontFamily: fonts.ui, fontSize: 12, color: t.ink2 }}>Later</Text>
                </Pressable>
              </View>
            </View>
          ))}
          {s.backlog.listen ? (
            <Pressable accessibilityRole="button" onPress={() => onOpen('/inbox')} style={{ minHeight: 40, justifyContent: 'center', borderTopWidth: 1, borderTopColor: t.line, marginTop: 4 }}>
              <Text style={{ fontFamily: fonts.ui, fontSize: 12.5, color: t.ink2 }}>
                {`${s.backlog.listen} older listening ${s.backlog.listen === 1 ? 'test' : 'tests'} in the inbox ›`}
              </Text>
            </Pressable>
          ) : null}
        </ScrollView>
        <Text style={{ marginTop: 8, fontFamily: fonts.mono, fontSize: 11, color: t.ink3 }}>{status}</Text>
      </Animated.View>
    </View>
  );
}
