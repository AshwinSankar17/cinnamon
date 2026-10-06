/**
 * Thread file browser: the repo worktree, the run's own vault folder, and the repo's shared vault. Read-only.
 * Audio plays inline, images and text open in place (logs, configs, listen_results.json, plots).
 */
import { useCallback, useEffect, useState, useSyncExternalStore } from 'react';
import { ActivityIndicator, Alert, FlatList, Image, Pressable, ScrollView, Text, View } from 'react-native';
import { useLocalSearchParams } from 'expo-router';
import { Icon } from '@/components/Icon';
import { BackHeader, Chip, EmptyNote, Mono } from '@/components/ui';
import { api, describeError } from '@/src/api';
import { player } from '@/src/audio';
import { colors, fonts } from '@/src/theme';
import type { FileEntry, FileListing } from '@/src/types';

const AUDIO = /\.(wav|flac|mp3|ogg|opus|m4a|aac|webm)$/i;
const IMAGE = /\.(png|jpe?g|gif|webp|svg)$/i;
const ROOT_LABEL: Record<string, string> = { worktree: 'Repo', run: 'Run vault', vault: 'Shared vault' };

function size(n: number): string {
  return n < 1024 ? `${n} B` : n < 1048576 ? `${(n / 1024).toFixed(0)} KB` : `${(n / 1048576).toFixed(1)} MB`;
}

type Preview = { name: string; kind: 'image' | 'text'; url?: string; text?: string };

export default function FilesScreen() {
  const { id, root: root0 } = useLocalSearchParams<{ id: string; root?: string }>();
  const [root, setRoot] = useState(root0 ?? 'worktree');
  const [path, setPath] = useState('');
  const [list, setList] = useState<FileListing | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [opening, setOpening] = useState<string | null>(null);
  const playing = useSyncExternalStore(player.subscribe, () => player.current, () => null);
  const [audioUrl, setAudioUrl] = useState<Record<string, string>>({});

  const load = useCallback(async () => {
    setErr(null);
    try {
      setList(await api.files(id, root, path));
    } catch (e) {
      setList(null);
      setErr(describeError(e));
    }
  }, [id, root, path]);
  useEffect(() => {
    load();
  }, [load]);
  useEffect(() => () => player.pause(), []);

  const open = async (e: FileEntry) => {
    const rel = path ? `${path}/${e.name}` : e.name;
    if (e.dir) {
      setPath(rel);
      setPreview(null);
      return;
    }
    setOpening(rel);
    try {
      if (AUDIO.test(e.name)) {
        const u = audioUrl[rel] ?? URL.createObjectURL(await api.fileBlob(id, root, rel));
        setAudioUrl((m) => ({ ...m, [rel]: u }));
        await player.play(u);
      } else if (IMAGE.test(e.name)) {
        setPreview({ name: e.name, kind: 'image', url: URL.createObjectURL(await api.fileBlob(id, root, rel)) });
      } else if (e.size <= 400_000) {
        setPreview({ name: e.name, kind: 'text', text: await (await api.fileBlob(id, root, rel)).text() });
      } else {
        Alert.alert('Too large to preview', `${e.name} is ${size(e.size)}.`);
      }
    } catch (x) {
      Alert.alert('Could not open', describeError(x));
    } finally {
      setOpening(null);
    }
  };

  const crumbs = path ? path.split('/') : [];
  return (
    <View style={{ flex: 1, backgroundColor: colors.ground }}>
      <BackHeader title="Files" subtitle={[ROOT_LABEL[root], path].filter(Boolean).join(' / ')} surface={colors.ground} />
      <View style={{ paddingHorizontal: 16, gap: 8, paddingBottom: 8 }}>
        <View style={{ flexDirection: 'row', gap: 8, flexWrap: 'wrap' }}>
          {(list?.roots ?? ['worktree']).map((r) => (
            <Chip key={r} label={ROOT_LABEL[r] ?? r} selected={r === root} onPress={() => { setRoot(r); setPath(''); setPreview(null); }} />
          ))}
        </View>
        {crumbs.length ? (
          <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={{ gap: 4, alignItems: 'center' }}>
            <Pressable onPress={() => { setPath(''); setPreview(null); }}><Mono style={{ color: colors.accent }}>{ROOT_LABEL[root]}</Mono></Pressable>
            {crumbs.map((c, i) => (
              <Pressable key={i} onPress={() => { setPath(crumbs.slice(0, i + 1).join('/')); setPreview(null); }}>
                <Mono style={{ color: i === crumbs.length - 1 ? colors.text : colors.accent }}> / {c}</Mono>
              </Pressable>
            ))}
          </ScrollView>
        ) : null}
      </View>
      {preview ? (
        <View style={{ flex: 1, paddingHorizontal: 16, gap: 8 }}>
          <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
            <Text style={{ flex: 1, fontFamily: fonts.mono, fontSize: 13, color: colors.text }} numberOfLines={1}>{preview.name}</Text>
            <Chip label="Close" onPress={() => setPreview(null)} />
          </View>
          {preview.kind === 'image' ? (
            <Image source={{ uri: preview.url }} resizeMode="contain" style={{ flex: 1, width: '100%' }} accessibilityLabel={preview.name} />
          ) : (
            <ScrollView style={{ flex: 1, backgroundColor: colors.surface, borderRadius: 10, borderWidth: 1, borderColor: colors.hairline }}>
              <ScrollView horizontal>
                <Text selectable style={{ fontFamily: fonts.mono, fontSize: 12, lineHeight: 18, color: colors.text, padding: 12 }}>{preview.text}</Text>
              </ScrollView>
            </ScrollView>
          )}
        </View>
      ) : (
        <FlatList
          data={list?.entries ?? []}
          keyExtractor={(e) => e.name}
          contentContainerStyle={{ paddingHorizontal: 16, paddingBottom: 24 }}
          ListEmptyComponent={err ? <EmptyNote>{err}</EmptyNote> : list ? <EmptyNote>Empty folder.</EmptyNote> : <ActivityIndicator color={colors.accent} />}
          renderItem={({ item: e }) => {
            const rel = path ? `${path}/${e.name}` : e.name;
            const isPlaying = !!playing && playing === audioUrl[rel];
            return (
              <Pressable
                accessibilityRole="button"
                accessibilityLabel={e.dir ? `Open folder ${e.name}` : `Open ${e.name}`}
                onPress={() => open(e)}
                style={({ pressed }) => ({ minHeight: 48, flexDirection: 'row', alignItems: 'center', gap: 10, borderBottomWidth: 1, borderBottomColor: colors.hairline, opacity: pressed ? 0.7 : 1 })}
              >
                <Icon
                  name={e.dir ? 'repos' : AUDIO.test(e.name) ? (isPlaying ? 'pause' : 'play') : 'info'}
                  size={16}
                  color={e.dir ? colors.text2 : AUDIO.test(e.name) ? colors.accent : colors.text3}
                />
                <Text style={{ flex: 1, fontFamily: e.dir ? fonts.sansSemi : fonts.sans, fontSize: 14, color: colors.text }} numberOfLines={1}>
                  {e.name}
                  {e.dir ? '/' : ''}
                </Text>
                {opening === rel ? <ActivityIndicator color={colors.accent} /> : <Mono style={{ color: colors.text3 }}>{e.dir ? '' : size(e.size)}</Mono>}
              </Pressable>
            );
          }}
        />
      )}
    </View>
  );
}
