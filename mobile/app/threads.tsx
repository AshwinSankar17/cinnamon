import { useEffect, useMemo, useState } from 'react';
import { Pressable, RefreshControl, ScrollView, Text, TextInput, View } from 'react-native';
import { router } from 'expo-router';
import { ConnectionBanner } from '@/components/ConnectionBanner';
import { Icon } from '@/components/Icon';
import { plainText } from '@/components/Markdown';
import { ThreadActionsSheet } from '@/components/ThreadActionsSheet';
import { Chip, Dot, EmptyNote, IconButton, ListCard, Mono, ProviderPill, SectionLabel, BackHeader } from '@/components/ui';
import { api } from '@/src/api';
import { ago } from '@/src/format';
import { useRemote } from '@/src/hooks';
import { getThreads, upsertThreads } from '@/src/store';
import { colors, fonts, stateColor, stateLabel, type } from '@/src/theme';
import type { BrainEvent, Thread } from '@/src/types';


async function loadThreads(archived: boolean): Promise<{ threads: Thread[]; stale: boolean }> {
  try {
    const remote = await api.threads({ archived: archived ? 1 : 0 });
    await upsertThreads(remote);
    return { threads: remote, stale: false };
  } catch (e) {
    const local = (await getThreads(true)).filter((t) => !!t.archived === archived);
    if (local.length) return { threads: local, stale: true };
    throw e;
  }
}

type Filter = 'all' | 'needs_you' | 'running';

function lastEventLine(t: Thread): string {
  const e = t.last_event as BrainEvent | null | undefined;
  if (e) {
    switch (e.type) {
      case 'turn.ended':
        return plainText(e.payload.text ?? '') || (e.payload.stop_reason === 'tool_deferred' ? 'Waiting for your approval' : 'Turn finished');
      case 'assistant.text':
        return plainText(e.payload.text);
      case 'user.message':
        return `you: ${plainText(e.payload.text)}`;
      case 'tool.started':
        return e.payload.summary;
      case 'job.state':
        return `job ${e.payload.job_id} · ${e.payload.state.toLowerCase()}`;
      case 'approval.requested':
        return `approval · ${e.payload.summary}`;
      case 'provider.status':
        return `${e.payload.status.replace('_', ' ')} · ${e.payload.message ?? ''}`;
      case 'system.notice':
        return /^Thread created on /.test(e.payload.text) ? 'No messages yet' : e.payload.text;
      default:
        break;
    }
  }
  return [stateLabel[t.state]?.toLowerCase(), t.branch].filter(Boolean).join(' · ');
}

