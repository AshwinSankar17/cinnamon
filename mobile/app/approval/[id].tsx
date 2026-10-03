import { useCallback, useState } from 'react';
import { Alert, KeyboardAvoidingView, Platform, ScrollView, Text, TextInput, View } from 'react-native';
import { router, useFocusEffect, useLocalSearchParams } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { ConnectionBanner } from '@/components/ConnectionBanner';
import { BackHeader, Button, Card, Dot, EmptyNote, Mono, ProviderPill, SectionLabel } from '@/components/ui';
import { api, describeError } from '@/src/api';
import { hhmm } from '@/src/format';
import { getThread, upsertThreads } from '@/src/store';
import { colors, fonts, providerShort, type } from '@/src/theme';
import type { Approval, DecisionSummary, Thread } from '@/src/types';

function reversibleText(r: DecisionSummary['reversible']): string {
  if (typeof r === 'boolean') return r ? 'yes' : 'no';
  return r;
}

function nextProse(a: Approval): string {
  const d = a.decision_summary;
  if (!d) return 'Brain did not send a decision summary for this request.';
  const rev =
    typeof d.reversible === 'boolean'
      ? d.reversible
        ? 'It can be undone afterwards.'
        : 'It cannot be undone.'
      : `Reversibility: ${d.reversible}.`;
  const allow = `If you allow, Brain will ${d.verb} ${d.target}${d.blast_radius ? ` (${d.blast_radius})` : ''}.`;
  const deny = d.fallback ? `If you deny: ${d.fallback}` : 'If you deny, the agent is told no and carries on without it.';
  return [allow, rev, deny].join(' ');
}

