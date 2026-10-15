/**
 * The standup: Cinnamon's home (spec §7.1). The focus thread is the roll, living in the chosen world; the world shows
 * the other threads its own way, the thing waiting for the owner sits at thumb height, the companion keeps it company,
 * and the ☰ menu and the composer share the bottom tray. Everything on screen comes from Brain (src/standup.ts).
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { Animated, Easing, KeyboardAvoidingView, Platform, Pressable, Text, TextInput, Vibration, View, useWindowDimensions } from 'react-native';
import { Redirect, router, useFocusEffect } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import Svg, { Path } from 'react-native-svg';
import { Ambient } from '@/components/cinnamon/Ambient';
import { ConnectionBanner } from '@/components/ConnectionBanner';
import { BACKDROPS, SceneDefs } from '@/components/cinnamon/backdrops';
import { at, frameFor, HERO } from '@/components/cinnamon/frame';
import { Menu } from '@/components/cinnamon/Menu';
import { Pet, SAY_AT } from '@/components/cinnamon/Pets';
import { Presenters } from '@/components/cinnamon/Presenters';
import { Roll, ROLL_BASE, type Mood, type RollHandle } from '@/components/cinnamon/Roll';
import { Unrolled } from '@/components/cinnamon/Unrolled';
import { WaitingCard } from '@/components/cinnamon/WaitingCard';
import { api, describeError } from '@/src/api';
import { statusOf, useConnection } from '@/src/connection';
import { headline, loadStandup, petLine, subline, type Standup } from '@/src/standup';
import { getMeta } from '@/src/store';
import { useLive, useWorld, useWorldFont } from '@/src/world';
import { WORLDS, type WorldId } from '@/src/worlds';
import { brainSocket } from '@/src/ws';

export default function StandupScreen() {
  const conn = useConnection();
  const insets = useSafeAreaInsets();
  const win = useWindowDimensions();
  const [box, setBox] = useState<{ w: number; h: number } | null>(null); // the app column, not the window (desktop caps it)
  const W = box?.w ?? win.width;
  const H = box?.h ?? win.height;
  const { world, id, tokens: t, dark, pet, showcase, setWorld } = useWorld();
  const display = useWorldFont('display');
  const ui = useWorldFont('ui');
  const uiSemi = useWorldFont('uiSemi');
  const mono = useWorldFont('mono');
  const hand = useWorldFont('hand');
  const [s, setS] = useState<Standup | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [menu, setMenu] = useState(false);
  const [hist, setHist] = useState(false);
  const [say, setSay] = useState(false);
  const [draft, setDraft] = useState('');
  const [sending, setSending] = useState(false);
  const roll = useRef<RollHandle>(null);
  const wipe = useRef(new Animated.Value(0)).current;
  const [wipeColor, setWipeColor] = useState<string | null>(null);
  const live = useLive(!menu && !hist);

  const reload = useCallback(async () => {
    try {
      const left = await getMeta('left_at').catch(() => null);
      setS(await loadStandup(left ? Number(left) : null));
      setErr(null);
    } catch (e) {
      setErr(describeError(e));
    }
  }, []);
  // The home subscribes to no thread, so socket events rarely reach it: poll while it is on screen.
  useFocusEffect(useCallback(() => { reload(); const iv = setInterval(reload, 20_000); return () => clearInterval(iv); }, [reload]));
  useEffect(() => {
    let last = 0;
    let timer: ReturnType<typeof setTimeout> | null = null;
    const off = brainSocket.onAnyEvent(() => {
      const wait = Math.max(0, 2000 - (Date.now() - last));
      if (timer) return;
      timer = setTimeout(() => { timer = null; last = Date.now(); reload(); }, wait);
    });
    return () => { off(); if (timer) clearTimeout(timer); };
  }, [reload]);

  const pickWorld = useCallback((next: WorldId) => {
    if (next === id || wipeColor) return; // one wipe at a time
    const tk = dark ? WORLDS[next].night : WORLDS[next].day;
    setWipeColor(tk.sSky1 ?? tk.sWall ?? tk.sPlank ?? tk.sW1 ?? tk.bg2);
    try { Vibration.vibrate(15); } catch { /* no vibration on this device */ }
    wipe.setValue(0);
    Animated.timing(wipe, { toValue: 1, duration: 600, easing: Easing.bezier(0.65, 0, 0.35, 1), useNativeDriver: true }).start(() => {
      setWorld(next);
      setMenu(false);
      setTimeout(() => roll.current?.land(), 60);
      Animated.timing(wipe, { toValue: 2, duration: 420, useNativeDriver: true }).start(() => setWipeColor(null));
    });
  }, [id, dark, wipe, wipeColor, setWorld]);

  const status = statusOf(conn);
  useEffect(() => { if (status === 'live') reload(); }, [status, reload]);

  if (!conn.paired) return <Redirect href="/pair" />;

  const f = frameFor(W, H);
  const short = Math.min(1, H / 844); // a short browser window shrinks the scene; the headline follows so they never meet
  const asleep = status !== 'live' && status !== 'connecting';
  const mood: Mood = asleep || s?.stale ? 'asleep' : s?.waiting ? 'needs' : s?.runningJobs.length || s?.focus?.state === 'running' ? 'working' : 'proud';
  const [hx, hy, hs] = HERO[id];
  const rollW = 224 * hs * f.s;
  const rollH = (rollW * 236) / 224;
  const rollPos = { left: f.ox + hx * f.s - rollW * ROLL_BASE.x, top: f.oy + hy * f.s - rollH * ROLL_BASE.y };
  const Backdrop = BACKDROPS[id];
  const liveText = status === 'live' && !s?.stale ? (conn.paused ? 'paused' : 'live') : s ? `as of ${Math.max(1, Math.round((Date.now() - s.asOf) / 60000))} min ago` : status;
  const sayPos = at(f, SAY_AT[id][0], SAY_AT[id][1]);
  const composerTarget = s?.focus ? (showcase ? 'the thread' : s.focus.title) : null;

  const send = async () => {
    const text = draft.trim();
    if (!text) return;
    if (!s?.focus) return router.push({ pathname: '/new', params: { message: text } });
    setSending(true);
    try {
      await api.sendMessage(s.focus.id, text);
      setDraft('');
      reload();
    } catch (e) {
      setErr(describeError(e));
    } finally {
      setSending(false);
    }
  };
  const openWaiting = () => {
    const w = s?.waiting;
    if (!w) return;
    router.push(w.kind === 'approval' ? `/approval/${encodeURIComponent(w.id)}` : w.kind === 'listen' ? `/listen/${encodeURIComponent(w.id)}` : `/thread/${encodeURIComponent(w.id)}`);
  };

  return (
    <View style={{ flex: 1, backgroundColor: t.bg2, overflow: 'hidden' }}
      onLayout={(e) => { const { width, height } = e.nativeEvent.layout; setBox((b) => (b && b.w === width && b.h === height ? b : { w: width, h: height })); }}>
      {(f.ox > 0 ? [[0, f.ox, '11 0 0.5 844'], [W - f.ox, f.ox, `${id === 'forest' ? 11 : 379} 0 0.5 844`]] as const : []).map(([left, w, vb]) => (
        // the scene's outermost column stretched across a wide screen's margins: walls, sky and counters run on
        <Svg key={vb} width={w + 1} height={H} viewBox={vb} preserveAspectRatio="none" style={{ position: 'absolute', left: left - 0.5 }}>
          <SceneDefs t={t} />
          <Backdrop t={t} dark={dark} />
        </Svg>
      ))}
      {f.ox > 0 ? [0, W - f.ox].map((left) => ( // a light veil so the margins recede and the scene reads as the stage
        <View key={left} pointerEvents="none" style={{ position: 'absolute', left, top: 0, width: f.ox, height: H, backgroundColor: t.bg2, opacity: 0.35 }} />
      )) : null}
      <Svg width={390 * f.s} height={844 * f.s} viewBox="0 0 390 844" style={{ position: 'absolute', left: f.ox, top: f.oy }}>
        <SceneDefs t={t} />
        <Backdrop t={t} dark={dark} />
      </Svg>
      <Ambient world={id} dark={dark} f={f} live={live} />
      {s ? (
        <Presenters world={id} t={t} f={f} threads={s.threads.filter((x) => x.id !== s.focus?.id || s.threads.length === 1)} live={live} showcase={showcase}
          fonts={{ display, mono, hand, ui }} onThread={(tid) => router.push(`/thread/${encodeURIComponent(tid)}`)} />
      ) : null}
      {pet && s ? (
        <Pet world={id} f={f} live={live} running={s.runningJobs.length} needs={!!s.waiting} label={`${world.pet.name} the ${world.pet.kind}. Tap for a status line.`}
          onPress={() => { setSay((v) => !v); setTimeout(() => setSay(false), 5000); }} />
      ) : null}
      <Pressable accessibilityRole="button" accessibilityLabel={`${showcase ? 'The roll' : s?.focus?.title ?? 'The roll'}: its swirl is the history. Tap to unroll it.`}
        onPress={() => setHist(true)} style={{ position: 'absolute', ...rollPos, width: rollW, height: rollH }}>
        <Roll ref={roll} mood={mood} activity={s?.activity ?? null} accessory={world.accessory} live={live} width={rollW}
          helmetGlass={t.sGlass} helmetRim={t.sRim} helmetCollar={t.sSat} zColor={t.ink2} />
      </Pressable>
      {say && s ? (
        <View accessibilityRole="text" style={{ position: 'absolute', left: Math.min(sayPos.left, W - 250), top: sayPos.top, maxWidth: 240, backgroundColor: t.raise,
                                                 borderRadius: 18, paddingHorizontal: 13, paddingVertical: 10, shadowColor: '#000', shadowOpacity: 0.18, shadowRadius: 12, shadowOffset: { width: 0, height: 8 } }}>
          <Text style={{ fontFamily: ui, fontSize: 13, lineHeight: 18, color: t.ink }}>
            <Text style={{ fontFamily: uiSemi }}>{world.pet.name}</Text>{` · ${petLine(id, s, showcase)}`}
          </Text>
        </View>
      ) : null}

      <View pointerEvents="box-none" style={{ position: 'absolute', left: 0, right: 0, top: 0, paddingTop: insets.top + 10, paddingHorizontal: 18 }}>
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 9, height: 44 }}>
          <Svg width={24} height={24} viewBox="0 0 24 24"><Path d="M12 12a2 2 0 1 1 2 2a4 4 0 1 1-4-4a6 6 0 1 1 6 6a8 8 0 1 1-8-8" fill="none" stroke={t.accent} strokeWidth={1.8} strokeLinecap="round" /></Svg>
          <Text style={{ flex: 1, fontFamily: display, fontSize: 23 * world.displayScale, color: t.ink }}>Cinnamon</Text>
          <View accessibilityLabel={`Brain ${liveText}`} style={{ flexDirection: 'row', alignItems: 'center', gap: 7, minHeight: 40 }}>
            <View style={{ width: 7, height: 7, borderRadius: 4, backgroundColor: status === 'live' && !s?.stale ? t.finding : t.ink3 }} />
            <Text style={{ fontFamily: mono, fontSize: 11, color: t.ink2 }}>{liveText}</Text>
          </View>
        </View>
        <Text accessibilityRole="header" style={{ marginTop: 10, fontFamily: display, fontSize: 29 * world.displayScale * short, lineHeight: 32 * world.displayScale * short, color: t.ink }}>
          {s ? headline(s, showcase) : err ? 'Brain is not reachable.' : ' '}
        </Text>
        <Text style={{ marginTop: 8, maxWidth: 330, fontFamily: ui, fontSize: 13.5, lineHeight: 19, color: t.ink2 }}>
          {s ? subline(s, showcase) : err ?? ''}
        </Text>
        {s && err ? <Text accessibilityRole="alert" style={{ marginTop: 8, fontFamily: ui, fontSize: 13, color: t.needs }}>{err}</Text> : null}
        {status === 'revoked' || status === 'identity_mismatch' ? <View style={{ marginTop: 12, marginHorizontal: -16 }}><ConnectionBanner /></View> : null}
      </View>

      <KeyboardAvoidingView behavior={Platform.OS === 'ios' ? 'padding' : undefined} style={{ position: 'absolute', left: 12, right: 12, bottom: Math.max(insets.bottom, 12) }}>
        {s?.waiting ? (
          <View style={{ marginBottom: 10 }}>
            <WaitingCard world={world} t={t} waiting={s.waiting} more={Math.max(0, s.waitingCount - 1)} live={live} fonts={{ display, mono, ui }}
              onPress={s.waitingCount > 1 ? () => router.push('/inbox') : openWaiting} />
          </View>
        ) : null}
        <View style={{ flexDirection: 'row', gap: 8, alignItems: 'center', height: 58 }}>
          <Pressable accessibilityRole="button" accessibilityLabel={menu ? 'Close menu' : 'Open menu'} accessibilityState={{ expanded: menu }} onPress={() => setMenu((m) => !m)}
            style={{ width: 58, height: 58, borderRadius: 29, backgroundColor: t.raise, borderWidth: 1, borderColor: t.line, alignItems: 'center', justifyContent: 'center' }}>
            <Svg width={22} height={22} viewBox="0 0 24 24"><Path d={menu ? 'M6 6l12 12M18 6L6 18' : 'M4 8h16M4 16h16'} stroke={t.ink} strokeWidth={2} strokeLinecap="round" /></Svg>
          </Pressable>
          <View style={{ flex: 1, height: 58, borderRadius: 29, backgroundColor: t.raise, borderWidth: 1, borderColor: t.line, flexDirection: 'row', alignItems: 'center', paddingLeft: 20, paddingRight: 8, gap: 10 }}>
            <TextInput value={draft} onChangeText={setDraft} onSubmitEditing={send} returnKeyType="send" editable={!sending}
              placeholder={composerTarget ? `Tell ${composerTarget}…` : 'Start something…'} placeholderTextColor={t.ink3}
              accessibilityLabel={composerTarget ? `Message ${composerTarget}` : 'Start something new'}
              style={{ flex: 1, fontFamily: ui, fontSize: 15, color: t.ink, paddingVertical: 8 }} />
            <Pressable accessibilityRole="button" accessibilityLabel="Send" onPress={send} disabled={sending}
              style={{ width: 42, height: 42, borderRadius: 21, backgroundColor: t.ink, alignItems: 'center', justifyContent: 'center', opacity: sending ? 0.6 : 1 }}>
              <Svg width={18} height={18} viewBox="0 0 24 24"><Path d="M12 19V5M6 11l6-6 6 6" fill="none" stroke={t.raise} strokeWidth={2.2} strokeLinecap="round" strokeLinejoin="round" /></Svg>
            </Pressable>
          </View>
        </View>
      </KeyboardAvoidingView>

      {wipeColor ? (
        <Animated.View pointerEvents="none"
          style={{ position: 'absolute', left: W / 2 - H * 1.2, top: H - 260 - H * 1.2, width: H * 2.4, height: H * 2.4, borderRadius: H * 1.2, backgroundColor: wipeColor,
                   opacity: wipe.interpolate({ inputRange: [0, 1, 2], outputRange: [1, 1, 0] }),
                   transform: [{ scale: wipe.interpolate({ inputRange: [0, 1, 2], outputRange: [0.001, 1, 1] }) }] }} />
      ) : null}
      <Unrolled open={hist} onClose={() => setHist(false)} activity={s?.activity ?? null} title={s?.focus?.title ?? 'This thread'} width={W} />
      <Menu open={menu} onClose={() => setMenu(false)} onPickWorld={pickWorld} />
    </View>
  );
}