export default function ThreadsScreen() {
  const [showArchived, setShowArchived] = useState(false);
  const { data, error, loading, reload, setData } = useRemote(() => loadThreads(showArchived), { pollMs: 30_000 });
  useEffect(() => {
    reload();
  }, [showArchived, reload]);
  const archivedCount = useRemote(() => api.threads({ archived: 1 }).then((x) => x.length), { pollMs: 120_000 }).data ?? 0;
  const [q, setQ] = useState('');
  const [filter, setFilter] = useState<Filter>('all');
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({});
  const [sheet, setSheet] = useState<Thread | null>(null);

  const threads = data?.threads ?? [];
  const counts = useMemo(() => {
    const c: Record<string, number> = {};
    for (const t of threads) c[t.state] = (c[t.state] ?? 0) + 1;
    return c;
  }, [threads]);

  const filtered = useMemo(() => {
    const needle = q.trim().toLowerCase();
    return threads
      .filter((t) => !!t.archived === showArchived)
      .filter((t) => filter === 'all' || t.state === filter)
      .filter(
        (t) =>
          !needle ||
          [t.title, t.repo_id, t.branch ?? '', lastEventLine(t)].some((s) => s.toLowerCase().includes(needle)),
      );
  }, [threads, q, filter, showArchived]);

  const pinned = filtered.filter((t) => t.pinned);
  const groups = useMemo(() => {
    const m = new Map<string, Thread[]>();
    for (const t of filtered.filter((x) => !x.pinned)) {
      const arr = m.get(t.repo_id) ?? [];
      arr.push(t);
      m.set(t.repo_id, arr);
    }
    return [...m.entries()].sort((a, b) => a[0].localeCompare(b[0]));
  }, [filtered]);

  const onChanged = (t: Thread) => {
    setData((d) => (d ? { ...d, threads: d.threads.map((x) => (x.id === t.id ? t : x)) } : d));
    reload();
  };

  return (
    <View style={{ flex: 1, backgroundColor: colors.ground }}>
      <BackHeader
        title="Threads"
        surface={colors.ground}
        right={
          <View style={{ flexDirection: 'row', gap: 8 }}>
            <IconButton name="refresh" label="Import terminal sessions" bordered onPress={() => router.push('/import')} />
            <IconButton name="plus" label="Start a thread, run or idea" bordered onPress={() => router.push('/new')} />
          </View>
        }
      />
      <ConnectionBanner />
      <ScrollView
        keyboardShouldPersistTaps="handled"
        contentContainerStyle={{ paddingHorizontal: 16, paddingBottom: 24, gap: 8 }}
        refreshControl={<RefreshControl refreshing={loading && !!data} onRefresh={reload} tintColor={colors.accent} />}
      >
        <View
          style={{
            height: 44,
            flexDirection: 'row',
            alignItems: 'center',
            gap: 8,
            paddingHorizontal: 12,
            backgroundColor: colors.surface,
            borderWidth: 1,
            borderColor: colors.border,
            borderRadius: 12,
          }}
        >
          <Icon name="search" color={colors.text3} />
          <TextInput
            accessibilityLabel="Search threads"
            value={q}
            onChangeText={setQ}
            placeholder="Search threads, branches, repos…"
            placeholderTextColor={colors.text3}
            style={{ flex: 1, height: 40, fontFamily: fonts.sans, fontSize: 15, color: colors.text, padding: 0 }}
            returnKeyType="search"
            autoCorrect={false}
            autoCapitalize="none"
          />
        </View>

        <View style={{ flexDirection: 'row', gap: 6, paddingVertical: 4 }}>
          {(['all', 'needs_you', 'running'] as Filter[]).map((f) => (
            <Chip
              key={f}
              label={f === 'all' ? 'All' : `${stateLabel[f]}${counts[f] ? ` (${counts[f]})` : ''}`}
              dot={f === 'all' ? undefined : stateColor[f]}
              selected={filter === f}
              onPress={() => setFilter(f)}
            />
          ))}
        </View>

        {data?.stale ? (
          <View style={{ minHeight: 44, borderRadius: 10, backgroundColor: colors.surface2, paddingHorizontal: 12, justifyContent: 'center' }}>
            <Text style={type.small}>Showing threads saved on this phone; Brain did not answer.</Text>
          </View>
        ) : null}
        {error && !data ? <EmptyNote>{error}</EmptyNote> : null}
        {showArchived ? (
          <Pressable accessibilityRole="button" onPress={() => setShowArchived(false)} style={{ minHeight: 44, justifyContent: 'center' }}>
            <Text style={[type.label, { color: colors.accent }]}>‹ Back to active threads</Text>
          </Pressable>
        ) : null}
        {data && !filtered.length ? (
          <EmptyNote>{q || filter !== 'all' ? 'No threads match.' : showArchived ? 'Nothing archived.' : 'No threads yet. Tap + to start one.'}</EmptyNote>
        ) : null}

        {pinned.length ? (
          <View style={{ gap: 6 }}>
            <SectionLabel>Pinned</SectionLabel>
            <ListCard>
              {pinned.map((t, i) => (
                <ThreadRow key={t.id} t={t} first={i === 0} showPin onActions={() => setSheet(t)} />
              ))}
            </ListCard>
          </View>
        ) : null}

        {groups.length === 1 ? (
          // one repo: no header, just the list
          <ListCard>
            {groups[0][1].map((t, i) => (
              <ThreadRow key={t.id} t={t} first={i === 0} onActions={() => setSheet(t)} />
            ))}
          </ListCard>
        ) : null}
        {groups.length > 1 && groups.map(([repo, list]) => {
          const isCollapsed = !!collapsed[repo];
          return (
            <ListCard key={repo}>
              <Pressable
                accessibilityRole="button"
                accessibilityState={{ expanded: !isCollapsed }}
                onPress={() => setCollapsed((c) => ({ ...c, [repo]: !isCollapsed }))}
                style={{ minHeight: 44, flexDirection: 'row', alignItems: 'center', gap: 6, paddingHorizontal: 14 }}
              >
                <Text style={{ fontFamily: fonts.monoMedium, fontSize: 12, color: colors.text }}>{repo}</Text>
                <Text style={{ fontFamily: fonts.mono, fontSize: 12, color: colors.text3, flex: 1 }}>· {list.length}</Text>
                <Icon name={isCollapsed ? 'chevronRight' : 'chevronDown'} size={16} color={colors.text3} strokeWidth={2} />
              </Pressable>
              {!isCollapsed && list.map((t) => <ThreadRow key={t.id} t={t} onActions={() => setSheet(t)} />)}
            </ListCard>
          );
        })}
        {!showArchived && archivedCount ? (
          <Pressable
            accessibilityRole="button"
            onPress={() => setShowArchived(true)}
            style={{ minHeight: 44, flexDirection: 'row', alignItems: 'center', gap: 6, paddingHorizontal: 2 }}
          >
            <Icon name="archive" size={16} color={colors.text3} />
            <Text style={[type.small, { flex: 1 }]}>Archived · {archivedCount}</Text>
            <Icon name="chevronRight" size={14} color={colors.text3} strokeWidth={2} />
          </Pressable>
        ) : null}
      </ScrollView>
      <ThreadActionsSheet thread={sheet} onClose={() => setSheet(null)} onChanged={onChanged} />
    </View>
  );
}

