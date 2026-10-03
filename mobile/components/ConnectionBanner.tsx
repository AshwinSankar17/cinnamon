/**
 * Honest connection state. Never shows "live" unless the WebSocket is actually open, and never
 * collapses "identity mismatch" into a generic error.
 */
import { Pressable, Text, View } from 'react-native';
import { router } from 'expo-router';
import { statusOf, useConnection, type ConnectionStatus } from '@/src/connection';
import { colors, fonts, type } from '@/src/theme';
import { Dot } from './ui';

const COPY: Record<ConnectionStatus, { dot: string; bg: string; fg: string; title: string; body: string }> = {
  unpaired: {
    dot: colors.idle,
    bg: colors.surface2,
    fg: colors.text2,
    title: 'Not paired',
    body: 'Pair this phone with Brain to see threads and runs.',
  },
  offline: {
    dot: colors.idle,
    bg: colors.surface2,
    fg: colors.text2,
    title: 'Offline',
    body: 'This phone has no network. Showing what was saved on the device.',
  },
  unreachable: {
    dot: colors.failed,
    bg: colors.failedTint,
    fg: colors.failed,
    title: 'Endpoint unreachable',
    body: 'The relay did not answer. Brain may still be running; push will reach you if it moves.',
  },
  identity_mismatch: {
    dot: colors.failed,
    bg: colors.failedTint,
    fg: colors.failed,
    title: 'Identity mismatch',
    body: 'Something answered at this endpoint but could not prove it is your paired Brain. Nothing was sent.',
  },
  revoked: {
    dot: colors.failed,
    bg: colors.failedTint,
    fg: colors.failed,
    title: 'Device revoked',
    body: 'Brain no longer accepts this phone. Pair again from the cluster.',
  },
  paused: {
    dot: colors.needsYou,
    bg: colors.needsYouTint,
    fg: colors.needsYou,
    title: 'Brain paused',
    body: 'No new turns or jobs will start until you resume.',
  },
  connecting: {
    dot: colors.waiting,
    bg: colors.waitingTint,
    fg: colors.waiting,
    title: 'Connecting',
    body: 'Reachable over HTTPS; live updates are reconnecting.',
  },
  live: { dot: colors.running, bg: colors.runningTint, fg: colors.running, title: 'Live', body: 'Connected to Brain.' },
};

/** Full-width banner. Hidden when live unless `showLive`. */
export function ConnectionBanner({ showLive = false }: { showLive?: boolean }) {
  const c = useConnection();
  const st = statusOf(c);
  if (st === 'live' && !showLive) return null;
  const copy = COPY[st];
  const actionable = st === 'unpaired' || st === 'revoked' || st === 'identity_mismatch';
  return (
    <Pressable
      accessibilityRole={actionable ? 'button' : 'text'}
      onPress={actionable ? () => router.push('/pair') : undefined}
      style={{
        marginHorizontal: 16,
        marginBottom: 8,
        minHeight: 44,
        borderRadius: 10,
        backgroundColor: copy.bg,
        paddingHorizontal: 12,
        paddingVertical: 8,
        flexDirection: 'row',
        alignItems: 'center',
        gap: 10,
      }}
    >
      <Dot color={copy.dot} />
      <View style={{ flex: 1, minWidth: 0 }}>
        <Text style={[type.status, { color: copy.fg }]}>{copy.title.toLowerCase()}</Text>
        <Text style={[type.small, { color: colors.text2 }]}>{copy.body}</Text>
      </View>
      {actionable ? <Text style={{ fontFamily: fonts.sansSemi, fontSize: 13, color: colors.accent }}>Pair</Text> : null}
    </Pressable>
  );
}

/** Header pill: cluster label with a state dot (E-Inbox "aqua-hpc" chip). */
export function ClusterChip() {
  const c = useConnection();
  const st = statusOf(c);
  return (
    <View
      accessible
      accessibilityLabel={`Cluster ${c.label ?? 'unknown'}, ${COPY[st].title}`}
      style={{
        flexDirection: 'row',
        alignItems: 'center',
        gap: 6,
        height: 36,
        paddingHorizontal: 12,
        borderRadius: 999,
        backgroundColor: colors.surface,
        borderWidth: 1,
        borderColor: colors.hairline,
        maxWidth: 150,
      }}
    >
      <Dot color={COPY[st].dot} />
      <Text numberOfLines={1} style={{ fontFamily: fonts.mono, fontSize: 12, color: colors.text2 }}>
        {c.label ?? 'unpaired'}
      </Text>
    </View>
  );
}
