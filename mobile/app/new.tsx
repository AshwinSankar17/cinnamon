/**
 * Start something from the phone:
 *   Thread — work on an existing repo (read-only or its own worktree branch)
 *   Run    — a training run off an existing repo: worktree + its own vault folder for checkpoints/logs
 *   Idea   — a new project from scratch: Brain creates a repo + vault on the cluster and starts on the idea
 * Query params: kind=thread|run|idea, repo=<repo id> preselect.
 */
import { useEffect, useState } from 'react';
import { Alert, KeyboardAvoidingView, Platform, ScrollView, Text, TextInput, View } from 'react-native';
import { router, useLocalSearchParams } from 'expo-router';
import { BackHeader, Button, Chip, EmptyNote, Mono, SectionLabel } from '@/components/ui';
import { api, describeError } from '@/src/api';
import { upsertThreads } from '@/src/store';
import { colors, fonts, providerName, type } from '@/src/theme';
import type { Provider, ProviderInfo, Repo, ThreadMode } from '@/src/types';

type Kind = 'thread' | 'run' | 'idea';
const KINDS: { id: Kind; label: string; hint: string }[] = [
  { id: 'thread', label: 'Thread', hint: 'Work on an existing repo.' },
  { id: 'run', label: 'Training run', hint: 'A run off an existing repo, with its own vault folder for checkpoints, logs and outputs.' },
  { id: 'idea', label: 'New idea', hint: 'Brain creates a fresh repo and vault on the cluster and starts on your idea.' },
];

function Field({
  label,
  value,
  onChangeText,
  placeholder,
  multiline,
}: {
  label: string;
  value: string;
  onChangeText: (t: string) => void;
  placeholder?: string;
  multiline?: boolean;
}) {
  return (
    <View style={{ gap: 6 }}>
      <SectionLabel>{label}</SectionLabel>
      <TextInput
        accessibilityLabel={label}
        value={value}
        onChangeText={onChangeText}
        placeholder={placeholder}
        placeholderTextColor={colors.text3}
        multiline={multiline}
        textAlignVertical={multiline ? 'top' : 'center'}
        style={{
          borderWidth: 1,
          borderColor: colors.border,
          borderRadius: 12,
          paddingHorizontal: 12,
          paddingVertical: 10,
          minHeight: multiline ? 120 : 44,
          fontFamily: fonts.sans,
          fontSize: 15,
          color: colors.text,
          backgroundColor: colors.surface,
        }}
      />
    </View>
  );
}

