import { useState, type ReactNode } from 'react';
import { Alert, KeyboardAvoidingView, Platform, ScrollView, Text, TextInput, View } from 'react-native';
import { router } from 'expo-router';
import { CameraView, useCameraPermissions } from 'expo-camera';
import * as Device from 'expo-device';
import { Icon } from '@/components/Icon';
import { Badge, Button, Card, Chip, Dot, Mono, SectionLabel, TabHeader } from '@/components/ui';
import { completePairing, describeError, forgetPairing, getPairing, health, clusterLabel } from '@/src/api';
import { b64urlDecode } from '@/src/b64';
import { useConnection } from '@/src/connection';
import { fingerprint, fullFingerprint, parsePairingPayload, type PairingPayload } from '@/src/crypto';
import { registerForPush, type PushSetup } from '@/src/push';
import { wipeLocal } from '@/src/store';
import { colors, fonts, type } from '@/src/theme';
import { brainSocket } from '@/src/ws';

type Mode = 'scan' | 'paste' | 'manual';
type TestState =
  | { kind: 'idle' }
  | { kind: 'running' }
  | { kind: 'ok'; detail: string }
  | { kind: 'fail'; detail: string };

export default function PairScreen() {
  const conn = useConnection();
  const existing = conn.paired ? getPairing() : null;
  const [mode, setMode] = useState<Mode>('scan');
  const [payload, setPayload] = useState<PairingPayload | null>(null);
  const [parseErr, setParseErr] = useState<string | null>(null);
  const [test, setTest] = useState<TestState>({ kind: 'idle' });
  const [push, setPush] = useState<PushSetup | null>(null);
  const [pushBusy, setPushBusy] = useState(false);
  const paired = test.kind === 'ok';

  const accept = (raw: string) => {
    try {
      const p = parsePairingPayload(raw);
      if (b64urlDecode(p.brain_pub).length !== 32) throw new Error('brain_pub is not a 32-byte key.');
      setPayload(p);
      setParseErr(null);
      setTest({ kind: 'idle' });
    } catch (e) {
      setParseErr(e instanceof Error ? e.message : String(e));
    }
  };

  const pair = async () => {
    if (!payload) return;
    setTest({ kind: 'running' });
    try {
      if (existing) {
        brainSocket.stop();
        await forgetPairing();
        await wipeLocal();
      }
      const name = Device.deviceName ?? Device.modelName ?? 'phone';
      const r = await completePairing(payload, name);
      const h = await health();
      setTest({
        kind: 'ok',
        detail: `device ${r.deviceId} · Brain ${h.version} · rung ${h.rung}${h.paused ? ' · paused' : ''}`,
      });
    } catch (e) {
      setTest({ kind: 'fail', detail: describeError(e) });
    }
  };

  const enablePush = async () => {
    setPushBusy(true);
    setPush(await registerForPush(true));
    setPushBusy(false);
  };

  const unpair = () =>
    Alert.alert('Forget this Brain?', 'This phone will stop receiving updates until you pair again.', [
      { text: 'Keep', style: 'cancel' },
      {
        text: 'Forget',
        style: 'destructive',
        onPress: async () => {
          brainSocket.stop();
          await forgetPairing();
          await wipeLocal();
        },
      },
    ]);

  const title = payload ? `Pair with ${clusterLabel(payload.endpoint)}` : 'Pair with Brain';

  return (
    <View style={{ flex: 1, backgroundColor: colors.ground }}>
      <TabHeader title={title} />
      <KeyboardAvoidingView style={{ flex: 1 }} behavior={Platform.OS === 'ios' ? 'padding' : undefined}>
        <ScrollView contentContainerStyle={{ paddingHorizontal: 16, paddingTop: 4, paddingBottom: 24, gap: 12 }} keyboardShouldPersistTaps="handled">
          {existing && !paired ? (
            <Card>
              <Text style={type.body}>
                This phone is paired with <Text style={{ fontFamily: fonts.mono }}>{clusterLabel(existing.endpoint)}</Text>
                {conn.rest === 'identity_mismatch' ? ', but the endpoint failed the identity check.' : conn.rest === 'revoked' ? ', but Brain revoked it.' : '.'}
              </Text>
              <Mono>device {existing.deviceId} · key {fingerprint(b64urlDecode(existing.brainPub))}</Mono>
              <View style={{ flexDirection: 'row', gap: 8 }}>
                <Button label="Open Inbox" style={{ flex: 1 }} onPress={() => router.replace('/')} />
                <Button label="Forget" kind="destructive" style={{ flex: 1 }} onPress={unpair} />
              </View>
            </Card>
          ) : null}

          <Card style={{ gap: 12 }}>
            <Step n={1} done title={<Text style={stepTitle}>Run <Text style={{ fontFamily: fonts.mono, fontSize: 13 }}>brain pair</Text> on the cluster</Text>}>
              <Mono>prints a QR, a short code and the pairing JSON · 5-minute TTL</Mono>
            </Step>

            <Step n={2} done={!!payload} active={!payload} title={<Text style={stepTitle}>Scan the QR, paste the JSON, or type the code</Text>}>
              <View style={{ flexDirection: 'row', gap: 6, flexWrap: 'wrap' }}>
                <Chip label="Scan" selected={mode === 'scan'} onPress={() => setMode('scan')} />
                <Chip label="Paste JSON" selected={mode === 'paste'} onPress={() => setMode('paste')} />
                <Chip label="Type code" selected={mode === 'manual'} onPress={() => setMode('manual')} />
              </View>
              {mode === 'scan' ? <Scanner onData={accept} paused={!!payload} /> : null}
              {mode === 'paste' ? <PasteBox onSubmit={accept} /> : null}
              {mode === 'manual' ? <ManualEntry onPayload={(p) => { setPayload(p); setParseErr(null); }} /> : null}
              {parseErr ? <Text style={[type.small, { color: colors.failed }]}>{parseErr}</Text> : null}
            </Step>

            <Step
              n={3}
              done={!!payload}
              active={!!payload && test.kind !== 'ok'}
              title={
                <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
                  <Text style={[stepTitle, { flex: 1 }]}>Pinned identity</Text>
                  {payload ? <Badge label="pinned before any exchange" tone="ok" /> : null}
                </View>
              }
            >
              {payload ? (
                <>
                  <Mono>ed25519 {fingerprint(b64urlDecode(payload.brain_pub))} · {clusterLabel(payload.endpoint)}</Mono>
                  <Mono numberOfLines={2} style={{ color: colors.text3 }}>
                    {fullFingerprint(b64urlDecode(payload.brain_pub))}
                  </Mono>
                  <Mono numberOfLines={1}>{payload.endpoint}</Mono>
                </>
              ) : (
                <Mono>waiting for a pairing payload</Mono>
              )}
            </Step>

            <Step n={4} done={test.kind === 'ok'} active={!!payload && test.kind !== 'ok'} title={<Text style={stepTitle}>Pair and test the connection</Text>}>
              {test.kind === 'idle' && payload ? <Button label="Pair this phone" kind="primary" onPress={pair} /> : null}
              {test.kind === 'running' ? <Button label="Pairing" kind="primary" busy /> : null}
              {test.kind === 'ok' ? (
                <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6 }}>
                  <Dot color={colors.running} />
                  <Mono style={{ color: colors.running, flex: 1 }}>{test.detail}</Mono>
                </View>
              ) : null}
              {test.kind === 'fail' ? (
                <>
                  <View style={{ flexDirection: 'row', alignItems: 'flex-start', gap: 6 }}>
                    <View style={{ paddingTop: 4 }}>
                      <Dot color={colors.failed} />
                    </View>
                    <Text style={[type.small, { color: colors.failed, flex: 1 }]}>{test.detail}</Text>
                  </View>
                  <Button label="Try again" onPress={pair} />
                </>
              ) : null}
            </Step>

            <Step
              n={5}
              done={push?.status === 'ready'}
              active={paired && push?.status !== 'ready'}
              title={
                <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
                  <Text style={[stepTitle, { flex: 1 }]}>Push</Text>
                  {push ? <Badge label={push.status} tone={push.status === 'ready' ? 'ok' : 'muted'} /> : null}
                </View>
              }
            >
              <Mono>FCM: approvals and failures reach you when the app is closed (skippable)</Mono>
              {push?.detail ? <Mono style={{ color: colors.text3 }}>{push.detail}</Mono> : null}
              {paired && push?.status !== 'ready' ? <Button label="Enable notifications" busy={pushBusy} onPress={enablePush} /> : null}
            </Step>
          </Card>

          <Card style={{ gap: 4 }}>
            <SectionLabel style={{ paddingTop: 0, paddingHorizontal: 0 }}>What Brain may do on this account</SectionLabel>
            <Text style={type.proseSm}>
              Brain runs as you on the cluster. This phone can read threads, send messages, stop turns, cancel jobs and decide
              approvals; every decision is signed by a key that never leaves this phone.
            </Text>
          </Card>

          <Button label="Open Inbox" kind="primary" disabled={!paired} onPress={() => router.replace('/')} />
        </ScrollView>
      </KeyboardAvoidingView>
    </View>
  );
}

