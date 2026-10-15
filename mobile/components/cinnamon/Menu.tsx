/**
 * The menu in the bottom tray (spec §7.3): one sheet whose content changes per step (World, Quota, Rules, This phone),
 * sliding in the direction of travel, plus the companion and quiet-hours switches, connection, navigation and sign-out.
 * Every number shown comes from Brain; a provider that reports nothing reads "not reported".
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { Animated, Easing, Platform, Pressable, ScrollView, Text, View, useWindowDimensions } from 'react-native';
import { router } from 'expo-router';
import Svg, { G, Path, Rect } from 'react-native-svg';
import { api, clusterLabel, describeError, forgetPairing, getActiveEndpoint } from '@/src/api';
import { statusOf, useConnection } from '@/src/connection';
import { wipeLocal } from '@/src/store';
import type { DeviceSelf, Quota, Rules } from '@/src/types';
import { useWorld, useWorldFont, type Mode } from '@/src/world';
import { WORLDS, WORLD_IDS, type WorldId } from '@/src/worlds';

type Step = 'root' | 'world' | 'quota' | 'rules' | 'phone';
const TITLE: Record<Step, string> = { root: 'Menu', world: 'World', quota: 'Quota', rules: 'Rules', phone: 'This phone' };
const EASE = Easing.bezier(0.32, 0.72, 0, 1);

const ago = (s: number) => {
  const m = Math.round((Date.now() / 1000 - s) / 60);
  return m < 1 ? 'just now' : m < 60 ? `${m} min ago` : `${Math.round(m / 60)} h ago`;
};
const when = (s: number | null) => (s ? new Date(s * 1000).toLocaleString(undefined, { weekday: 'short', hour: '2-digit', minute: '2-digit' }) : '');

export function Menu({ open, onClose, onPickWorld }: { open: boolean; onClose: () => void; onPickWorld: (id: WorldId) => void }) {
  const { world, tokens: t, mode, setMode, pet, setPet, showcase, setShowcase, dark } = useWorld();
  const display = useWorldFont('display');
  const ui = useWorldFont('ui');
  const uiSemi = useWorldFont('uiSemi');
  const mono = useWorldFont('mono');
  const conn = useConnection();
  const { height: H } = useWindowDimensions();
  const [step, setStep] = useState<Step>('root');
  const [dir, setDir] = useState(1);
  const [quota, setQuota] = useState<Quota | null>(null);
  const [rules, setRules] = useState<Rules | null>(null);
  const [me, setMe] = useState<DeviceSelf | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [armed, setArmed] = useState(false);
  const slide = useRef(new Animated.Value(1)).current;
  const sheet = useRef(new Animated.Value(0)).current;
  const stepIn = useRef(new Animated.Value(1)).current;

  useEffect(() => {
    Animated.timing(sheet, { toValue: open ? 1 : 0, duration: 500, easing: EASE, useNativeDriver: true }).start();
    if (open) {
      setStep('root');
      setArmed(false);
      api.quota().then(setQuota).catch(() => undefined);
      api.rules().then(setRules).catch(() => undefined);
      api.deviceSelf().then(setMe).catch(() => undefined);
    }
  }, [open, sheet]);

  const go = useCallback((s: Step, d = 1) => {
    setDir(d);
    setStep(s);
    setErr(null);
    stepIn.setValue(0);
    Animated.timing(stepIn, { toValue: 1, duration: 420, easing: EASE, useNativeDriver: true }).start();
  }, [stepIn]);

  const signOut = async () => {
    if (!armed) {
      setArmed(true);
      setTimeout(() => setArmed(false), 3000);
      return;
    }
    await api.signOutSelf().catch(() => undefined); // revoke here if reachable; the key is forgotten either way
    await forgetPairing().catch(() => undefined);
    await wipeLocal().catch(() => undefined);
    onClose();
    router.replace('/pair');
  };

  const status = statusOf(conn);
  const ep = getActiveEndpoint();
  const top = quota?.providers.flatMap((p) => p.windows.map((w) => ({ p: p.name, ...w }))).filter((w) => w.used_pct != null)
    .sort((a, b) => (b.used_pct ?? 0) - (a.used_pct ?? 0))[0];
  const ruleCount = rules?.groups.reduce((n, g) => n + g.items.length, 0) ?? 0;
  const sheetH = Math.min(H * 0.86, step === 'root' ? 640 : step === 'world' ? 470 : step === 'quota' ? 540 : step === 'rules' ? 560 : 430);

  const txt = (size: number, color = t.ink, family = ui) => ({ fontFamily: family, fontSize: size, color });
  const Row = ({ title, detail, onPress, right, label }: { title: string; detail?: string; onPress?: () => void; right?: React.ReactNode; label?: string }) => (
    <Pressable onPress={onPress} disabled={!onPress} accessibilityRole={onPress ? 'button' : undefined} accessibilityLabel={label ?? `${title}${detail ? `, ${detail}` : ''}`}
      style={({ pressed }) => ({ flexDirection: 'row', alignItems: 'center', gap: 14, minHeight: 56, paddingHorizontal: 6, borderTopWidth: 1, borderTopColor: t.line, opacity: pressed ? 0.7 : 1 })}>
      <View style={{ flex: 1, gap: 1 }}>
        <Text style={txt(15.5, t.ink, uiSemi)}>{title}</Text>
        {detail ? <Text numberOfLines={1} style={txt(11, t.ink2, mono)}>{detail}</Text> : null}
      </View>
      {right ?? (onPress ? <Svg width={16} height={16} viewBox="0 0 24 24"><Path d="M9 5l7 7-7 7" fill="none" stroke={t.ink3} strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" /></Svg> : null)}
    </Pressable>
  );
  const Switch = ({ on, onChange, label }: { on: boolean; onChange: (v: boolean) => void; label: string }) => (
    <Pressable accessibilityRole="switch" accessibilityState={{ checked: on }} accessibilityLabel={label} onPress={() => onChange(!on)}
      style={{ width: 48, height: 30, borderRadius: 15, backgroundColor: on ? t.accent : t.bg2, borderWidth: 1, borderColor: t.line, padding: 3 }}>
      <View style={{ width: 22, height: 22, borderRadius: 11, backgroundColor: on ? t.raise : t.ink3, transform: [{ translateX: on ? 18 : 0 }] }} />
    </Pressable>
  );

  return (
    <View pointerEvents={open ? 'auto' : 'none'} style={{ position: 'absolute', left: 0, right: 0, top: 0, bottom: 0 }}>
      <Animated.View style={{ position: 'absolute', left: 0, right: 0, top: 0, bottom: 0, backgroundColor: 'rgba(10,6,4,.22)', opacity: sheet }}>
        <Pressable accessibilityLabel="Close menu" onPress={onClose} style={{ flex: 1 }} />
      </Animated.View>
      <Animated.View
        accessibilityViewIsModal
        style={{ position: 'absolute', left: 0, right: 0, bottom: 0, height: sheetH, backgroundColor: t.raise, borderTopLeftRadius: 30, borderTopRightRadius: 30,
                 paddingHorizontal: 14, paddingTop: 10, shadowColor: '#000', shadowOpacity: 0.12, shadowRadius: 20, shadowOffset: { width: 0, height: -6 },
                 transform: [{ translateY: sheet.interpolate({ inputRange: [0, 1], outputRange: [sheetH + 40, 0] }) }] }}
      >
        <View style={{ width: 40, height: 5, borderRadius: 3, backgroundColor: t.line, alignSelf: 'center' }} />
        <View style={{ flexDirection: 'row', alignItems: 'center', minHeight: 50 }}>
          {step !== 'root' ? (
            <Pressable accessibilityRole="button" accessibilityLabel="Back to menu" onPress={() => go('root', -1)} style={{ width: 40, height: 44, justifyContent: 'center' }}>
              <Svg width={20} height={20} viewBox="0 0 24 24"><Path d="M15 5l-7 7 7 7" fill="none" stroke={t.ink} strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" /></Svg>
            </Pressable>
          ) : null}
          <Text style={[txt(26 * world.displayScale, t.ink, display), { flex: 1, paddingLeft: 4 }]}>{TITLE[step]}</Text>
          <Pressable accessibilityRole="button" accessibilityLabel="Done" onPress={onClose} style={{ minHeight: 44, paddingHorizontal: 10, justifyContent: 'center' }}>
            <Text style={txt(14, t.ink, uiSemi)}>Done</Text>
          </Pressable>
        </View>
        <Animated.View style={{ flex: 1, opacity: stepIn, transform: [{ translateX: stepIn.interpolate({ inputRange: [0, 1], outputRange: [28 * dir, 0] }) }] }}>
          <ScrollView contentContainerStyle={{ paddingBottom: 28 }} showsVerticalScrollIndicator={false}>
            {err ? <Text style={[txt(13, '#B91C1C'), { padding: 6 }]}>{err}</Text> : null}
            {step === 'root' ? (
              <View>
                <Row title="World" detail={`${world.name} · ${mode === 'auto' ? 'matches phone' : dark ? 'night' : 'day'}${showcase ? ' · showcase' : ''}`} onPress={() => go('world')} />
                <Row title="Companion" detail={`${world.pet.name} the ${world.pet.kind}`} right={<Switch on={pet} onChange={setPet} label="Show companion" />} />
                <Row title="Quota" detail={top ? `${top.p} ${Math.round(top.used_pct ?? 0)}% of ${top.name}` : quota ? 'not reported' : 'asking providers…'} onPress={() => go('quota')} />
                <Row title="Rules" detail={rules ? `${ruleCount} rules Brain enforces` : '…'} onPress={() => go('rules')} />
                <Row title="This phone" detail={me ? `${showcase ? 'this phone' : me.name} · sign out` : 'sign out'} onPress={() => go('phone')} />
                <Row title="Quiet hours" detail={rules ? `${rules.quiet_hours.from}–${rules.quiet_hours.to} · approvals still ring` : '…'}
                  right={<Switch on={!!rules?.quiet_hours.enabled} label="Quiet hours"
                    onChange={(v) => api.setQuietHours(v).then((q) => setRules((r) => (r ? { ...r, quiet_hours: q } : r))).catch((e) => setErr(describeError(e)))} />} />
                <Row title="Connection" detail={`${status === 'live' ? 'live' : status}${ep && !showcase ? ` · ${clusterLabel(ep)}` : ''}`} />
                <Row title="Inbox" detail="everything waiting for you, and the pause switch" onPress={() => { onClose(); router.push('/inbox'); }} />
                <Row title="Threads" detail="every thread, by repo" onPress={() => { onClose(); router.push('/threads'); }} />
                <Row title="Jobs" detail="SLURM jobs and their logs" onPress={() => { onClose(); router.push('/runs'); }} />
                <Row title="Import sessions" detail="your terminal Claude and Codex sessions" onPress={() => { onClose(); router.push('/import'); }} />
                <Row title="Start something" detail="a thread, a run or a new idea" onPress={() => { onClose(); router.push('/new'); }} />
                <Pressable accessibilityRole="button" accessibilityLabel={armed ? 'Tap again to sign out' : 'Sign out of this phone'} onPress={signOut}
                  style={{ marginTop: 12, minHeight: 50, borderRadius: 16, backgroundColor: t.bg2, alignItems: 'center', justifyContent: 'center', borderWidth: armed ? 1.5 : 0, borderColor: t.needs }}>
                  <Text style={txt(15, t.accent, uiSemi)}>{armed ? 'Tap again to sign out' : 'Sign out of this phone'}</Text>
                </Pressable>
              </View>
            ) : null}
            {step === 'world' ? (
              <View style={{ gap: 14 }}>
                <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={{ gap: 10, paddingVertical: 2 }}>
                  {WORLD_IDS.map((id) => {
                    const w = WORLDS[id];
                    const tk = dark ? w.night : w.day;
                    const sel = id === world.id;
                    return (
                      <Pressable key={id} accessibilityRole="button" accessibilityState={{ selected: sel }} accessibilityLabel={`${w.name} world, ${w.where}`}
                        onPress={() => onPickWorld(id)}
                        style={{ width: 132, borderRadius: 22, overflow: 'hidden', borderWidth: 2, borderColor: sel ? t.ink : 'transparent', backgroundColor: tk.raise }}>
                        <Svg width={128} height={96} viewBox="0 0 128 96">
                          <Rect width={128} height={96} fill={tk.sSky1 ?? tk.sWall ?? tk.sPlank ?? tk.sW1 ?? tk.bg2} />
                          <MiniArt id={id} t={tk} />
                          <G transform="translate(64 82) scale(.26) translate(-100 -150)">
                            <Path d="M22 66C22 124 52 148 100 148C148 148 178 124 178 66Z" fill="#C98A4B" />
                            <Path d="M100 64m-78 0a78 42 0 1 0 156 0a78 42 0 1 0 -156 0" fill="#E9B275" />
                            <Path d="M100 64m-8 0a8 4 0 1 1 16 0a16 8 0 1 1 -32 0a26 13 0 1 1 52 0a38 19 0 1 1 -76 0" fill="none" stroke="#8A4320" strokeWidth={7} />
                          </G>
                        </Svg>
                        <View style={{ padding: 10, gap: 2 }}>
                          <Text style={{ fontFamily: w.fonts.display, fontSize: 19 * w.displayScale, color: tk.ink }}>{w.name}</Text>
                          <Text style={{ fontFamily: ui, fontSize: 11, color: tk.ink2 }}>{`${w.where} · ${w.pet.name}`}</Text>
                        </View>
                      </Pressable>
                    );
                  })}
                </ScrollView>
                <Text style={txt(13, t.ink2)}>{world.line}</Text>
                <View accessibilityRole="radiogroup" style={{ flexDirection: 'row', backgroundColor: t.bg2, borderRadius: 999, padding: 3 }}>
                  {(['day', 'night', 'auto'] as Mode[]).map((m) => (
                    <Pressable key={m} accessibilityRole="radio" accessibilityState={{ checked: mode === m }} onPress={() => setMode(m)}
                      style={{ flex: 1, minHeight: 38, borderRadius: 999, alignItems: 'center', justifyContent: 'center', backgroundColor: mode === m ? t.ink : 'transparent' }}>
                      <Text style={txt(13, mode === m ? t.raise : t.ink, uiSemi)}>{m === 'day' ? 'Day' : m === 'night' ? 'Night' : 'Match phone'}</Text>
                    </Pressable>
                  ))}
                </View>
                <Row title="Showcase" detail="Hide names and numbers to show it off" right={<Switch on={showcase} onChange={setShowcase} label="Showcase" />} />
              </View>
            ) : null}
            {step === 'quota' ? (
              <View>
                {(quota?.providers ?? []).map((p) => (
                  <View key={p.name} style={{ paddingVertical: 12, paddingHorizontal: 4, borderTopWidth: 1, borderTopColor: t.line, gap: 8 }}>
                    <View style={{ flexDirection: 'row', alignItems: 'baseline' }}>
                      <Text style={[txt(15.5, t.ink, uiSemi), { flex: 1 }]}>{{ claude: 'Claude Code', codex: 'Codex', agy: 'Antigravity' }[p.name] ?? p.name}</Text>
                      {p.plan && !showcase ? <Text style={txt(11, t.ink3, mono)}>{`${p.plan} plan`}</Text> : null}
                    </View>
                    {p.windows.length ? (
                      <View style={{ flexDirection: 'row', gap: 14, flexWrap: 'wrap' }}>
                        {p.windows.slice(0, 2).map((w) => <Gauge key={w.name} pct={w.used_pct ?? 0} name={w.name} resets={w.resets_at} t={t} mono={mono} ui={ui} />)}
                      </View>
                    ) : (
                      <Text style={txt(13, t.ink2)}>{p.error ? `Not reported: ${p.error}` : 'Not reported'}</Text>
                    )}
                    <Text style={txt(10.5, t.ink3, mono)}>{p.source}</Text>
                  </View>
                ))}
                <Text style={[txt(11.5, t.ink3), { paddingTop: 10, borderTopWidth: 1, borderTopColor: t.line }]}>
                  {quota ? `As each provider reported it, ${ago(quota.as_of)}. Cinnamon never estimates quota from its own token counts.` : 'Asking providers…'}
                </Text>
                <Pressable accessibilityRole="button" onPress={() => { setQuota(null); api.quota(true).then(setQuota).catch((e) => setErr(describeError(e))); }}
                  style={{ marginTop: 10, alignSelf: 'flex-start', paddingHorizontal: 14, minHeight: 40, borderRadius: 999, backgroundColor: t.bg2, justifyContent: 'center' }}>
                  <Text style={txt(13, t.ink, uiSemi)}>Ask again</Text>
                </Pressable>
              </View>
            ) : null}
            {step === 'rules' ? (
              <View>
                {(rules?.groups ?? []).map((g) => (
                  <View key={g.head}>
                    <Text style={[txt(10.5, t.ink3, mono), { paddingTop: 12, paddingBottom: 4, paddingHorizontal: 4, letterSpacing: 0.5 }]}>{g.head.toUpperCase()}</Text>
                    {g.items.map((it) => (
                      <View key={it.text} style={{ flexDirection: 'row', alignItems: 'center', gap: 10, minHeight: 48, padding: 4, borderTopWidth: 1, borderTopColor: t.line }}>
                        <Text style={[txt(14), { flex: 1 }]}>{it.text}</Text>
                        <Text style={{ fontFamily: uiSemi, fontSize: 11, borderRadius: 999, paddingHorizontal: 9, paddingVertical: 3, overflow: 'hidden',
                                       backgroundColor: it.chip === 'blocked' ? t.ink : t.needs, color: it.chip === 'blocked' ? t.raise : '#1A120D' }}>{it.chip}</Text>
                      </View>
                    ))}
                  </View>
                ))}
                <Text style={[txt(11.5, t.ink3), { padding: 4, paddingTop: 12 }]}>Enforced by Brain; changed only from the terminal.</Text>
              </View>
            ) : null}
            {step === 'phone' ? (
              <View style={{ gap: 4 }}>
                <Row title={`${me && !showcase ? me.name : 'This phone'}`} detail={me ? `paired ${when(me.created_at)} · seen ${when(me.last_seen)} · signs approvals` : '…'} />
                <Row title="Notifications" detail={me ? (me.web_push || Platform.OS !== 'web' ? 'on' : 'off: enable from pairing') : '…'} />
                <Row title="Earlier pairings" detail={me ? `${me.revoked_others} revoked` : '…'} />
                <Pressable accessibilityRole="button" accessibilityLabel={armed ? 'Tap again to sign out' : 'Sign out of this phone'} onPress={signOut}
                  style={{ marginTop: 12, minHeight: 50, borderRadius: 16, backgroundColor: t.bg2, alignItems: 'center', justifyContent: 'center', borderWidth: armed ? 1.5 : 0, borderColor: t.needs }}>
                  <Text style={txt(15, t.accent, uiSemi)}>{armed ? 'Tap again to sign out' : 'Sign out of this phone'}</Text>
                </Pressable>
                <Text style={[txt(11.5, t.ink3), { padding: 4 }]}>Signing out forgets this phone's key. Pairing again needs a code from the terminal.</Text>
              </View>
            ) : null}
          </ScrollView>
        </Animated.View>
      </Animated.View>
    </View>
  );
}

function Gauge({ pct, name, resets, t, mono, ui }: { pct: number; name: string; resets: number | null; t: Record<string, string>; mono?: string; ui?: string }) {
  const sweep = useRef(new Animated.Value(0)).current;
  useEffect(() => {
    Animated.timing(sweep, { toValue: 1, duration: 900, easing: Easing.out(Easing.cubic), useNativeDriver: false }).start();
  }, [sweep]);
  const [v, setV] = useState(0);
  useEffect(() => {
    const id = sweep.addListener(({ value }) => setV(value * pct));
    return () => sweep.removeListener(id);
  }, [sweep, pct]);
  const r = 24;
  const len = Math.PI * r;
  return (
    <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8, minWidth: 150 }}>
      <Svg width={58} height={34} viewBox="0 0 58 34">
        <Path d="M5 30a24 24 0 0 1 48 0" fill="none" stroke={t.line} strokeWidth={6} strokeLinecap="round" />
        <Path d="M5 30a24 24 0 0 1 48 0" fill="none" stroke={pct >= 80 ? t.needs : t.accent} strokeWidth={6} strokeLinecap="round"
          strokeDasharray={`${(len * Math.min(100, v)) / 100} ${len}`} />
      </Svg>
      <View>
        <Text style={{ fontFamily: mono, fontSize: 15, color: t.ink }}>{`${Math.round(pct)}%`}</Text>
        <Text style={{ fontFamily: ui, fontSize: 11, color: t.ink2 }}>{`${name}${resets ? ` · resets ${when(resets)}` : ''}`}</Text>
      </View>
    </View>
  );
}

function MiniArt({ id, t }: { id: WorldId; t: Record<string, string> }) {
  if (id === 'bakery') return <G><Rect y={70} width={128} height={26} fill={t.sWood} /><Rect x={86} y={14} width={30} height={28} fill={t.sSky1} /></G>;
  if (id === 'forest') return <G><Path d="M58 96C62 74 60 56 64 40H76C78 56 76 74 82 96Z" fill={t.sTrunk} /><Path d="M70 6a30 26 0 1 1 -.1 0ZM36 30a22 20 0 1 1 -.1 0ZM102 28a22 20 0 1 1 -.1 0Z" fill={t.sLeaf2} /></G>;
  if (id === 'rustic') return <G><Path d="M78 96V54C78 38 120 38 120 54V96Z" fill={t.sFirebox} /><Path d="M90 92C90 80 98 78 96 66C104 76 102 84 108 86C110 78 114 74 112 66C120 78 120 88 118 92Z" fill="#F0A23A" /></G>;
  if (id === 'space') return <G><Path d="M0 78C40 68 88 68 128 78V96H0Z" fill={t.sGround} /><Path d="M40 30a14 14 0 1 1 -.1 0Z" fill={t.sPlanet} /></G>;
  return <G><Path d="M0 76C30 70 70 78 128 72V96H0Z" fill={t.sSand} /><Path d="M18 78C10 54 24 38 16 18M110 78C118 54 104 38 112 18" fill="none" stroke={t.sKelp} strokeWidth={5} /></G>;
}
