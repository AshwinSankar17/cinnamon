import { useCallback, useState } from 'react';
import { Alert, Pressable, RefreshControl, ScrollView, Text, View } from 'react-native';
import { router } from 'expo-router';
import { ClusterChip, ConnectionBanner } from '@/components/ConnectionBanner';
import { Button, Card, Dot, EmptyNote, IconButton, Mono, ProviderPill, SectionLabel, TabHeader } from '@/components/ui';
import { api, describeError } from '@/src/api';
import { useConnection } from '@/src/connection';
import { hhmm, sinceLabel, toMs, word } from '@/src/format';
import { useRemote } from '@/src/hooks';
import { getMeta, getThreads, upsertThreads } from '@/src/store';
import { colors, providerShort, type } from '@/src/theme';
import type { Approval, Job, Pending, Thread } from '@/src/types';

type InboxData = { pending: Pending; running: Thread[]; byId: Record<string, Thread>; leftAt: number | null };

const RUNNING_JOB = new Set(['running', 'pending', 'configuring', 'completing', 'requeued', 'submitted']);

async function loadInbox(): Promise<InboxData> {
  const [pending, running, leftAt] = await Promise.all([
    api.pending(),
    api.threads({ state: 'running', archived: 0 }),
    getMeta('left_at'),
  ]);
  await upsertThreads([...pending.threads_needing_you, ...running]);
  const local = await getThreads(true);
  const byId: Record<string, Thread> = {};
  for (const t of local) byId[t.id] = t;
  return { pending, running, byId, leftAt: leftAt ? Number(leftAt) : null };
}

function sinceProse(d: InboxData, paused: boolean): string {
  const s = d.pending.since ?? {};
  if (typeof s.text === 'string' && s.text) return s.text;
  if (typeof s.summary === 'string' && s.summary) return s.summary;
  const parts: string[] = [];
  const done = d.pending.jobs.filter((j) => !RUNNING_JOB.has(j.state.toLowerCase()));
  const failed = done.filter((j) => /fail|timeout|out_of_memory|node_fail/i.test(j.state));
  const finished = done.length - failed.length;
  const jobs = (n: number) => (n === 1 ? 'job' : 'jobs');
  if (finished && failed.length) parts.push(`${cap(word(finished))} ${jobs(finished)} finished and ${word(failed.length)} failed.`);
  else if (finished) parts.push(`${cap(word(finished))} ${jobs(finished)} finished.`);
  else if (failed.length) parts.push(`${cap(word(failed.length))} ${jobs(failed.length)} failed.`);
  if (typeof s.files_changed === 'number' && s.files_changed > 0) parts.push(`${cap(word(s.files_changed))} files changed.`);
  const needs = d.pending.approvals.length + d.pending.threads_needing_you.length;
  if (d.running.length) parts.push(`${cap(word(d.running.length))} ${d.running.length === 1 ? 'thread is' : 'threads are'} working.`);
  if (paused) parts.push('Brain is paused; nothing new will start until you resume.');
  parts.push(needs ? `${cap(word(needs))} ${needs === 1 ? 'thing needs' : 'things need'} you.` : 'Nothing needs you.');
  return parts.join(' ');
}

function cap(s: string) {
  return s.charAt(0).toUpperCase() + s.slice(1);
}

export default function InboxScreen() {
  const conn = useConnection();
  const { data, error, loading, reload } = useRemote(loadInbox, { pollMs: 20_000 });
  const [pausing, setPausing] = useState(false);

  const togglePause = useCallback(async () => {
    setPausing(true);
    try {
      if (conn.paused) await api.resume();
      else await api.pause();
    } catch (e) {
      Alert.alert(conn.paused ? 'Could not resume' : 'Could not pause', describeError(e));
    } finally {
      setPausing(false);
    }
  }, [conn.paused]);

  const sinceMs = data ? (toMs(data.pending.since?.ts) ?? data.leftAt) : null;
  const runningJobs = data?.pending.jobs.filter((j) => RUNNING_JOB.has(j.state.toLowerCase())) ?? [];
  const doneJobs = data?.pending.jobs.filter((j) => !RUNNING_JOB.has(j.state.toLowerCase())) ?? [];

  return (
    <View style={{ flex: 1, backgroundColor: colors.ground }}>
      <TabHeader
        title="Inbox"
        right={
          <>
            <ClusterChip />
            <IconButton
              name={conn.paused ? 'play' : 'pause'}
              label={conn.paused ? 'Resume Brain' : 'Pause Brain'}
              bordered
              onPress={pausing ? undefined : togglePause}
            />
          </>
        }
      />
      <ConnectionBanner />
      <ScrollView
        contentContainerStyle={{ paddingHorizontal: 16, paddingTop: 4, paddingBottom: 24, gap: 12 }}
        refreshControl={<RefreshControl refreshing={loading && !!data} onRefresh={reload} tintColor={colors.accent} />}
      >
        {error && !data ? <EmptyNote>{error}</EmptyNote> : null}

        {data ? (
          <View style={{ gap: 4, paddingBottom: 6, borderBottomWidth: 1, borderBottomColor: colors.hairline }}>
            <SectionLabel style={{ paddingTop: 2 }}>
              {sinceMs ? `Since you left · ${sinceLabel(Date.now() - sinceMs)}` : 'Since you last looked'}
            </SectionLabel>
            <Text style={type.prose}>{sinceProse(data, conn.paused)}</Text>
          </View>
        ) : null}

        <SectionLabel>Needs you</SectionLabel>
        {data && !data.pending.approvals.length && !data.pending.threads_needing_you.length ? (
          <EmptyNote>Nothing is waiting on you.</EmptyNote>
        ) : null}
        {data?.pending.approvals.map((a) => (
          <ApprovalCard key={a.id} a={a} thread={data.byId[a.thread_id]} />
        ))}
        {data?.pending.threads_needing_you.map((t) => <NeedsThreadCard key={t.id} t={t} />)}

        <SectionLabel>Running</SectionLabel>
        {data && !runningJobs.length && !data.running.length ? <EmptyNote>Nothing is running.</EmptyNote> : null}
        <View style={{ gap: 8 }}>
          {runningJobs.map((j) => (
            <JobRow key={j.job_id} j={j} onChanged={reload} />
          ))}
          {data?.running.map((t) => <RunningThreadRow key={t.id} t={t} onChanged={reload} />)}
        </View>

        <SectionLabel>Done</SectionLabel>
        {data && !doneJobs.length ? <EmptyNote>No finished runs since you left.</EmptyNote> : null}
        <View style={{ gap: 8 }}>
          {doneJobs.map((j) => (
            <JobRow key={j.job_id} j={j} onChanged={reload} />
          ))}
        </View>
      </ScrollView>
    </View>
  );
}