function ThreadRow({ t, first, showPin, onActions }: { t: Thread; first?: boolean; showPin?: boolean; onActions: () => void }) {
  return (
    <View
      style={{
        flexDirection: 'row',
        alignItems: 'center',
        minHeight: 50,
        borderTopWidth: first ? 0 : 1,
        borderTopColor: colors.hairline,
      }}
    >
      <Pressable
        accessibilityRole="button"
        accessibilityHint="Long-press for pin, mute, model, archive"
        accessibilityActions={[{ name: 'longpress', label: 'Thread actions' }]}
        onAccessibilityAction={(e) => e.nativeEvent.actionName === 'longpress' && onActions()}
        onPress={() => router.push(`/thread/${encodeURIComponent(t.id)}`)}
        onLongPress={onActions}
        style={({ pressed }) => ({
          flex: 1,
          minWidth: 0,
          flexDirection: 'row',
          alignItems: 'flex-start',
          gap: 10,
          paddingVertical: 9,
          paddingHorizontal: 14,
          opacity: pressed ? 0.8 : 1,
        })}
      >
        <View style={{ height: 22, justifyContent: 'center' }}>
          <Dot color={stateColor[t.state] ?? colors.idle} />
        </View>
        <View style={{ flex: 1, minWidth: 0 }}>
          <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
            {showPin ? <Icon name="pin" size={14} color={colors.text2} strokeWidth={2} /> : null}
            <Text style={[type.rowTitle, { flex: 1, minWidth: 0 }]} numberOfLines={1}>
              {t.title}
            </Text>
            {t.muted ? <Icon name="mute" size={14} color={colors.text3} /> : null}
            <ProviderPill provider={t.provider} />
            <Mono style={{ color: colors.text3 }}>{ago(t.updated_at)}</Mono>
          </View>
          <Text numberOfLines={1} style={[type.small, { color: colors.text2 }]}>
            {lastEventLine(t)}
          </Text>
        </View>
      </Pressable>
    </View>
  );
}
