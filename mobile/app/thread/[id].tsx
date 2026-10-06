import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Alert, FlatList, KeyboardAvoidingView, Platform, Pressable, Text, TextInput, View } from 'react-native';
import { useFocusEffect, useLocalSearchParams } from 'expo-router';
import * as DocumentPicker from 'expo-document-picker';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { BlockView, TurnView, UserBubble } from '@/components/chat';
import { ConnectionBanner } from '@/components/ConnectionBanner';
import { filterSlash, SlashMenu, slashQuery, type SlashItem } from '@/components/SlashMenu';
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
import type { BrainEvent, Provider, Thread } from '@/src/types';
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
  const [skills, setSkills] = useState<SlashItem[] | null>(null);
  const [files, setFiles] = useState<DocumentPicker.DocumentPickerAsset[]>([]);
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

  // "/" menu: the provider's skills + Brain's own commands. Reloaded when the thread switches provider.
  useEffect(() => {
    if (!thread?.provider) return;
    let live = true;
    setSkills(null);
    api
      .skills(id)
      .then((s) => live && setSkills(s))
      .catch(() => live && setSkills([]));
    return () => {
      live = false;
    };
  }, [id, thread?.provider]);
  const brainCommands = useMemo<SlashItem[]>(
    () => [
      ...(['claude', 'codex', 'agy'] as Provider[])
        .filter((p) => p !== provider)
        .map((p) => ({
          name: `model ${p}`,
          description: `Switch this thread to ${providerName[p]}; the full history carries over.`,
          kind: 'command' as const,
          scope: 'builtin' as const,
          brain: true as const,
        })),
      { name: 'stop', description: 'Stop the current turn.', kind: 'command', scope: 'builtin', brain: true },
    ],
    [provider],
  );
  const q = slashQuery(text);
  const slashItems = q === null ? [] : filterSlash([...brainCommands, ...(skills ?? [])], q);
  const pickSlash = (i: SlashItem) => setText(i.brain ? `/${i.name}` : `/${i.name} `);

  /** Brain's own slash commands run here instead of being sent to the agent. */
  const runBrainCommand = async (body: string): Promise<boolean> => {
    const m = /^\/model\s+(claude|codex|agy)\s*$/.exec(body);
    if (m) {
      try {
        const t = await api.switchProvider(id, m[1] as Provider);
        setThread(t);
        await upsertThreads([t]);
        setText('');
      } catch (e) {
        Alert.alert('Could not switch model', describeError(e));
      }
      return true;
    }
    if (/^\/stop\s*$/.test(body)) {
      setText('');
      await onStop();
      return true;
    }
    return false;
  };

  const pickFiles = async () => {
    const r = await DocumentPicker.getDocumentAsync({ multiple: true, copyToCacheDirectory: true });
    if (!r.canceled) setFiles((cur) => [...cur, ...r.assets].slice(0, 10));
  };

  const onSend = async () => {
    let body = text.trim();
    if (!body && !files.length) return;
    if (!files.length && (await runBrainCommand(body))) return;
    if (!draftKey.current) draftKey.current = uuid();
    setSending(true);
    try {
      if (files.length) {
        // upload first; the message then tells the agent where each file is (inside its own worktree)
        const up = [];
        for (const f of files) up.push(await api.attach(id, { uri: f.uri, name: f.name, mimeType: f.mimeType, file: f.file }));
        // paths are relative to the thread's worktree, which is every agent's working directory
        const list = up.map((a) => `- \`${a.rel}\` (${a.mime || 'file'}, ${Math.max(1, Math.round(a.size / 1024))} KB)`).join('\n');
        body = `${body || 'See the attached files.'}\n\nAttached files (in this repo's working directory):\n${list}`;
      }
      await api.sendMessage(id, body, draftKey.current);
      setFiles([]);
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

  // Only the latest turn can be retried: Brain runs it again with the same prompt.
  const lastTurn = items.findLast((i) => i.kind === 'turn');
  const retry = async () => {
    try {
      await api.retryTurn(id);
      atBottom.current = true;
      if (!brainSocket.isOpen) await catchUp();
    } catch (e) {
      Alert.alert('Could not retry', describeError(e));
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

  const showStop = running && !text.trim() && !files.length;

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
            if (item.kind === 'turn')
              return (
                <TurnView
                  turn={item}
                  now={now}
                  onFork={(seq) => setSheet({ forkAtSeq: seq })}
                  onRetry={item === lastTurn && !running ? retry : undefined}
                />
              );
            return <BlockView b={item.block} />;
          }}
        />

        <View style={{ paddingTop: 8, paddingHorizontal: 12, paddingBottom: Math.max(insets.bottom, 12), borderTopWidth: 1, borderTopColor: colors.hairline, backgroundColor: colors.surface }}>
          {q !== null ? <SlashMenu items={slashItems} onPick={pickSlash} loading={skills === null} /> : null}
          {files.length ? (
            <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 6, paddingBottom: 8 }}>
              {files.map((f, i) => (
                <Pressable
                  key={`${f.uri}-${i}`}
                  accessibilityRole="button"
                  accessibilityLabel={`Remove ${f.name}`}
                  onPress={() => setFiles((cur) => cur.filter((_, j) => j !== i))}
                  style={{ flexDirection: 'row', alignItems: 'center', gap: 6, height: 32, paddingHorizontal: 10, borderRadius: 999, borderWidth: 1, borderColor: colors.border, backgroundColor: colors.surface }}
                >
                  <Icon name="attach" size={13} color={colors.text2} />
                  <Text numberOfLines={1} style={{ maxWidth: 180, fontFamily: fonts.sansMedium, fontSize: 12, color: colors.text }}>
                    {f.name}
                  </Text>
                  <Icon name="cross" size={12} color={colors.text3} />
                </Pressable>
              ))}
            </View>
          ) : null}
          <View style={{ borderWidth: 1, borderColor: colors.border, borderRadius: 22, paddingTop: 6, paddingBottom: 6, paddingLeft: 14, paddingRight: 6, gap: 2 }}>
            <TextInput
              accessibilityLabel={`Message ${pName}`}
              value={text}
              onChangeText={setText}
              placeholder={running ? 'Queue a follow-up…' : `Ask ${pName}… (/ for skills)`}
              placeholderTextColor={colors.text3}
              multiline
              style={{ minHeight: 36, maxHeight: 132, paddingTop: 8, paddingBottom: 6, paddingRight: 8, fontFamily: fonts.sans, fontSize: 15, color: colors.text }}
            />
            <View style={{ flexDirection: 'row', alignItems: 'center', gap: 4 }}>
              <Pressable
                accessibilityRole="button"
                accessibilityLabel="Attach files"
                onPress={pickFiles}
                hitSlop={6}
                style={({ pressed }) => ({ width: 36, height: 32, alignItems: 'center', justifyContent: 'center', opacity: pressed ? 0.6 : 1 })}
              >
                <Icon name="attach" size={18} color={colors.text2} />
              </Pressable>
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
                  disabled={(!text.trim() && !files.length) || sending}
                  onPress={onSend}
                  style={({ pressed }) => [composerBtn, { backgroundColor: colors.black, opacity: (!text.trim() && !files.length) || sending ? 0.4 : pressed ? 0.85 : 1 }]}
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
