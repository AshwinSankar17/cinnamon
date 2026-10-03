import { Alert, FlatList, Pressable, RefreshControl, Text, View } from 'react-native';
import { router } from 'expo-router';
import { ClusterChip, ConnectionBanner } from '@/components/ConnectionBanner';
import { Button, Dot, EmptyNote, Mono, TabHeader } from '@/components/ui';
import { api, describeError } from '@/src/api';
import { useRemote } from '@/src/hooks';
import { colors, type } from '@/src/theme';
import type { Job } from '@/src/types';

const ACTIVE = new Set(['running', 'pending', 'configuring', 'completing', 'requeued', 'submitted']);

function dotFor(state: string): string {
  const s = state.toLowerCase();
  if (ACTIVE.has(s)) return s === 'pending' ? colors.waiting : colors.running;
  if (/fail|timeout|out_of_memory|cancel/.test(s)) return colors.failed;
  return colors.idle;
}

export default function RunsScreen() {
  const { data, error, loading, reload } = useRemote(() => api.jobs(), { pollMs: 30_000 });
  const jobs = [...(data ?? [])].sort(
    (a, b) => Number(ACTIVE.has(b.state.toLowerCase())) - Number(ACTIVE.has(a.state.toLowerCase())),
  );

  const cancel = (j: Job) =>
    Alert.alert('Cancel job?', `${j.name} (${j.job_id}) will be cancelled.`, [
      { text: 'Keep running', style: 'cancel' },
      {
        text: 'Cancel job',
        style: 'destructive',
        onPress: () => api.cancelJob(j.job_id).then(reload, (e) => Alert.alert('Could not cancel', describeError(e))),
      },
    ]);

  return (
    <View style={{ flex: 1, backgroundColor: colors.ground }}>
      <TabHeader title="Runs" right={<ClusterChip />} />
      <ConnectionBanner />
      <FlatList
        data={jobs}
        keyExtractor={(j) => j.job_id}
        contentContainerStyle={{ paddingHorizontal: 16, paddingBottom: 24, gap: 8 }}
        refreshControl={<RefreshControl refreshing={loading && !!data} onRefresh={reload} tintColor={colors.accent} />}
        ListEmptyComponent={<EmptyNote>{error ?? (data ? 'No jobs yet.' : 'Loading…')}</EmptyNote>}
        renderItem={({ item: j }) => (
          <View
            style={{
              backgroundColor: colors.surface,
              borderWidth: 1,
              borderColor: colors.hairline,
              borderRadius: 14,
              paddingVertical: 10,
              paddingHorizontal: 14,
              flexDirection: 'row',
              alignItems: 'center',
              gap: 12,
              minHeight: 64,
            }}
          >
            <Dot color={dotFor(j.state)} />
            <Pressable
              style={{ flex: 1, minWidth: 0, minHeight: 44, justifyContent: 'center', gap: 2 }}
              onPress={j.thread_id ? () => router.push(`/thread/${encodeURIComponent(j.thread_id as string)}`) : undefined}
            >
              <Text style={type.monoBody} numberOfLines={1}>
                {j.name}
              </Text>
              <Mono numberOfLines={1}>
                {[
                  j.job_id,
                  j.state.toLowerCase(),
                  j.gpus ? `${j.gpus} GPU` : null,
                  j.elapsed ? `${j.elapsed}${j.time_limit ? ` / ${j.time_limit}` : ''}` : null,
                ]
                  .filter(Boolean)
                  .join(' · ')}
              </Mono>
            </Pressable>
            {ACTIVE.has(j.state.toLowerCase()) ? (
              <Button label="Cancel" kind="destructive" compact onPress={() => cancel(j)} />
            ) : null}
          </View>
        )}
      />
    </View>
  );
}
