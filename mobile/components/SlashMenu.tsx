/** "/" suggestions above the composer: the thread provider's skills and commands, plus Brain's own commands. */
import { Pressable, ScrollView, Text, View } from 'react-native';
import { colors, fonts } from '@/src/theme';
import type { SkillInfo } from '@/src/types';

export type SlashItem = SkillInfo & { brain?: true };

/** The "/word" being typed (before the first space), or null when the menu should be closed. */
export function slashQuery(text: string): string | null {
  const m = /^\/(\S*)$/.exec(text);
  return m ? m[1].toLowerCase() : null;
}

export function filterSlash(items: SlashItem[], q: string): SlashItem[] {
  const hits = items.filter((i) => i.name.toLowerCase().includes(q) || (q.length > 1 && i.description.toLowerCase().includes(q)));
  const rank = (i: SlashItem) => (i.name.toLowerCase().startsWith(q) ? 0 : i.name.toLowerCase().includes(`:${q}`) ? 1 : 2);
  return hits.sort((a, b) => rank(a) - rank(b) || a.name.localeCompare(b.name)).slice(0, 30);
}

const TAG: Record<string, string> = { project: 'repo', user: 'yours', plugin: 'plugin', builtin: 'built-in' };

export function SlashMenu({ items, onPick, loading }: { items: SlashItem[]; onPick: (i: SlashItem) => void; loading?: boolean }) {
  return (
    <View
      accessibilityRole="menu"
      style={{
        maxHeight: 280,
        marginBottom: 8,
        borderWidth: 1,
        borderColor: colors.border,
        borderRadius: 14,
        backgroundColor: colors.surface,
        overflow: 'hidden',
      }}
    >
      {items.length === 0 ? (
        <Text style={{ padding: 12, fontFamily: fonts.sans, fontSize: 13, color: colors.text3 }}>
          {loading ? 'Loading skills…' : 'No matching skill or command.'}
        </Text>
      ) : (
        <ScrollView keyboardShouldPersistTaps="always">
          {items.map((i, n) => (
            <Pressable
              key={i.name}
              accessibilityRole="menuitem"
              accessibilityLabel={`/${i.name}: ${i.description}`}
              onPress={() => onPick(i)}
              style={({ pressed }) => ({
                paddingVertical: 8,
                paddingHorizontal: 12,
                gap: 2,
                borderTopWidth: n ? 1 : 0,
                borderTopColor: colors.hairline,
                backgroundColor: pressed ? colors.surface2 : colors.surface,
              })}
            >
              <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
                <Text style={{ flex: 1, fontFamily: fonts.mono, fontSize: 13, color: colors.text }} numberOfLines={1}>
                  /{i.name}
                  {i.args ? <Text style={{ color: colors.text3 }}> {i.args}</Text> : null}
                </Text>
                <Text style={{ fontFamily: fonts.sansMedium, fontSize: 11, color: i.brain ? colors.accent : colors.text3 }}>
                  {i.brain ? 'Brain' : `${i.kind} · ${TAG[i.scope] ?? i.scope}`}
                </Text>
              </View>
              {i.description ? (
                <Text style={{ fontFamily: fonts.sans, fontSize: 12, lineHeight: 16, color: colors.text2 }} numberOfLines={2}>
                  {i.description}
                </Text>
              ) : null}
            </Pressable>
          ))}
        </ScrollView>
      )}
    </View>
  );
}
