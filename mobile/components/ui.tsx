/** Shared STYLE-V2 primitives: headers, cards, chips, buttons, pills, status words. */
import type { ReactNode } from 'react';
import {
  ActivityIndicator,
  Pressable,
  StyleSheet,
  Text,
  View,
  type PressableProps,
  type StyleProp,
  type TextStyle,
  type ViewStyle,
} from 'react-native';
import { router } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { colors, fonts, providerShort, radius, stateColor, type, type ThreadState } from '@/src/theme';
import { Icon, type IconName } from './Icon';

export function Dot({ color, size = 8 }: { color: string; size?: number }) {
  return <View style={{ width: size, height: size, borderRadius: size / 2, backgroundColor: color }} />;
}

export function SectionLabel({ children, style }: { children: ReactNode; style?: StyleProp<TextStyle> }) {
  return <Text style={[type.section, { paddingHorizontal: 2, paddingTop: 4 }, style]}>{children}</Text>;
}

export function Mono({ children, style, numberOfLines }: { children: ReactNode; style?: StyleProp<TextStyle>; numberOfLines?: number }) {
  return (
    <Text style={[type.mono, style]} numberOfLines={numberOfLines}>
      {children}
    </Text>
  );
}

export function Card({ children, style, tone }: { children: ReactNode; style?: StyleProp<ViewStyle>; tone?: 'needs' | 'plain' }) {
  return (
    <View
      style={[
        s.card,
        tone === 'needs' && { backgroundColor: colors.needsYouTint, borderColor: colors.needsYou },
        style,
      ]}
    >
      {children}
    </View>
  );
}

/** One white card holding rows separated by hairlines (E-Threads list). */
export function ListCard({ children, style }: { children: ReactNode; style?: StyleProp<ViewStyle> }) {
  return <View style={[s.listCard, style]}>{children}</View>;
}

type BtnKind = 'primary' | 'secondary' | 'destructive' | 'black';
export function Button({
  label,
  kind = 'secondary',
  onPress,
  onLongPress,
  delayLongPress,
  disabled,
  busy,
  style,
  icon,
  accessibilityLabel,
  compact,
}: {
  label: string;
  kind?: BtnKind;
  onPress?: () => void;
  onLongPress?: () => void;
  delayLongPress?: number;
  disabled?: boolean;
  busy?: boolean;
  style?: StyleProp<ViewStyle>;
  icon?: IconName;
  accessibilityLabel?: string;
  compact?: boolean;
}) {
  const palette = {
    primary: { bg: colors.accent, fg: colors.onAccent, border: colors.accent },
    secondary: { bg: colors.surface, fg: colors.text, border: colors.border },
    destructive: { bg: colors.failedTint, fg: colors.failed, border: colors.failedBorder },
    black: { bg: colors.black, fg: colors.onBlack, border: colors.black },
  }[kind];
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={accessibilityLabel ?? label}
      onPress={onPress}
      onLongPress={onLongPress}
      delayLongPress={delayLongPress}
      disabled={disabled || busy}
      style={({ pressed }) => [
        s.button,
        compact && { paddingHorizontal: 12 },
        { backgroundColor: palette.bg, borderColor: palette.border, opacity: disabled ? 0.5 : pressed ? 0.85 : 1 },
        style,
      ]}
    >
      {busy ? (
        <ActivityIndicator color={palette.fg} />
      ) : (
        <>
          {icon ? <Icon name={icon} color={palette.fg} size={18} /> : null}
          <Text style={[type.button, { color: palette.fg, fontSize: compact ? 14 : 15 }]}>{label}</Text>
        </>
      )}
    </Pressable>
  );
}

export function IconButton({
  name,
  label,
  onPress,
  onLongPress,
  bordered,
  filled,
  color,
  size = 44,
  iconSize = 20,
  style,
}: {
  name: IconName;
  label: string;
  onPress?: () => void;
  onLongPress?: () => void;
  bordered?: boolean;
  filled?: string;
  color?: string;
  size?: number;
  iconSize?: number;
  style?: StyleProp<ViewStyle>;
}) {
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={label}
      onPress={onPress}
      onLongPress={onLongPress}
      hitSlop={size < 44 ? (44 - size) / 2 : 0}
      style={({ pressed }) => [
        {
          width: size,
          height: size,
          borderRadius: filled ? size / 2 : radius.button,
          alignItems: 'center',
          justifyContent: 'center',
          opacity: pressed ? 0.7 : 1,
        },
        bordered && { backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.hairline },
        filled ? { backgroundColor: filled } : null,
        style,
      ]}
    >
      <Icon name={name} size={iconSize} color={color ?? colors.text} />
    </Pressable>
  );
}

export function Chip({
  label,
  selected,
  onPress,
  dot,
  mono,
}: {
  label: string;
  selected?: boolean;
  onPress?: () => void;
  dot?: string;
  mono?: boolean;
}) {
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityState={{ selected: !!selected }}
      onPress={onPress}
      hitSlop={4}
      style={[
        s.chip,
        selected
          ? { backgroundColor: colors.accentTint, borderColor: colors.accentTint }
          : { backgroundColor: colors.surface, borderColor: colors.border },
      ]}
    >
      {dot ? <Dot color={dot} /> : null}
      <Text
        style={{
          fontFamily: mono ? fonts.mono : selected ? fonts.sansSemi : fonts.sansMedium,
          fontSize: 12,
          color: selected ? colors.accent : colors.text2,
        }}
      >
        {label}
      </Text>
    </Pressable>
  );
}

