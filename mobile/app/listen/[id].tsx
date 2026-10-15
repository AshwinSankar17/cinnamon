/**
 * Blind listening test. Per sentence the systems appear as A, B, C… in a shuffled order (stable per sentence), with
 * labels and metrics hidden until you submit. Rate each sample 1–5 and pick the best (or a tie); switching A↔B keeps
 * the playback position. Reference and voice prompt (if the agent provided them) are labelled and never blinded.
 */
import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react';
import { ActivityIndicator, Alert, FlatList, Pressable, Text, TextInput, View } from 'react-native';
import { router, useLocalSearchParams } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { Icon } from '@/components/Icon';
import { BackHeader, Button, Card, EmptyNote, Mono, SectionLabel } from '@/components/ui';
import { api, describeError } from '@/src/api';
import { AUDIO_SUPPORTED, player } from '@/src/audio';
import { getMeta, setMeta } from '@/src/store';
import { colors, fonts, type } from '@/src/theme';
import type { ItemRating, ListenItem, ListeningTest, ListenResult } from '@/src/types';

const LETTERS = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ';

/** Deterministic per-item shuffle so the blind order survives reloads. */
function shuffled<T>(xs: T[], seed: string): T[] {
  let h = 2166136261;
  for (let i = 0; i < seed.length; i++) h = Math.imul(h ^ seed.charCodeAt(i), 16777619);
  const rand = () => {
    h = (h + 0x6d2b79f5) | 0;
    let t = Math.imul(h ^ (h >>> 15), 1 | h);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
  const a = [...xs];
  for (let i = a.length - 1; i > 0; i--) {
    const j = Math.floor(rand() * (i + 1));
    [a[i], a[j]] = [a[j], a[i]];
  }
  return a;
}

function usePlaying(): string | null {
  return useSyncExternalStore(player.subscribe, () => player.current, () => null);
}

export default function ListenScreen() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const insets = useSafeAreaInsets();
  const [test, setTest] = useState<ListeningTest | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [ratings, setRatings] = useState<Record<string, ItemRating>>({});
  const [result, setResult] = useState<ListenResult | null>(null);
  const [busy, setBusy] = useState(false);
  const urls = useRef(new Map<string, Promise<string>>());
  const playing = usePlaying();
  const draftKey = `listen:${id}`;

  useEffect(() => {
    (async () => {
      try {
        const t = await api.listening(id);
        setTest(t);
        if (t.state === 'rated') {
          setRatings(t.ratings ?? {});
          setResult(t.summary);
        } else {
          const draft = await getMeta(draftKey);
          if (draft) setRatings(JSON.parse(draft));
        }
      } catch (e) {
        setErr(describeError(e));
      }
    })();
    return () => player.pause();
  }, [id, draftKey]);

  const update = useCallback(
    (itemId: string, fn: (r: ItemRating) => ItemRating) =>
      setRatings((cur) => {
        const next = { ...cur, [itemId]: fn(cur[itemId] ?? {}) };
        setMeta(draftKey, JSON.stringify(next)).catch(() => undefined);
        return next;
      }),
    [draftKey],
  );

  const play = useCallback(
    async (path: string, ab: boolean) => {
      try {
        let u = urls.current.get(path);
        if (!u) {
          u = api.listeningAudio(id, path);
          urls.current.set(path, u);
          u.catch(() => urls.current.delete(path));
        }
        await player.play(await u, ab);
      } catch (e) {
        Alert.alert('Could not play', describeError(e));
      }
    },
    [id],
  );

  const revealed = !!result;
  const rated = useMemo(
    () => (test ? test.items.filter((it) => { const r = ratings[it.id]; return r && (r.best || Object.keys(r.scores ?? {}).length); }).length : 0),
    [test, ratings],
  );

  const submit = async () => {
    if (!test) return;
    setBusy(true);
    try {
      const r = await api.rateListening(id, ratings);
      setResult(r);
      player.pause();
    } catch (e) {
      Alert.alert('Ratings not sent', `${describeError(e)}\n\nYour ratings are kept on this phone; try again.`);
    } finally {
      setBusy(false);
    }
  };

  if (!test) {
    return (
      <View style={{ flex: 1, backgroundColor: colors.ground }}>
        <BackHeader title="Listening test" surface={colors.ground} />
        {err ? <EmptyNote>{err}</EmptyNote> : <ActivityIndicator style={{ marginTop: 40 }} color={colors.accent} />}
      </View>
    );
  }

  const label = (sid: string) => test.systems.find((s) => s.id === sid)?.label ?? sid;
  return (
    <View style={{ flex: 1, backgroundColor: colors.ground }}>
      <BackHeader
        title={test.title}
        subtitle={revealed ? 'results' : `blind · ${rated}/${test.items.length} rated`}
        surface={colors.ground}
        right={<Button label="Thread" compact onPress={() => router.push(`/thread/${encodeURIComponent(test.thread_id)}`)} />}
      />
      {!AUDIO_SUPPORTED ? <EmptyNote>Audio plays in the web app for now. Open Brain&apos;s link in Chrome.</EmptyNote> : null}
      <FlatList
        data={test.items}
        keyExtractor={(it) => it.id}
        contentContainerStyle={{ padding: 16, gap: 12, paddingBottom: 120 }}
        ListHeaderComponent={
          <View style={{ gap: 10, paddingBottom: 4 }}>
            {test.description ? <Text style={[type.body, { color: colors.text2 }]}>{test.description}</Text> : null}
            {revealed && result ? <ResultCard test={test} result={result} /> : (
              <Text style={[type.small, { color: colors.text2 }]}>
                Systems are shown as letters in a different order for each sentence. Tap a letter to play; tapping another while one
                plays switches at the same moment. Score each 1–5 and pick the best.
              </Text>
            )}
          </View>
        }
        renderItem={({ item, index }) => (
          <ItemCard
            item={item}
            index={index}
            total={test.items.length}
            order={shuffled(Object.keys(item.samples), `${test.id}:${item.id}`)}
            rating={ratings[item.id] ?? {}}
            revealed={revealed}
            label={label}
            playing={playing}
            onPlay={play}
            onRate={(fn) => update(item.id, fn)}
            urlOf={(p) => urls.current.get(p)}
          />
        )}
      />
      {!revealed ? (
        <View style={{ position: 'absolute', left: 0, right: 0, bottom: 0, padding: 12, paddingBottom: Math.max(insets.bottom, 12), backgroundColor: colors.ground, borderTopWidth: 1, borderTopColor: colors.hairline }}>
          <Button
            label={rated === test.items.length ? 'Submit ratings' : `Submit ${rated} of ${test.items.length} rated`}
            kind="primary"
            disabled={!rated}
            busy={busy}
            onPress={submit}
          />
        </View>
      ) : null}
    </View>
  );
}

