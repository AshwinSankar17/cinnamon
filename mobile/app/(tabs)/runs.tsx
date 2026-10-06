import { useState } from 'react';
import { Pressable, RefreshControl, ScrollView, Text, View } from 'react-native';
import { ConnectionBanner } from '@/components/ConnectionBanner';
import { ACTIVE, JobRow } from '@/components/JobRow';
import { EmptyNote, SectionLabel, TabHeader } from '@/components/ui';
import { api } from '@/src/api';
import { useRemote } from '@/src/hooks';
import { colors, type } from '@/src/theme';

const RECENT_MS = 24 * 3600 * 1000;

/** SLURM jobs your agents submitted: what is running now, what finished in the last day. */
export default function RunsScreen() {
  const { data, error, loading, reload } = useRemote(() => api.jobs(), { pollMs: 30_000 });
  const [showOlder, setShowOlder] = useState(false);
  const jobs = data ?? [];
  const active = jobs.filter((j) => ACTIVE.has(j.state.toLowerCase()));
  const finished = jobs.filter((j) => !ACTIVE.has(j.state.toLowerCase()));
  const recent = finished.filter((j) => !j.updated_at || Date.now() - j.updated_at * 1000 < RECENT_MS);
  const older = finished.filter((j) => !recent.includes(j));

  return (
    <View style={{ flex: 1, backgroundColor: colors.ground }}>
      <TabHeader title="Runs" />
      <ConnectionBanner />
      <ScrollView
        contentContainerStyle={{ paddingHorizontal: 16, paddingBottom: 24, gap: 8 }}
        refreshControl={<RefreshControl refreshing={loading && !!data} onRefresh={reload} tintColor={colors.accent} />}
      >
        {error && !data ? <EmptyNote>{error}</EmptyNote> : null}
        {data && !jobs.length ? (
          <EmptyNote>No jobs yet. When an agent submits a SLURM job, it shows up here while it runs.</EmptyNote>
        ) : null}
        {active.length ? <SectionLabel>Running · {active.length}</SectionLabel> : null}
        {active.map((j) => (
          <JobRow key={j.job_id} j={j} onChanged={reload} />
        ))}
        {recent.length ? <SectionLabel>Last 24 hours</SectionLabel> : null}
        {recent.map((j) => (
          <JobRow key={j.job_id} j={j} onChanged={reload} />
        ))}
        {older.length && !showOlder ? (
          <Pressable accessibilityRole="button" onPress={() => setShowOlder(true)} style={{ minHeight: 44, justifyContent: 'center' }}>
            <Text style={[type.label, { color: colors.accent }]}>Show {older.length} older</Text>
          </Pressable>
        ) : null}
        {showOlder ? older.map((j) => <JobRow key={j.job_id} j={j} onChanged={reload} />) : null}
      </ScrollView>
    </View>
  );
}
