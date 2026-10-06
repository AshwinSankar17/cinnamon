/** A SLURM job your agent submitted: its script name, the thread it came from, state, and cancel while running. */
import { Alert, Pressable, Text, View } from 'react-native';
import { router } from 'expo-router';
import { api, describeError } from '@/src/api';
import { ago, duration } from '@/src/format';
import { colors, type } from '@/src/theme';
import type { Job } from '@/src/types';
import { Button, Dot, Mono } from './ui';

export const ACTIVE = new Set(['running', 'pending', 'configuring', 'completing', 'requeued', 'submitted']);

export function jobColor(state: string): string {
  const s = state.toLowerCase();
  if (ACTIVE.has(s)) return s === 'pending' ? colors.waiting : colors.running;
  if (/fail|timeout|out_of_memory|cancel/.test(s)) return colors.failed;
  return colors.idle;
}

export function jobLabel(j: Job): string {
  return j.label || j.name;
}

export function JobRow({ j, onChanged }: { j: Job; onChanged: () => void }) {
  const running = ACTIVE.has(j.state.toLowerCase());
  const cancel = () =>
    Alert.alert('Cancel job?', `${jobLabel(j)} (job ${j.job_id}) will be cancelled.`, [
      { text: 'Keep running', style: 'cancel' },
      {
        text: 'Cancel job',
        style: 'destructive',
        onPress: () => api.cancelJob(j.job_id).then(onChanged, (e) => Alert.alert('Could not cancel', describeError(e))),
      },
    ]);
  const elapsed = typeof j.elapsed === 'number' ? duration(j.elapsed * 1000) : j.elapsed;
  const state = j.state.toLowerCase().replace(/_/g, ' ');
  return (
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
      <Dot color={jobColor(j.state)} />
      <Pressable
        accessibilityRole="button"
        accessibilityLabel={`${jobLabel(j)}, ${state}${j.thread_title ? `, from ${j.thread_title}` : ''}`}
        style={{ flex: 1, minWidth: 0, minHeight: 44, justifyContent: 'center', gap: 2 }}
        onPress={j.thread_id ? () => router.push(`/thread/${encodeURIComponent(j.thread_id as string)}`) : undefined}
      >
        <Text style={type.rowTitle} numberOfLines={1}>
          {jobLabel(j)}
        </Text>
        <Mono numberOfLines={1}>
          {[
            j.thread_title,
            running ? state : `${state}${j.exit_code ? ` (exit ${j.exit_code})` : ''} · ${ago(j.updated_at ? j.updated_at * 1000 : null)} ago`,
            j.gpus ? `${j.gpus} GPU` : null,
            running && elapsed ? elapsed : null,
          ]
            .filter(Boolean)
            .join(' · ')}
        </Mono>
      </Pressable>
      {running ? <Button label="Cancel" kind="destructive" compact onPress={cancel} /> : null}
    </View>
  );
}