function ItemCard({
  item, index, total, order, rating, revealed, label, playing, onPlay, onRate, urlOf,
}: {
  item: ListenItem;
  index: number;
  total: number;
  order: string[];
  rating: ItemRating;
  revealed: boolean;
  label: (sid: string) => string;
  playing: string | null;
  onPlay: (path: string, ab: boolean) => void;
  onRate: (fn: (r: ItemRating) => ItemRating) => void;
  urlOf: (path: string) => Promise<string> | undefined;
}) {
  const [noteOpen, setNoteOpen] = useState(!!rating.note);
  const [playingPath, setPlayingPath] = useState<string | null>(null);
  useEffect(() => {
    // map the player's blob URL back to the file it came from
    let live = true;
    (async () => {
      for (const p of [...order.map((s) => item.samples[s]), item.reference, item.prompt]) {
        if (!p) continue;
        const u = urlOf(p);
        if (u && playing && (await u) === playing) {
          if (live) setPlayingPath(p);
          return;
        }
      }
      if (live) setPlayingPath(null);
    })();
    return () => {
      live = false;
    };
  }, [playing, order, item, urlOf]);

  const extra = (path: string | null, name: string) =>
    path ? <PlayChip key={name} text={name} active={playingPath === path} onPress={() => onPlay(path, false)} muted /> : null;
  return (
    <Card>
      <Mono>
        {index + 1} of {total} · {item.id}
      </Mono>
      {item.text ? <Text style={type.body}>{item.text}</Text> : null}
      {item.reference || item.prompt ? (
        <View style={{ flexDirection: 'row', gap: 8, flexWrap: 'wrap' }}>
          {extra(item.reference, 'Reference')}
          {extra(item.prompt, 'Voice prompt')}
        </View>
      ) : null}
      <View style={{ gap: 8 }}>
        {order.map((sid, i) => {
          const path = item.samples[sid];
          const score = rating.scores?.[sid];
          return (
            <View key={sid} style={{ flexDirection: 'row', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
              <PlayChip text={revealed ? label(sid) : LETTERS[i]} active={playingPath === path} onPress={() => onPlay(path, true)} />
              <View style={{ flexDirection: 'row', gap: 4 }}>
                {[1, 2, 3, 4, 5].map((n) => (
                  <Pressable
                    key={n}
                    accessibilityRole="button"
                    accessibilityLabel={`Score ${LETTERS[i]} ${n}`}
                    accessibilityState={{ selected: score === n }}
                    disabled={revealed}
                    onPress={() => onRate((r) => ({ ...r, scores: { ...(r.scores ?? {}), [sid]: n } }))}
                    style={{ width: 32, height: 32, borderRadius: 16, alignItems: 'center', justifyContent: 'center', borderWidth: 1,
                      borderColor: score === n ? colors.accent : colors.border, backgroundColor: score === n ? colors.accentTint : colors.surface }}
                  >
                    <Text style={{ fontFamily: score === n ? fonts.sansBold : fonts.sansMedium, fontSize: 13, color: score === n ? colors.accent : colors.text2 }}>{n}</Text>
                  </Pressable>
                ))}
              </View>
              {revealed && item.metrics?.[sid] ? (
                <Mono style={{ color: colors.text3 }}>{Object.entries(item.metrics[sid]).map(([k, v]) => `${k} ${v}`).join(' · ')}</Mono>
              ) : null}
            </View>
          );
        })}
      </View>
      <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
        <SectionLabel>Best</SectionLabel>
        {[...order, 'tie'].map((sid, i) => {
          const on = rating.best === sid;
          const text = sid === 'tie' ? 'Tie' : revealed ? label(sid) : LETTERS[i];
          return (
            <Pressable
              key={sid}
              accessibilityRole="button"
              accessibilityLabel={`Best ${text}`}
              accessibilityState={{ selected: on }}
              disabled={revealed}
              onPress={() => onRate((r) => ({ ...r, best: on ? null : sid }))}
              style={{ height: 32, paddingHorizontal: 12, borderRadius: 999, justifyContent: 'center', borderWidth: 1,
                borderColor: on ? colors.accent : colors.border, backgroundColor: on ? colors.accentTint : colors.surface }}
            >
              <Text style={{ fontFamily: on ? fonts.sansBold : fonts.sansMedium, fontSize: 13, color: on ? colors.accent : colors.text2 }}>{text}</Text>
            </Pressable>
          );
        })}
        {!revealed && !noteOpen ? (
          <Pressable onPress={() => setNoteOpen(true)} hitSlop={6}>
            <Text style={{ fontFamily: fonts.sansMedium, fontSize: 13, color: colors.accent }}>+ note</Text>
          </Pressable>
        ) : null}
      </View>
      {noteOpen || (revealed && rating.note) ? (
        <TextInput
          accessibilityLabel="Note"
          editable={!revealed}
          value={rating.note ?? ''}
          onChangeText={(t) => onRate((r) => ({ ...r, note: t }))}
          placeholder="What do you hear? (e.g. B clips at the end)"
          placeholderTextColor={colors.text3}
          style={{ borderWidth: 1, borderColor: colors.border, borderRadius: 10, padding: 8, fontFamily: fonts.sans, fontSize: 14, color: colors.text }}
        />
      ) : null}
    </Card>
  );
}

function PlayChip({ text, active, onPress, muted }: { text: string; active: boolean; onPress: () => void; muted?: boolean }) {
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={`${active ? 'Pause' : 'Play'} ${text}`}
      onPress={onPress}
      style={({ pressed }) => ({ minWidth: 64, height: 36, paddingHorizontal: 12, borderRadius: 18, flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 6,
        backgroundColor: active ? colors.black : muted ? colors.surface2 : colors.surface, borderWidth: 1, borderColor: active ? colors.black : colors.border, opacity: pressed ? 0.8 : 1 })}
    >
      <Icon name={active ? 'pause' : 'play'} size={14} color={active ? colors.onBlack : colors.text} />
      <Text style={{ fontFamily: fonts.sansSemi, fontSize: 14, color: active ? colors.onBlack : colors.text }}>{text}</Text>
    </Pressable>
  );
}

function ResultCard({ test, result }: { test: ListeningTest; result: ListenResult }) {
  return (
    <Card tone="needs">
      <Text style={type.cardTitle}>Results · {result.rated_items}/{result.total_items} rated</Text>
      {result.ranking.map((sid, i) => {
        const s = result.systems[sid];
        const agg = test.metrics?.[sid];
        return (
          <View key={sid} style={{ gap: 2 }}>
            <Text style={[type.body, { fontFamily: i === 0 ? fonts.sansBold : fonts.sans }]}>
              {i + 1}. {s.label}
            </Text>
            <Mono>
              {[s.mos !== null ? `MOS ${s.mos.toFixed(2)}${s.mos_ci95 !== null ? ` ± ${s.mos_ci95.toFixed(2)}` : ''}` : null, `best in ${s.best}`,
                ...(agg ? Object.entries(agg).map(([k, v]) => `${k} ${v}`) : [])].filter(Boolean).join(' · ')}
            </Mono>
          </View>
        );
      })}
      {result.ties ? <Mono>{result.ties} ties</Mono> : null}
      <Text style={[type.small, { color: colors.text2 }]}>Sent to the agent; it continues with these results.</Text>
    </Card>
  );
}