const stepTitle = { fontFamily: fonts.sansSemi, fontSize: 15, lineHeight: 20, color: colors.text };

function Step({ n, done, active, title, children }: { n: number; done?: boolean; active?: boolean; title: ReactNode; children?: ReactNode }) {
  return (
    <View style={{ flexDirection: 'row', alignItems: 'flex-start', gap: 10 }}>
      <View
        style={{
          width: 22,
          height: 22,
          borderRadius: 11,
          alignItems: 'center',
          justifyContent: 'center',
          backgroundColor: done ? colors.runningTint : active ? colors.accentTint : colors.surface,
          borderWidth: done ? 0 : 1,
          borderColor: active ? colors.accent : colors.border,
        }}
      >
        {done ? (
          <Icon name="check" size={14} color={colors.running} strokeWidth={2.25} />
        ) : (
          <Text style={{ fontFamily: fonts.mono, fontSize: 12, color: active ? colors.accent : colors.text2 }}>{n}</Text>
        )}
      </View>
      <View style={{ flex: 1, minWidth: 0, gap: 6 }}>
        {title}
        {children}
      </View>
    </View>
  );
}

function Scanner({ onData, paused }: { onData: (raw: string) => void; paused: boolean }) {
  const [perm, requestPerm] = useCameraPermissions();
  const [last, setLast] = useState<string | null>(null);
  if (!perm) return <Mono>checking camera permission</Mono>;
  if (!perm.granted) {
    return (
      <View style={{ gap: 6 }}>
        <Text style={type.small}>Camera access is needed to scan the pairing QR.</Text>
        <Button label="Allow camera" onPress={requestPerm} />
      </View>
    );
  }
  if (paused) return <Mono>QR read · identity pinned below</Mono>;
  return (
    <View style={{ height: 220, borderRadius: 12, overflow: 'hidden', backgroundColor: colors.surface2, borderWidth: 1, borderColor: colors.border }}>
      <CameraView
        style={{ flex: 1 }}
        facing="back"
        barcodeScannerSettings={{ barcodeTypes: ['qr'] }}
        onBarcodeScanned={({ data }) => {
          if (data && data !== last) {
            setLast(data);
            onData(data);
          }
        }}
      />
    </View>
  );
}

