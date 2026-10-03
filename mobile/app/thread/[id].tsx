import { useCallback, useMemo, useRef, useState } from 'react';
import { Alert, FlatList, KeyboardAvoidingView, Platform, Pressable, Text, TextInput, View } from 'react-native';
import { useFocusEffect, useLocalSearchParams } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { BlockView, TurnView, UserBubble } from '@/components/chat';
import { ConnectionBanner } from '@/components/ConnectionBanner';
import { Icon } from '@/components/Icon';
import { ThreadActionsSheet } from '@/components/ThreadActionsSheet';
import { BackHeader, Dot, EmptyNote, IconButton, Mono } from '@/components/ui';
import { api, describeError } from '@/src/api';
import { uuid } from '@/src/crypto';
import { duration, toMs } from '@/src/format';
import { useTick } from '@/src/hooks';
import { getEvents, getThread, lastSeq, mergeEvents, upsertEvents, upsertThreads } from '@/src/store';
import { colors, fonts, providerName, type } from '@/src/theme';
import { buildItems, hasOpenTurn, type Item } from '@/src/turns';
import type { BrainEvent, Thread } from '@/src/types';
import { brainSocket } from '@/src/ws';

/** Events after which the thread object (state, title, last_seq) is re-read. */
const REFRESH_ON = new Set(['turn.started', 'turn.ended', 'approval.requested', 'approval.decided', 'provider.status']);

