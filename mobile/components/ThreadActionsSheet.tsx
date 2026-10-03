/** Bottom sheet with thread actions (E-ThreadActions): pin, mute, fork, archive. */
import { useState } from 'react';
import { ActivityIndicator, Alert, Modal, Pressable, Text, View } from 'react-native';
import { router } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { api, describeError } from '@/src/api';
import { upsertThreads } from '@/src/store';
import { colors, fonts, providerShort, type } from '@/src/theme';
import type { Thread } from '@/src/types';
import { Icon, type IconName } from './Icon';
import { Button, Mono } from './ui';

type Props = {
  thread: Thread | null;
  onClose: () => void;
  onChanged?: (t: Thread) => void;
  /** Fork from a specific event seq (turn footer); default is the thread head. */
  forkAtSeq?: number;
};

export function ThreadActionsSheet({ thread, onClose, onChanged, forkAtSeq }: Props) {
  const insets = useSafeAreaInsets();
  const [busy, setBusy] = useState<string | null>(null);
  if (!thread) return null;

  const run = async (key: string, fn: () => Promise<Thread>, after?: (t: Thread) => void) => {
    setBusy(key);
    try {
      const t = await fn();
      await upsertThreads([t]);
      onChanged?.(t);
      onClose();
      after?.(t);
    } catch (e) {
      Alert.alert('Action failed', describeError(e));
    } finally {
      setBusy(null);
    }
  };

  const actions: { key: string; label: string; icon: IconName; danger?: boolean; onPress: () => void }[] = [
    {
      key: 'pin',
      label: thread.pinned ? 'Unpin' : 'Pin to top',
      icon: 'pin',
      onPress: () => run('pin', () => api.patchThread(thread.id, { pinned: !thread.pinned })),
    },
    {
      key: 'mute',
      label: thread.muted ? 'Unmute notifications' : 'Mute notifications',
      icon: 'mute',
      onPress: () => run('mute', () => api.patchThread(thread.id, { muted: !thread.muted })),
    },
    {
      key: 'fork',
      label: forkAtSeq ? `Fork from event ${forkAtSeq}` : 'Fork thread',
      icon: 'fork',
      onPress: () =>
        run(
          'fork',
          () => api.forkThread(thread.id, { title: `${thread.title} · fork`, mode: thread.mode, at_seq: forkAtSeq }),
          (t) => router.push(`/thread/${encodeURIComponent(t.id)}`),
        ),
    },
    {
      key: 'archive',
      label: thread.archived ? 'Unarchive' : 'Archive',
      icon: 'archive',
      danger: !thread.archived,
      onPress: () => run('archive', () => api.patchThread(thread.id, { archived: !thread.archived })),
    },
  ];

  return (
    <Modal transparent animationType="slide" visible onRequestClose={onClose} statusBarTranslucent>
      <View style={{ flex: 1, justifyContent: 'flex-end' }}>
        <Pressable
          accessibilityLabel="Close"
          onPress={onClose}
          style={{ position: 'absolute', top: 0, left: 0, right: 0, bottom: 0, backgroundColor: colors.scrim, opacity: 0.7 }}
        />
        <View
          style={{
            backgroundColor: colors.surface,
            borderTopLeftRadius: 14,
            borderTopRightRadius: 14,
            paddingTop: 8,
            paddingHorizontal: 16,
            paddingBottom: insets.bottom + 16,
            gap: 12,
          }}
        >
          <View style={{ alignItems: 'center', paddingTop: 2, paddingBottom: 4 }}>
            <View style={{ width: 36, height: 4, borderRadius: 2, backgroundColor: colors.border }} />
          </View>
          <View style={{ gap: 2 }}>
            <Text style={type.sheetTitle} numberOfLines={2}>
              {thread.title}
            </Text>
            <Mono numberOfLines={1}>
              {[thread.repo_id, thread.branch, providerShort[thread.provider]].filter(Boolean).join(' · ')}
            </Mono>
            {thread.parent_id ? (
              <Mono numberOfLines={1}>
                fork of {thread.parent_id}
                {thread.fork_turn !== null ? ` · turn ${thread.fork_turn}` : ''}
              </Mono>
            ) : null}
          </View>
          <View>
            {actions.map((a, i) => (
              <Pressable
                key={a.key}
                accessibilityRole="button"
                onPress={busy ? undefined : a.onPress}
                style={({ pressed }) => ({
                  height: 52,
                  flexDirection: 'row',
                  alignItems: 'center',
                  gap: 12,
                  paddingHorizontal: 2,
                  borderBottomWidth: i < actions.length - 1 ? 1 : 0,
                  borderBottomColor: colors.hairline,
                  opacity: pressed ? 0.7 : 1,
                })}
              >
                <Icon name={a.icon} color={a.danger ? colors.failed : colors.text} />
                <Text
                  style={{
                    flex: 1,
                    fontFamily: a.danger ? fonts.sansSemi : fonts.sansMedium,
                    fontSize: 15,
                    color: a.danger ? colors.failed : colors.text,
                  }}
                >
                  {a.label}
                </Text>
                {busy === a.key ? <ActivityIndicator color={colors.accent} /> : null}
              </Pressable>
            ))}
          </View>
          <Button label="Cancel" onPress={onClose} />
        </View>
      </View>
    </Modal>
  );
}
