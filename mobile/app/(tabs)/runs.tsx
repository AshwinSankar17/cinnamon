import { useState } from 'react';
import { Pressable, RefreshControl, ScrollView, Text, View } from 'react-native';
import { ConnectionBanner } from '@/components/ConnectionBanner';
import { Icon } from '@/components/Icon';
import { ACTIVE, JobRow, jobColor } from '@/components/JobRow';
import { Dot, EmptyNote, Mono, TabHeader } from '@/components/ui';
import { api } from '@/src/api';
import { ago } from '@/src/format';
import { useRemote } from '@/src/hooks';
import { colors, type } from '@/src/theme';
import type { Job } from '@/src/types';

const PREVIEW = 5;

type Group = { key: string; title: string; jobs: Job[]; running: number; failed: number; last: number };

function groupJobs(jobs: Job[]): Group[] {
  const m = new Map<string, Group>();
  for (const j of jobs) {
    const key = j.thread_id ?? 'other';
    const g = m.get(key) ?? { key, title: j.thread_title || (j.thread_id ? 'Thread' : 'Other jobs'),
      jobs: [], running: 0, failed: 0, last: 0 };
    g.jobs.push(j);
    if (ACTIVE.has(j.state.toLowerCase())) g.running++;
    else if (jobColor(j.state) === colors.failed) g.failed++;
    g.last = Math.max(g.last, j.updated_at ?? j.submitted_at ?? 0);
    m.set(key, g);
  }
  // threads with running jobs first, then most recently active
  return [...m.values()].sort((a, b) => Number(b.running > 0) - Number(a.running > 0) || b.last - a.last);
}

/** SLURM jobs your agents submitted, one collapsible row per thread. */
export default function RunsScreen() {
  const { data, error, loading, reload } = useRemote(() => api.jobs(), { pollMs: 30_000 });
  const [open, setOpen] = useState<Record<string, boolean>>({});
  const groups = groupJobs(data ?? []);

  return (
    <View style={{ flex: 1, backgroundColor: colors.ground }}>
      <TabHeader title="Runs" />
      <ConnectionBanner />
      <ScrollView
        contentContainerStyle={{ paddingHorizontal: 16, paddingBottom: 24, gap: 8 }}
        refreshControl={<RefreshControl refreshing={loading && !!data} onRefresh={reload} tintColor={colors.accent} />}
      >
        {error && !data ? <EmptyNote>{error}</EmptyNote> : null}
        {data && !groups.length ? (
          <EmptyNote>No jobs yet. When an agent submits a SLURM job, it shows up here while it runs.</EmptyNote>
        ) : null}
        {groups.map((g) => (
          <ThreadGroup
            key={g.key}
            g={g}
            open={open[g.key] ?? g.running > 0}
            onToggle={() => setOpen((o) => ({ ...o, [g.key]: !(o[g.key] ?? g.running > 0) }))}
            onChanged={reload}
          />
        ))}
      </ScrollView>
    </View>
  );
}

function ThreadGroup({ g, open, onToggle, onChanged }: { g: Group; open: boolean; onToggle: () => void; onChanged: () => void }) {
  const [all, setAll] = useState(false);
  const done = g.jobs.length - g.running - g.failed;
  const summary = [g.running ? `${g.running} running` : null, done ? `${done} done` : null, g.failed ? `${g.failed} failed` : null]
    .filter(Boolean)
    .join(' · ');
  const shown = all ? g.jobs : g.jobs.slice(0, PREVIEW);
  return (
    <View style={{ backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.hairline, borderRadius: 14, overflow: 'hidden' }}>
      <Pressable
        accessibilityRole="button"
        accessibilityState={{ expanded: open }}
        accessibilityLabel={`${g.title}: ${summary}`}
        onPress={onToggle}
        style={({ pressed }) => ({ minHeight: 60, flexDirection: 'row', alignItems: 'center', gap: 12, paddingHorizontal: 14,
          paddingVertical: 10, opacity: pressed ? 0.8 : 1 })}
      >
        <Dot color={g.running ? colors.running : g.failed ? colors.failed : colors.idle} />
        <View style={{ flex: 1, minWidth: 0, gap: 2 }}>
          <Text style={type.rowTitle} numberOfLines={1}>
            {g.title}
          </Text>
          <Mono numberOfLines={1}>
            {summary}
            {g.last ? ` · ${ago(g.last * 1000)} ago` : ''}
          </Mono>
        </View>
        <Icon name={open ? 'chevronUp' : 'chevronDown'} size={16} color={colors.text3} strokeWidth={2} />
      </Pressable>
      {open ? (
        <View>
          {shown.map((j) => (
            <JobRow key={j.job_id} j={j} onChanged={onChanged} nested />
          ))}
          {!all && g.jobs.length > PREVIEW ? (
            <Pressable accessibilityRole="button" onPress={() => setAll(true)} style={{ minHeight: 44, justifyContent: 'center', paddingHorizontal: 14, borderTopWidth: 1, borderTopColor: colors.hairline }}>
              <Text style={[type.label, { color: colors.accent }]}>Show all {g.jobs.length}</Text>
            </Pressable>
          ) : null}
        </View>
      ) : null}
    </View>
  );
}
