import { useCallback, useState } from 'react';
import { Alert, Pressable, RefreshControl, ScrollView, Text, View } from 'react-native';
import { router } from 'expo-router';
import { ClusterChip, ConnectionBanner } from '@/components/ConnectionBanner';
import { Icon } from '@/components/Icon';
import { ACTIVE, JobRow, jobColor, jobLabel } from '@/components/JobRow';
import { Button, Card, Dot, EmptyNote, IconButton, Mono, ProviderPill, SectionLabel, BackHeader } from '@/components/ui';
import { api, describeError } from '@/src/api';
import { useConnection } from '@/src/connection';
import { hhmm, sinceLabel, toMs, word } from '@/src/format';
import { useRemote } from '@/src/hooks';
import { getMeta, getThreads, upsertThreads } from '@/src/store';
import { colors, providerShort, type } from '@/src/theme';
import type { Approval, Job, Pending, Thread } from '@/src/types';

type InboxData = { pending: Pending; running: Thread[]; byId: Record<string, Thread>; leftAt: number | null };


async function loadInbox(): Promise<InboxData> {
  const leftAt = await getMeta('left_at');
  const [pending, running] = await Promise.all([
    api.pending(leftAt ? Number(leftAt) : null),
    api.threads({ state: 'running', archived: 0 }),
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
  const finished = s.jobs_finished ?? 0;
  const failedN = s.jobs_failed ?? 0;
  const jobs = (n: number) => (n === 1 ? 'job' : 'jobs');
  if (finished && failedN) parts.push(`${cap(word(finished))} ${jobs(finished)} finished and ${word(failedN)} failed.`);
  else if (finished) parts.push(`${cap(word(finished))} ${jobs(finished)} finished.`);
  else if (failedN) parts.push(`${cap(word(failedN))} ${jobs(failedN)} failed.`);
  if (typeof s.edits === 'number' && s.edits > 0) parts.push(`${cap(word(s.edits))} ${s.edits === 1 ? 'edit' : 'edits'}.`);
  const needs = d.pending.approvals.length + needsThreads(d.pending).length + (d.pending.listening?.length ?? 0);
  if (d.running.length) parts.push(`${cap(word(d.running.length))} ${d.running.length === 1 ? 'thread is' : 'threads are'} working.`);
  if (paused) parts.push('Brain is paused; nothing new will start until you resume.');
  parts.push(needs ? `${cap(word(needs))} ${needs === 1 ? 'thing needs' : 'things need'} you.` : 'Nothing needs you.');
  return parts.join(' ');
}

/** A thread waiting on an approval is listed once, as that approval. */
function needsThreads(p: Pending): Thread[] {
  const withApproval = new Set(p.approvals.map((a) => a.thread_id));
  return p.threads_needing_you.filter((t) => !withApproval.has(t.id));
}

function count(label: string, n: number | null): string {
  return n ? `${label} · ${n}` : label;
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
  const jobs = data?.pending.jobs ?? [];
  const runningJobs = jobs.filter((j) => ACTIVE.has(j.state.toLowerCase()));
  const doneJobs = jobs.filter((j) => !ACTIVE.has(j.state.toLowerCase()));
  const failed = doneJobs.filter((j) => jobColor(j.state) === colors.failed).length;
  const runningIds = new Set(data?.running.map((t) => t.id));
  const looseJobs = runningJobs.filter((j) => !j.thread_id || !runningIds.has(j.thread_id)); // jobs of threads not shown below
  const needs = data ? [...data.pending.approvals.map((a) => a.id), ...needsThreads(data.pending).map((t) => t.id)].length : 0;
  const listening = data?.pending.listening ?? [];
  const quiet = data && !needs && !listening.length && !runningJobs.length && !data.running.length;

  return (
    <View style={{ flex: 1, backgroundColor: colors.ground }}>
      <BackHeader
        title="Inbox"
        surface={colors.ground}
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

        {needs ? <SectionLabel>{count('Needs you', needs)}</SectionLabel> : null}
        {data?.pending.approvals.map((a) => (
          <ApprovalCard key={a.id} a={a} thread={data.byId[a.thread_id]} />
        ))}
        {data ? needsThreads(data.pending).map((t) => <NeedsThreadCard key={t.id} t={t} />) : null}

        {listening.length ? <SectionLabel>{count('Ready to listen', listening.length)}</SectionLabel> : null}
        {listening.map((l) => (
          <Card key={l.id} tone="needs">
            <Text style={type.cardTitle} numberOfLines={2}>
              {l.title}
            </Text>
            <Mono numberOfLines={1}>
              {[data?.byId[l.thread_id]?.title, `${l.n_items} sentences × ${l.n_systems} systems`].filter(Boolean).join(' · ')}
            </Mono>
            <Button label="Listen (blind)" kind="primary" style={{ maxWidth: 360 }} onPress={() => router.push(`/listen/${encodeURIComponent(l.id)}`)} />
          </Card>
        ))}

        {data?.running.length || looseJobs.length ? (
          <SectionLabel>{count('Working', (data?.running.length ?? 0) + looseJobs.length)}</SectionLabel>
        ) : null}
        {data?.running.map((t) => (
          <RunningThreadRow key={t.id} t={t} jobs={runningJobs.filter((j) => j.thread_id === t.id)} onChanged={reload} />
        ))}
        {looseJobs.map((j) => (
          <JobRow key={j.job_id} j={j} onChanged={reload} />
        ))}

        {doneJobs.length ? (
          <Pressable
            accessibilityRole="button"
            accessibilityLabel="See finished jobs in Runs"
            onPress={() => router.push('/runs')}
            style={({ pressed }) => ({ minHeight: 44, flexDirection: 'row', alignItems: 'center', gap: 8, opacity: pressed ? 0.7 : 1 })}
          >
            <Text style={[type.body, { flex: 1, color: colors.text2 }]}>
              {`${cap(word(doneJobs.length - failed))} ${doneJobs.length - failed === 1 ? 'job' : 'jobs'} finished`}
              {failed ? ` · ${word(failed)} failed` : ''}
            </Text>
            <Icon name="chevronRight" size={16} color={colors.text3} strokeWidth={2} />
          </Pressable>
        ) : null}

        {quiet ? <EmptyNote>All quiet. Nothing needs you and nothing is running.</EmptyNote> : null}
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
        {[thread?.title, a.decision_summary?.verb ?? a.action_type, `pending since ${hhmm(a.created_at)}`].filter(Boolean).join(' · ')}
      </Mono>
      <View style={{ flexDirection: 'row', gap: 8 }}>
        <Button label="Review" kind="primary" style={{ flex: 1, maxWidth: 360 }} onPress={() => router.push(`/approval/${encodeURIComponent(a.id)}`)} />
        {a.thread_id.startsWith('brain:') ? null : (
          <Button label="Open thread" style={{ flex: 1, maxWidth: 360 }} onPress={() => router.push(`/thread/${encodeURIComponent(a.thread_id)}`)} />
        )}
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
      <Button label="Open thread" kind="primary" style={{ maxWidth: 360 }} onPress={() => router.push(`/thread/${encodeURIComponent(t.id)}`)} />
    </Card>
  );
}

function RunningThreadRow({ t, jobs, onChanged }: { t: Thread; jobs: Job[]; onChanged: () => void }) {
  const job = jobs[0];
  return (
    <View style={rowStyle}>
      <Dot color={colors.running} />
      <Pressable
        accessibilityRole="button"
        style={{ flex: 1, minWidth: 0, gap: 2, minHeight: 44, justifyContent: 'center' }}
        onPress={() => router.push(`/thread/${encodeURIComponent(t.id)}`)}
      >
        <Text style={type.rowTitle} numberOfLines={1}>
          {t.title}
        </Text>
        <Mono numberOfLines={1}>
          {job ? `${jobLabel(job)} · ${job.state.toLowerCase()}${jobs.length > 1 ? ` · +${jobs.length - 1} more` : ''}` : [providerShort[t.provider], t.repo_id].filter(Boolean).join(' · ')}
        </Mono>
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