/** Provider pill: "CC" / "CX" / "GM". variant 'outline' is the Inbox/Approval card pill. */
export function ProviderPill({ provider, variant = 'soft' }: { provider: string; variant?: 'soft' | 'outline' | 'avatar' }) {
  const label = providerShort[provider] ?? provider.slice(0, 2).toUpperCase();
  if (variant === 'avatar') {
    return (
      <View style={s.avatar}>
        <Text style={{ fontFamily: fonts.monoMedium, fontSize: 10, color: colors.onBlack }}>{label}</Text>
      </View>
    );
  }
  return (
    <View
      style={[
        s.pill,
        variant === 'outline'
          ? { height: 22, paddingHorizontal: 8, backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.hairline }
          : { backgroundColor: colors.surface2 },
      ]}
    >
      <Text style={[type.monoPill, { color: variant === 'outline' ? colors.text : colors.text2, fontSize: variant === 'outline' ? 12 : 11 }]}>
        {label}
      </Text>
    </View>
  );
}

export function Badge({ label, tone }: { label: string; tone: 'ok' | 'fail' | 'accent' | 'muted' }) {
  const p = {
    ok: { bg: colors.runningTint, fg: colors.running },
    fail: { bg: colors.failedTint, fg: colors.failed },
    accent: { bg: colors.accentTint, fg: colors.accent },
    muted: { bg: colors.surface2, fg: colors.text2 },
  }[tone];
  return (
    <View style={[s.pill, { backgroundColor: p.bg, height: 20 }]}>
      <Text style={[type.monoPill, { color: p.fg }]}>{label}</Text>
    </View>
  );
}

/** Small-caps status word preceded by an 8 px dot. */
export function StatusWord({ state, label }: { state: ThreadState; label?: string }) {
  const c = stateColor[state];
  return (
    <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6 }}>
      <Dot color={c} />
      <Text style={[type.status, { color: c }]}>{(label ?? state.replace('_', ' ')).toLowerCase()}</Text>
    </View>
  );
}

/** Tab-root header: serif 28 title + trailing actions (E-Inbox / E-Threads). */
export function TabHeader({ title, right }: { title: string; right?: ReactNode }) {
  const insets = useSafeAreaInsets();
  return (
    <View style={[s.tabHeader, { paddingTop: insets.top + 10 }]}>
      <Text style={[type.screenTitle, { flex: 1 }]} numberOfLines={1}>
        {title}
      </Text>
      {right}
    </View>
  );
}

/** Sub-screen header: back chevron · centred serif title + mono subtitle · trailing 44 px action (E-Chat). */
export function BackHeader({
  title,
  subtitle,
  right,
  backLabel = 'Back',
  surface = colors.surface,
}: {
  title: string;
  subtitle?: ReactNode;
  right?: ReactNode;
  backLabel?: string;
  surface?: string;
}) {
  const insets = useSafeAreaInsets();
  return (
    <View style={[s.backHeader, { paddingTop: insets.top + 6, backgroundColor: surface }]}>
      <IconButton
        name="back"
        label={backLabel}
        iconSize={22}
        onPress={() => (router.canGoBack() ? router.back() : router.replace('/'))}
      />
      <View style={{ flex: 1, minWidth: 0, alignItems: 'center' }}>
        <Text style={type.subTitle} numberOfLines={1}>
          {title}
        </Text>
        {typeof subtitle === 'string' ? (
          <Mono numberOfLines={1} style={{ lineHeight: 24 }}>
            {subtitle}
          </Mono>
        ) : (
          subtitle
        )}
      </View>
      {right ?? <View style={{ width: 44 }} />}
    </View>
  );
}

export function Row({ children, style, ...rest }: { children: ReactNode; style?: StyleProp<ViewStyle> } & PressableProps) {
  return (
    <Pressable {...rest} style={({ pressed }) => [{ opacity: pressed ? 0.8 : 1 }, style as ViewStyle]}>
      {children}
    </Pressable>
  );
}

export function Hairline() {
  return <View style={{ height: StyleSheet.hairlineWidth * 2, backgroundColor: colors.hairline }} />;
}

export function EmptyNote({ children }: { children: ReactNode }) {
  return <Text style={[type.small, { paddingHorizontal: 2, paddingVertical: 8 }]}>{children}</Text>;
}

const s = StyleSheet.create({
  card: {
    backgroundColor: colors.surface,
    borderWidth: 1,
    borderColor: colors.hairline,
    borderRadius: radius.card,
    paddingHorizontal: 14,
    paddingVertical: 12,
    gap: 8,
  },
  listCard: {
    backgroundColor: colors.surface,
    borderWidth: 1,
    borderColor: colors.hairline,
    borderRadius: radius.card,
    overflow: 'hidden',
  },
  button: {
    minHeight: 44,
    paddingHorizontal: 16,
    borderRadius: radius.button,
    borderWidth: 1,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 8,
  },
  chip: {
    height: 36,
    paddingHorizontal: 10,
    borderRadius: radius.pill,
    borderWidth: 1,
    flexDirection: 'row',
    alignItems: 'center',
    gap: 5,
  },
  pill: { height: 20, paddingHorizontal: 6, borderRadius: radius.pill, alignItems: 'center', justifyContent: 'center' },
  avatar: {
    width: 24,
    height: 24,
    borderRadius: 12,
    backgroundColor: colors.black,
    alignItems: 'center',
    justifyContent: 'center',
  },
  tabHeader: { paddingHorizontal: 16, paddingBottom: 10, flexDirection: 'row', alignItems: 'center', gap: 8 },
  backHeader: {
    paddingHorizontal: 8,
    paddingBottom: 8,
    flexDirection: 'row',
    alignItems: 'center',
    gap: 4,
    borderBottomWidth: 1,
    borderBottomColor: colors.hairline,
  },
});
