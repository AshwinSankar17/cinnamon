import { useCallback, useEffect, useState } from 'react';
import { ScrollView, Text, View } from 'react-native';
import { router } from 'expo-router';
import { BackHeader, Button, EmptyNote, ListCard, Mono, SectionLabel } from '@/components/ui';
import { api, describeError } from '@/src/api';
import { ago } from '@/src/format';
import { colors, type } from '@/src/theme';
import type { ImportCandidate } from '@/src/types';

/** Your terminal Claude/Codex sessions, by repo: import one to read and continue it here (a fork; the terminal's is untouched). */
export default function ImportScreen() {
  const [list, setList] = useState<ImportCandidate[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const load = useCallback(() => {
    api.imports().then(setList).catch((e) => setErr(describeError(e)));
  }, []);
  useEffect(load, [load]);

  const open = async (c: ImportCandidate) => {
    if (c.thread_id) return router.push(`/thread/${encodeURIComponent(c.thread_id)}`);
    setBusy(c.session_id);
    try {
      const t = await api.importSession(c.provider, c.session_id);
      router.push(`/thread/${encodeURIComponent(t.id)}`);
    } catch (e) {
      setErr(describeError(e));
    } finally {
      setBusy(null);
    }
  };

  const byRepo = new Map<string, ImportCandidate[]>();
  for (const c of list ?? []) byRepo.set(c.repo_path, [...(byRepo.get(c.repo_path) ?? []), c]);

  return (
    <View style={{ flex: 1, backgroundColor: colors.ground }}>
      <BackHeader title="Import sessions" subtitle="From your terminal" surface={colors.ground} />
      <ScrollView contentContainerStyle={{ padding: 16, gap: 14 }}>
        {err ? <Text style={[type.small, { color: colors.failed }]}>{err}</Text> : null}
        {list && !list.length ? (
          <EmptyNote>No sessions to import. On the cluster run: brain import --root ~/your-folder --claude-dir ~/.claude</EmptyNote>
        ) : null}
        {[...byRepo.entries()].map(([repo, items]) => (
          <View key={repo} style={{ gap: 6 }}>
            <SectionLabel>{repo.split('/').pop() ?? repo}</SectionLabel>
            <ListCard>
              {items.map((c) => (
                <View key={c.session_id} style={{ flexDirection: 'row', alignItems: 'center', gap: 10, paddingVertical: 10 }}>
                  <View style={{ flex: 1, gap: 2 }}>
                    <Text style={type.body} numberOfLines={2}>{c.title}</Text>
                    <Mono>{`${c.provider} · ${ago(c.last_ts)}${c.live ? ' · open in a terminal' : ''}`}</Mono>
                  </View>
                  <Button
                    compact
                    label={c.thread_id ? 'Open' : 'Import'}
                    kind={c.thread_id ? 'secondary' : 'primary'}
                    busy={busy === c.session_id}
                    onPress={() => open(c)}
                    accessibilityLabel={`${c.thread_id ? 'Open' : 'Import'} ${c.title}`}
                  />
                </View>
              ))}
            </ListCard>
          </View>
        ))}
      </ScrollView>
    </View>
  );
}