export default function NewScreen() {
  const params = useLocalSearchParams<{ kind?: string; repo?: string }>();
  const [kind, setKind] = useState<Kind>((['thread', 'run', 'idea'] as Kind[]).includes(params.kind as Kind) ? (params.kind as Kind) : 'thread');
  const [repos, setRepos] = useState<Repo[] | null>(null);
  const [providers, setProviders] = useState<ProviderInfo[] | null>(null);
  const [repoId, setRepoId] = useState<string | null>(params.repo ?? null);
  const [provider, setProvider] = useState<Provider>('claude');
  const [mode, setMode] = useState<ThreadMode>('worktree');
  const [title, setTitle] = useState('');
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);
  const [loadErr, setLoadErr] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([api.repos(), api.providers()])
      .then(([r, p]) => {
        setRepos(r);
        const usable = p.filter((x) => x.name !== 'fake');
        setProviders(usable.length ? usable : p);
        if (!repoId && r.length) setRepoId(r[0].id);
        if (usable.length && !usable.some((x) => x.name === provider)) setProvider(usable[0].name);
      })
      .catch((e) => setLoadErr(describeError(e)));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const repo = repos?.find((r) => r.id === repoId) ?? null;
  const runBlocked = kind === 'run' && repo && !repo.vault_root;
  const ready =
    !busy &&
    !!providers &&
    (kind === 'idea' ? title.trim().length > 0 && message.trim().length > 0 : !!repo && !runBlocked && (kind !== 'run' || title.trim().length > 0));

  const create = async () => {
    setBusy(true);
    try {
      const t =
        kind === 'idea'
          ? (await api.createProject({ name: title.trim(), idea: message.trim(), provider })).thread
          : await api.createThread({
              repo_id: repo!.id,
              title: title.trim() || undefined,
              mode: kind === 'run' ? 'worktree' : mode,
              provider,
              run: kind === 'run',
              message: message.trim() || undefined,
            });
      await upsertThreads([t]);
      router.replace(`/thread/${encodeURIComponent(t.id)}`);
    } catch (e) {
      Alert.alert('Could not create it', describeError(e));
    } finally {
      setBusy(false);
    }
  };

  const hint = KINDS.find((k) => k.id === kind)!.hint;
  return (
    <View style={{ flex: 1, backgroundColor: colors.ground }}>
      <BackHeader title="Start something" backLabel="Cancel" surface={colors.ground} />
      <KeyboardAvoidingView style={{ flex: 1 }} behavior={Platform.OS === 'ios' ? 'padding' : undefined}>
        <ScrollView contentContainerStyle={{ padding: 16, gap: 18 }} keyboardShouldPersistTaps="handled">
          <View style={{ flexDirection: 'row', gap: 8, flexWrap: 'wrap' }}>
            {KINDS.map((k) => (
              <Chip key={k.id} label={k.label} selected={kind === k.id} onPress={() => setKind(k.id)} />
            ))}
          </View>
          <Text style={[type.body, { color: colors.text2 }]}>{hint}</Text>

          {loadErr ? <EmptyNote>{loadErr}</EmptyNote> : null}

          {kind !== 'idea' ? (
            <View style={{ gap: 6 }}>
              <SectionLabel>Repo</SectionLabel>
              {repos === null && !loadErr ? <Mono>Loading…</Mono> : null}
              {repos?.length === 0 ? <EmptyNote>No repos registered with Brain yet. Start a new idea instead.</EmptyNote> : null}
              <View style={{ flexDirection: 'row', gap: 8, flexWrap: 'wrap' }}>
                {repos?.map((r) => (
                  <Chip key={r.id} label={r.name ?? r.id} selected={r.id === repoId} onPress={() => setRepoId(r.id)} />
                ))}
              </View>
              {kind === 'run' && repo ? (
                <Mono>{repo.vault_root ? `run vault under ${repo.vault_root}` : 'This repo has no vault root; set one with `brain init --vault-root`.'}</Mono>
              ) : null}
            </View>
          ) : null}

          {kind === 'thread' ? (
            <View style={{ gap: 6 }}>
              <SectionLabel>Mode</SectionLabel>
              <View style={{ flexDirection: 'row', gap: 8 }}>
                <Chip label="Own branch (can edit)" selected={mode === 'worktree'} onPress={() => setMode('worktree')} />
                <Chip label="Read-only" selected={mode === 'readonly'} onPress={() => setMode('readonly')} />
              </View>
            </View>
          ) : null}

          <View style={{ gap: 6 }}>
            <SectionLabel>Model</SectionLabel>
            <View style={{ flexDirection: 'row', gap: 8, flexWrap: 'wrap' }}>
              {providers?.map((p) => (
                <Chip key={p.name} label={providerName[p.name] ?? p.name} selected={provider === p.name} onPress={() => setProvider(p.name)} />
              ))}
            </View>
            <Mono>{"You can switch models later; the thread's full history carries over."}</Mono>
          </View>

          <Field
            label={kind === 'idea' ? 'Project name' : kind === 'run' ? 'Run name' : 'Title (optional)'}
            value={title}
            onChangeText={setTitle}
            placeholder={kind === 'idea' ? 'tiny distilled TTS' : kind === 'run' ? 'prosody loss ablation, lr 3e-4' : 'what this thread is about'}
          />
          <Field
            label={kind === 'idea' ? 'The idea' : 'First message (optional)'}
            value={message}
            onChangeText={setMessage}
            multiline
            placeholder={
              kind === 'idea'
                ? 'What you want to build, constraints, where to start…'
                : kind === 'run'
                  ? 'What to train, which config, how to evaluate…'
                  : 'What you want done'
            }
          />

          <Button label={kind === 'idea' ? 'Create project' : kind === 'run' ? 'Start run' : 'Start thread'} kind="primary" onPress={create} disabled={!ready} busy={busy} />
        </ScrollView>
      </KeyboardAvoidingView>
    </View>
  );
}