export default function ApprovalScreen() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const insets = useSafeAreaInsets();
  const [approval, setApproval] = useState<Approval | null>(null);
  const [thread, setThread] = useState<Thread | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [note, setNote] = useState('');
  const [busy, setBusy] = useState<'allow' | 'deny' | null>(null);
  const [hint, setHint] = useState(false);

  // API v0.2 has no GET /v1/approvals/{id}; pending approvals come from /v1/pending.
  useFocusEffect(
    useCallback(() => {
      let cancelled = false;
      (async () => {
        try {
          const p = await api.pending();
          const a = p.approvals.find((x) => x.id === id) ?? null;
          if (cancelled) return;
          setApproval(a);
          setErr(a ? null : 'This approval is no longer pending. It may have been decided from another device.');
          if (a) {
            const local = await getThread(a.thread_id);
            if (local) setThread(local);
            else
              api.thread(a.thread_id).then(
                (t) => {
                  if (!cancelled) setThread(t);
                  upsertThreads([t]).catch(() => undefined);
                },
                () => undefined,
              );
          }
        } catch (e) {
          if (!cancelled) setErr(describeError(e));
        } finally {
          if (!cancelled) setLoaded(true);
        }
      })();
      return () => {
        cancelled = true;
      };
    }, [id]),
  );

  const decide = async (decision: 'allow' | 'deny') => {
    if (!approval) return;
    setBusy(decision);
    try {
      const res = await api.decide(approval.id, decision, decision === 'deny' ? note.trim() || undefined : undefined);
      setApproval(res);
      router.back();
    } catch (e) {
      Alert.alert('Decision not recorded', describeError(e));
    } finally {
      setBusy(null);
    }
  };

  const d = approval?.decision_summary;
  const subtitle = [thread?.title ?? approval?.thread_id, thread ? providerShort[thread.provider] : null].filter(Boolean).join(' · ');

  return (
    <View style={{ flex: 1, backgroundColor: colors.ground }}>
      <BackHeader title="Approval" subtitle={subtitle || ' '} backLabel="Back" />
      <View style={{ paddingTop: 8 }}>
        <ConnectionBanner />
      </View>
      <KeyboardAvoidingView style={{ flex: 1 }} behavior={Platform.OS === 'ios' ? 'padding' : undefined}>
        <ScrollView contentContainerStyle={{ paddingHorizontal: 16, paddingTop: 6, paddingBottom: 16, gap: 12 }} keyboardShouldPersistTaps="handled">
          {!loaded ? <EmptyNote>Loading…</EmptyNote> : null}
          {err ? <EmptyNote>{err}</EmptyNote> : null}

          {approval ? (
            <>
              <View style={{ flexDirection: 'row', alignItems: 'center', gap: 10, paddingHorizontal: 2 }}>
                <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6 }}>
                  <Dot color={colors.needsYou} />
                  <Text style={[type.status, { color: colors.needsYou }]}>
                    {approval.state === 'pending' ? 'needs you' : approval.state}
                  </Text>
                </View>
                <Mono numberOfLines={1} style={{ flex: 1 }}>
                  {approval.action_type} · pending since {hhmm(approval.created_at)}
                </Mono>
              </View>

              <Card style={{ borderColor: colors.needsYou, gap: 10 }}>
                <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
                  {thread ? <ProviderPill provider={thread.provider} variant="outline" /> : null}
                  <Text style={[type.cardTitle, { flex: 1 }]}>{approval.summary}</Text>
                </View>
                {d?.target ? (
                  <View style={{ backgroundColor: colors.surface2, borderRadius: 10, paddingVertical: 8, paddingHorizontal: 10 }}>
                    <Text selectable style={{ fontFamily: fonts.mono, fontSize: 12, lineHeight: 18, color: colors.text }}>
                      <Text style={{ color: colors.text3 }}>{d.verb ? `${d.verb} ` : ''}</Text>
                      {d.target}
                    </Text>
                  </View>
                ) : null}
                <View>
                  <Fact label="Blast radius" value={d?.blast_radius ?? 'not stated'} mono />
                  <Fact label="Reversible" value={d ? reversibleText(d.reversible) : 'not stated'} />
                  <Fact label="If denied" value={d?.fallback || 'the agent is told no'} last />
                </View>
              </Card>

              <View style={{ gap: 4, paddingHorizontal: 2 }}>
                <SectionLabel style={{ paddingTop: 0, paddingHorizontal: 0 }}>What happens next</SectionLabel>
                <Text style={type.proseSm}>{nextProse(approval)}</Text>
              </View>

              <View style={{ gap: 4 }}>
                <Text style={{ fontFamily: fonts.sansSemi, fontSize: 15, lineHeight: 22, color: colors.text }} nativeID="deny-note-label">
                  Tell it what to do instead
                </Text>
                <TextInput
                  accessibilityLabelledBy="deny-note-label"
                  accessibilityLabel="Tell it what to do instead"
                  value={note}
                  onChangeText={setNote}
                  placeholder="Optional, sent with Deny"
                  placeholderTextColor={colors.text3}
                  style={{
                    height: 44,
                    paddingHorizontal: 12,
                    borderRadius: 10,
                    backgroundColor: colors.surface,
                    borderWidth: 1,
                    borderColor: colors.border,
                    fontFamily: fonts.sans,
                    fontSize: 15,
                    color: colors.text,
                  }}
                />
              </View>
            </>
          ) : null}
        </ScrollView>

        {approval && approval.state === 'pending' ? (
          <View style={{ paddingTop: 10, paddingHorizontal: 16, paddingBottom: Math.max(insets.bottom, 16), borderTopWidth: 1, borderTopColor: colors.hairline, backgroundColor: colors.surface, gap: 6 }}>
            <View style={{ flexDirection: 'row', gap: 8 }}>
              <Button label="Deny" kind="destructive" style={{ flex: 1 }} busy={busy === 'deny'} disabled={!!busy} onPress={() => decide('deny')} />
              <Button
                label="Hold to allow"
                kind="primary"
                style={{ flex: 1 }}
                busy={busy === 'allow'}
                disabled={!!busy}
                delayLongPress={600}
                onPress={() => setHint(true)}
                onLongPress={() => decide('allow')}
                accessibilityLabel="Allow. Long-press to confirm."
              />
            </View>
            <Text style={{ fontFamily: fonts.sans, fontSize: 12, lineHeight: 16, color: hint ? colors.accent : colors.text3, textAlign: 'center' }}>
              {hint ? 'Press and hold Allow to confirm.' : 'Pending until you decide, never auto-denied. Your decision is signed by this phone.'}
            </Text>
          </View>
        ) : null}
      </KeyboardAvoidingView>
    </View>
  );
}

function Fact({ label, value, mono, last }: { label: string; value: string; mono?: boolean; last?: boolean }) {
  return (
    <View style={{ flexDirection: 'row', alignItems: 'baseline', gap: 10, paddingTop: 6, paddingBottom: last ? 0 : 6, borderTopWidth: 1, borderTopColor: colors.hairline }}>
      <Text style={{ width: 84, fontFamily: fonts.sansSemi, fontSize: 12, lineHeight: 16, color: colors.text3 }}>{label}</Text>
      <Text style={mono ? { flex: 1, fontFamily: fonts.mono, fontSize: 12, lineHeight: 18, color: colors.text } : { flex: 1, fontFamily: fonts.sans, fontSize: 14, lineHeight: 20, color: colors.text }}>
        {value}
      </Text>
    </View>
  );
}