function PasteBox({ onSubmit }: { onSubmit: (raw: string) => void }) {
  const [raw, setRaw] = useState('');
  return (
    <View style={{ gap: 6 }}>
      <TextInput
        accessibilityLabel="Pairing JSON"
        value={raw}
        onChangeText={setRaw}
        multiline
        autoCapitalize="none"
        autoCorrect={false}
        placeholder='{"v":1,"brain_pub":"…","endpoint":"https://…","code":"…","secret":"…","exp":…}'
        placeholderTextColor={colors.text3}
        style={[inputStyle, { height: 110, paddingTop: 10, fontFamily: fonts.mono, fontSize: 12, textAlignVertical: 'top' }]}
      />
      <Button label="Use this payload" kind="primary" disabled={!raw.trim()} onPress={() => onSubmit(raw)} />
    </View>
  );
}

/**
 * Code + endpoint entry. API v0.2's transcript MAC needs the 32-byte `secret`, which only the QR /
 * JSON carries, so this path also asks for it, and pins brain_pub from /v1/health only after the
 * user confirms the fingerprint matches what `brain pair` printed (trust on first use, explicitly).
 */
function ManualEntry({ onPayload }: { onPayload: (p: PairingPayload) => void }) {
  const [endpoint, setEndpoint] = useState('https://');
  const [code, setCode] = useState('');
  const [secret, setSecret] = useState('');
  const [found, setFound] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const lookup = async () => {
    setBusy(true);
    setErr(null);
    try {
      const h = await health(endpoint.trim());
      setFound(h.brain_pub);
    } catch (e) {
      setErr(describeError(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <View style={{ gap: 8 }}>
      <Field label="Endpoint">
        <TextInput value={endpoint} onChangeText={(t) => { setEndpoint(t); setFound(null); }} autoCapitalize="none" autoCorrect={false} keyboardType="url" style={[inputStyle, { fontFamily: fonts.mono, fontSize: 14 }]} accessibilityLabel="Endpoint" />
      </Field>
      <Field label="Pairing code">
        <TextInput value={code} onChangeText={(t) => setCode(t.toUpperCase())} autoCapitalize="characters" autoCorrect={false} placeholder="AQ7K-3H9D" placeholderTextColor={colors.text3} style={[inputStyle, { fontFamily: fonts.mono, fontSize: 14, letterSpacing: 0.56 }]} accessibilityLabel="Pairing code" />
      </Field>
      <Field label="Secret (from brain pair)">
        <TextInput value={secret} onChangeText={setSecret} autoCapitalize="none" autoCorrect={false} style={[inputStyle, { fontFamily: fonts.mono, fontSize: 12 }]} accessibilityLabel="Pairing secret" />
      </Field>
      {!found ? (
        <Button label="Fetch Brain identity" busy={busy} disabled={!/^https?:\/\/.+/.test(endpoint.trim()) || !code.trim() || !secret.trim()} onPress={lookup} />
      ) : (
        <View style={{ gap: 6 }}>
          <Text style={type.small}>Compare with the fingerprint printed by brain pair:</Text>
          <Mono style={{ color: colors.text }}>{fullFingerprint(b64urlDecode(found))}</Mono>
          <Button
            label="Fingerprint matches, pin it"
            kind="primary"
            onPress={() =>
              onPayload({ v: 1, brain_pub: found, endpoint: endpoint.trim(), code: code.trim(), secret: secret.trim(), exp: Math.floor(Date.now() / 1000) + 300 })
            }
          />
        </View>
      )}
      {err ? <Text style={[type.small, { color: colors.failed }]}>{err}</Text> : null}
    </View>
  );
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <View style={{ gap: 4 }}>
      <Text style={type.label}>{label}</Text>
      {children}
    </View>
  );
}

const inputStyle = {
  minHeight: 44,
  paddingHorizontal: 12,
  borderRadius: 10,
  backgroundColor: colors.surface,
  borderWidth: 1,
  borderColor: colors.border,
  color: colors.text,
};