export default function ThreadScreen() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const insets = useSafeAreaInsets();
  const [thread, setThread] = useState<Thread | null>(null);
  const [events, setEvents] = useState<BrainEvent[]>([]);
  const [loadErr, setLoadErr] = useState<string | null>(null);
  const [text, setText] = useState('');
  const [sending, setSending] = useState(false);
  const [sheet, setSheet] = useState<{ forkAtSeq?: number } | null>(null);
  const draftKey = useRef<string | null>(null);
  const listRef = useRef<FlatList<Item>>(null);
  const atBottom = useRef(true);

  const merge = useCallback((incoming: BrainEvent[]) => {
    if (incoming.length) setEvents((cur) => mergeEvents(cur, incoming));
  }, []);

  const catchUp = useCallback(async () => {
    const after = await lastSeq(id);
    await api.catchUp(id, after, async (page) => {
      await upsertEvents(page);
      merge(page);
    });
  }, [id, merge]);

  // Focus: local mirror → REST thread + catch-up → WS subscribe from the last seq we hold.
  useFocusEffect(
    useCallback(() => {
      let cancelled = false;
      let unsub: (() => void) | null = null;
      (async () => {
        const [localThread, localEvents] = await Promise.all([getThread(id), getEvents(id)]);
        if (cancelled) return;
        if (localThread) setThread(localThread);
        setEvents((cur) => mergeEvents(cur, localEvents));
        try {
          const t = await api.thread(id);
          if (cancelled) return;
          setThread(t);
          await upsertThreads([t]);
          await catchUp();
          setLoadErr(null);
        } catch (e) {
          if (!cancelled) setLoadErr(describeError(e));
        }
        if (cancelled) return;
        const after = await lastSeq(id);
        unsub = brainSocket.subscribe(id, after, (ev) => {
          merge([ev]);
          if (REFRESH_ON.has(ev.type)) {
            api.thread(id).then(
              (t) => {
                if (!cancelled) setThread(t);
                upsertThreads([t]).catch(() => undefined);
              },
              () => undefined,
            );
          }
        });
      })();
      return () => {
        cancelled = true;
        unsub?.();
      };
    }, [id, catchUp, merge]),
  );

  const items = useMemo(() => buildItems(events), [events]);
  const openTurn = hasOpenTurn(items);
  // Once the log holds any turn it is the source of truth; before that, trust the thread object.
  const running = items.some((i) => i.kind === 'turn') ? !!openTurn : thread?.state === 'running';
  const now = useTick(1000, running);

  const provider = thread?.provider ?? 'claude';
  const pName = providerName[provider] ?? provider;

  const onSend = async () => {
    const body = text.trim();
    if (!body) return;
    if (!draftKey.current) draftKey.current = uuid();
    setSending(true);
    try {
      await api.sendMessage(id, body, draftKey.current);
      setText('');
      draftKey.current = null;
      atBottom.current = true;
      if (!brainSocket.isOpen) await catchUp();
    } catch (e) {
      Alert.alert('Message not sent', `${describeError(e)}\n\nYour text is kept; sending again is safe.`);
    } finally {
      setSending(false);
    }
  };

  const onStop = async () => {
    try {
      await api.stopThread(id);
    } catch (e) {
      Alert.alert('Could not stop', describeError(e));
    }
  };

  const startedMs = openTurn ? toMs(openTurn.startedAt) : null;
  const subtitle = running ? (
    <View style={{ height: 24, flexDirection: 'row', alignItems: 'center', gap: 6 }}>
      <Dot color={colors.accent} size={7} />
      <Mono>
        {pName} working{startedMs ? ` · ${duration(now - startedMs)}` : ''}
      </Mono>
    </View>
  ) : (
    [thread?.repo_id, thread?.branch].filter(Boolean).join(' · ') || ' '
  );

  const showStop = running && !text.trim();

  return (
    <View style={{ flex: 1, backgroundColor: colors.surface }}>
      <BackHeader
        title={thread?.title ?? 'Thread'}
        subtitle={subtitle}
        backLabel="Back to threads"
        right={<IconButton name="dots" label="Thread actions" onPress={() => thread && setSheet({})} />}
      />
      <View style={{ paddingTop: 8 }}>
        <ConnectionBanner />
      </View>
      <KeyboardAvoidingView style={{ flex: 1 }} behavior={Platform.OS === 'ios' ? 'padding' : undefined}>
        <FlatList
          ref={listRef}
          data={items}
          keyExtractor={(it) => it.key}
          contentContainerStyle={{ paddingHorizontal: 16, paddingTop: 8, paddingBottom: 12, gap: 14 }}
          onScroll={(e) => {
            const { layoutMeasurement, contentOffset, contentSize } = e.nativeEvent;
            atBottom.current = layoutMeasurement.height + contentOffset.y >= contentSize.height - 80;
          }}
          scrollEventThrottle={100}
          onContentSizeChange={() => {
            if (atBottom.current) listRef.current?.scrollToEnd({ animated: false });
          }}
          ListEmptyComponent={<EmptyNote>{loadErr ?? 'No events yet. Say what you want done.'}</EmptyNote>}
          ListFooterComponent={loadErr && items.length ? <EmptyNote>{loadErr}</EmptyNote> : null}
          renderItem={({ item }) => {
            if (item.kind === 'user') return <UserBubble text={item.text} />;
            if (item.kind === 'turn') return <TurnView turn={item} now={now} onFork={(seq) => setSheet({ forkAtSeq: seq })} />;
            return <BlockView b={item.block} />;
          }}
        />

        <View style={{ paddingTop: 8, paddingHorizontal: 12, paddingBottom: Math.max(insets.bottom, 12), borderTopWidth: 1, borderTopColor: colors.hairline, backgroundColor: colors.surface }}>
          <View style={{ borderWidth: 1, borderColor: colors.border, borderRadius: 22, paddingTop: 6, paddingBottom: 6, paddingLeft: 14, paddingRight: 6, gap: 2 }}>
            <TextInput
              accessibilityLabel={`Message ${pName}`}
              value={text}
              onChangeText={setText}
              placeholder={running ? 'Queue a follow-up…' : `Ask ${pName}…`}
              placeholderTextColor={colors.text3}
              multiline
              style={{ minHeight: 36, maxHeight: 132, paddingTop: 8, paddingBottom: 6, paddingRight: 8, fontFamily: fonts.sans, fontSize: 15, color: colors.text }}
            />
            <View style={{ flexDirection: 'row', alignItems: 'center', gap: 4 }}>
              <View
                accessible
                accessibilityLabel={`Provider ${pName}`}
                style={{ height: 32, paddingHorizontal: 10, borderRadius: 999, borderWidth: 1, borderColor: colors.hairline, backgroundColor: colors.surface, justifyContent: 'center' }}
              >
                <Text style={{ fontFamily: fonts.sansSemi, fontSize: 12, color: colors.text }}>{pName}</Text>
              </View>
              {thread?.mode ? (
                <View style={{ height: 32, paddingHorizontal: 10, borderRadius: 999, borderWidth: 1, borderColor: colors.hairline, justifyContent: 'center' }}>
                  <Text style={{ fontFamily: fonts.sansSemi, fontSize: 12, color: colors.text2 }}>{thread.mode}</Text>
                </View>
              ) : null}
              <View style={{ flex: 1 }} />
              {showStop ? (
                <Pressable
                  accessibilityRole="button"
                  accessibilityLabel="Stop turn"
                  onPress={onStop}
                  style={({ pressed }) => [composerBtn, { backgroundColor: colors.accent, opacity: pressed ? 0.85 : 1 }]}
                >
                  <Icon name="stop" size={14} color={colors.onAccent} />
                </Pressable>
              ) : (
                <Pressable
                  accessibilityRole="button"
                  accessibilityLabel={running ? 'Queue message' : 'Send'}
                  disabled={!text.trim() || sending}
                  onPress={onSend}
                  style={({ pressed }) => [composerBtn, { backgroundColor: colors.black, opacity: !text.trim() || sending ? 0.4 : pressed ? 0.85 : 1 }]}
                >
                  <Icon name="send" size={18} color={colors.onBlack} strokeWidth={2.25} />
                </Pressable>
              )}
            </View>
          </View>
          {thread?.state === 'paused' ? (
            <Text style={[type.small, { paddingTop: 6, textAlign: 'center' }]}>Thread is paused; messages queue until it resumes.</Text>
          ) : null}
        </View>
      </KeyboardAvoidingView>

      {sheet && thread ? (
        <ThreadActionsSheet
          thread={thread}
          forkAtSeq={sheet.forkAtSeq}
          onClose={() => setSheet(null)}
          onChanged={(t) => {
            if (t.id === thread.id) setThread(t);
          }}
        />
      ) : null}
    </View>
  );
}

const composerBtn = {
  width: 44,
  height: 44,
  borderRadius: 22,
  alignItems: 'center' as const,
  justifyContent: 'center' as const,
};