function ApprovalCard({ a, thread }: { a: Approval; thread?: Thread }) {
  return (
    <Card tone="needs">
      <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
        {thread ? <ProviderPill provider={thread.provider} variant="outline" /> : null}
        <Text style={[type.cardTitle, { flex: 1 }]} numberOfLines={2}>
          {a.summary}
        </Text>
      </View>
      <Mono numberOfLines={1}>
        {[thread?.title, a.action_type, `pending since ${hhmm(a.created_at)}`].filter(Boolean).join(' · ')}
      </Mono>
      <View style={{ flexDirection: 'row', gap: 8 }}>
        <Button label="Review" kind="primary" style={{ flex: 1 }} onPress={() => router.push(`/approval/${encodeURIComponent(a.id)}`)} />
        <Button label="Open thread" style={{ flex: 1 }} onPress={() => router.push(`/thread/${encodeURIComponent(a.thread_id)}`)} />
      </View>
    </Card>
  );
}

function NeedsThreadCard({ t }: { t: Thread }) {
  return (
    <Card tone="needs">
      <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
        <ProviderPill provider={t.provider} variant="outline" />
        <Text style={[type.cardTitle, { flex: 1 }]} numberOfLines={2}>
          {t.title}
        </Text>
      </View>
      <Mono numberOfLines={1}>{[t.repo_id, t.branch, t.state.replace('_', ' ')].filter(Boolean).join(' · ')}</Mono>
      <Button label="Open thread" kind="primary" onPress={() => router.push(`/thread/${encodeURIComponent(t.id)}`)} />
    </Card>
  );
}

function jobDot(state: string): string {
  const s = state.toLowerCase();
  if (RUNNING_JOB.has(s)) return colors.running;
  if (/fail|timeout|out_of_memory|cancel/.test(s)) return colors.failed;
  return colors.waiting;
}

function JobRow({ j, onChanged }: { j: Job; onChanged: () => void }) {
  const running = RUNNING_JOB.has(j.state.toLowerCase());
  const cancel = () =>
    Alert.alert('Cancel job?', `${j.name} (${j.job_id}) will be cancelled.`, [
      { text: 'Keep running', style: 'cancel' },
      {
        text: 'Cancel job',
        style: 'destructive',
        onPress: () => api.cancelJob(j.job_id).then(onChanged, (e) => Alert.alert('Could not cancel', describeError(e))),
      },
    ]);
  return (
    <View style={rowStyle}>
      <Dot color={jobDot(j.state)} />
      <Pressable
        style={{ flex: 1, minWidth: 0, gap: 2, minHeight: 44, justifyContent: 'center' }}
        onPress={() => (j.thread_id ? router.push(`/thread/${encodeURIComponent(j.thread_id)}`) : router.push('/runs'))}
      >
        <Text style={type.monoBody} numberOfLines={1}>
          {j.name}
        </Text>
        <Mono numberOfLines={1}>
          {[j.job_id, j.state.toLowerCase(), j.gpus ? `${j.gpus} GPU` : null, j.elapsed && j.time_limit ? `${j.elapsed} / ${j.time_limit}` : j.elapsed]
            .filter(Boolean)
            .join(' · ')}
        </Mono>
      </Pressable>
      {running ? <Button label="Cancel" kind="destructive" compact onPress={cancel} /> : null}
    </View>
  );
}

function RunningThreadRow({ t, onChanged }: { t: Thread; onChanged: () => void }) {
  return (
    <View style={rowStyle}>
      <Dot color={colors.running} />
      <Pressable
        style={{ flex: 1, minWidth: 0, gap: 2, minHeight: 44, justifyContent: 'center' }}
        onPress={() => router.push(`/thread/${encodeURIComponent(t.id)}`)}
      >
        <Text style={type.rowTitle} numberOfLines={1}>
          {t.title}
        </Text>
        <Mono numberOfLines={1}>{[providerShort[t.provider], t.repo_id, t.branch].filter(Boolean).join(' · ')}</Mono>
      </Pressable>
      <Button
        label="Stop"
        compact
        onPress={() => api.stopThread(t.id).then(onChanged, (e) => Alert.alert('Could not stop', describeError(e)))}
      />
    </View>
  );
}

const rowStyle = {
  backgroundColor: colors.surface,
  borderWidth: 1,
  borderColor: colors.hairline,
  borderRadius: 14,
  paddingVertical: 10,
  paddingHorizontal: 14,
  flexDirection: 'row' as const,
  alignItems: 'center' as const,
  gap: 12,
  minHeight: 64,
};

