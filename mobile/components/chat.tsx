/** Chat render pieces for app/thread/[id].tsx (E-Chat / E-ChatLive). */
import { useState } from 'react';
import { ActivityIndicator, Pressable, ScrollView, Text, View } from 'react-native';
import { router } from 'expo-router';
import { api, describeError } from '@/src/api';
import { duration, kTokens, toMs } from '@/src/format';
import { colors, fonts, providerName, type } from '@/src/theme';
import { parseUnifiedDiff, type Block, type JobInfo, type Step, type Turn } from '@/src/turns';
import type { FileDiff } from '@/src/types';
import { Icon } from './Icon';
import { Badge, Dot, IconButton, Mono, ProviderPill } from './ui';

// ---------------------------------------------------------------------------------------------

export function UserBubble({ text }: { text: string }) {
  return (
    <View style={{ flexDirection: 'row', justifyContent: 'flex-end' }}>
      <View style={{ maxWidth: '82%', backgroundColor: colors.surface2, borderRadius: 18, paddingVertical: 10, paddingHorizontal: 14 }}>
        <Text style={type.body} selectable>
          {text}
        </Text>
      </View>
    </View>
  );
}

/** Prose with `inline code` spans. */
export function Prose({ text }: { text: string }) {
  const parts = text.split(/(`[^`\n]+`)/g);
  return (
    <Text style={type.bodyChat} selectable>
      {parts.map((p, i) =>
        p.startsWith('`') && p.endsWith('`') && p.length > 2 ? (
          <Text key={i} style={{ fontFamily: fonts.mono, fontSize: 13, backgroundColor: colors.surface2 }}>
            {p.slice(1, -1)}
          </Text>
        ) : (
          <Text key={i}>{p}</Text>
        ),
      )}
    </Text>
  );
}

// ---------------------------------------------------------------------------------------------

export function TurnView({ turn, now, onFork }: { turn: Turn; now: number; onFork: (seq: number) => void }) {
  const live = !turn.ended;
  const [open, setOpen] = useState<boolean | null>(null);
  const expanded = open ?? live;
  const started = toMs(turn.startedAt);
  const elapsed = turn.ended?.payload.duration_ms ?? (started !== null ? now - started : 0);
  const n = turn.steps.length;
  const failed = turn.steps.filter((s) => s.done && s.ok === false).length;

  return (
    <View style={{ gap: 10 }}>
      <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
        <ProviderPill provider={String(turn.provider)} variant="avatar" />
        <Text style={{ fontFamily: fonts.sansSemi, fontSize: 13, color: colors.text }}>
          {providerName[String(turn.provider)] ?? String(turn.provider)}
        </Text>
        <Text style={{ fontFamily: fonts.sans, fontSize: 12, color: colors.text3 }}>turn {turn.index}</Text>
      </View>

      {n > 0 || live ? (
        <Pressable
          accessibilityRole="button"
          accessibilityState={{ expanded }}
          onPress={() => setOpen(!expanded)}
          hitSlop={6}
          style={{
            alignSelf: 'flex-start',
            height: 32,
            flexDirection: 'row',
            alignItems: 'center',
            gap: 6,
            paddingLeft: 8,
            paddingRight: 10,
            borderRadius: 999,
            borderWidth: 1,
            borderColor: colors.hairline,
            backgroundColor: colors.surface,
          }}
        >
          <Icon name={expanded ? 'chevronDown' : 'chevronRight'} size={14} color={colors.text2} strokeWidth={2} />
          <Text style={{ fontFamily: fonts.sansMedium, fontSize: 13, color: colors.text2 }}>
            {live
              ? `Working · ${duration(elapsed)} · ${n} ${n === 1 ? 'step' : 'steps'}`
              : `Worked for ${duration(elapsed)} · ${n} ${n === 1 ? 'step' : 'steps'}${failed ? ` · ${failed} failed` : ''}`}
          </Text>
        </Pressable>
      ) : null}

      {expanded && n > 0 ? (
        <View style={{ borderLeftWidth: 2, borderLeftColor: colors.hairline, marginLeft: 12, paddingLeft: 12, paddingVertical: 2, gap: 2 }}>
          {turn.steps.map((s) => (
            <StepRow key={s.id} s={s} />
          ))}
        </View>
      ) : null}

      {turn.blocks.map((b) => (
        <BlockView key={b.key} b={b} />
      ))}

      {turn.ended ? <TurnFooter turn={turn} onFork={onFork} /> : null}
    </View>
  );
}

function StepRow({ s }: { s: Step }) {
  return (
    <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8, minHeight: 28 }}>
      {!s.done ? (
        <View style={{ width: 14, alignItems: 'center' }}>
          <Dot color={colors.accent} />
        </View>
      ) : (
        <Icon name={s.ok === false ? 'cross' : 'check'} size={14} color={s.ok === false ? colors.failed : colors.running} strokeWidth={2.25} />
      )}
      <Text style={{ fontFamily: s.done ? fonts.sans : fonts.sansSemi, fontSize: 13, lineHeight: 18, color: s.done ? colors.text2 : colors.text, flexShrink: 1 }} numberOfLines={2}>
        {s.summary}
      </Text>
      {s.exitCode !== undefined && s.exitCode !== null && s.exitCode !== 0 ? (
        <Mono style={{ color: colors.failed }}>exit {s.exitCode}</Mono>
      ) : null}
    </View>
  );
}

function TurnFooter({ turn, onFork }: { turn: Turn; onFork: (seq: number) => void }) {
  const p = turn.ended!.payload;
  const parts = [
    p.diffstat?.files ? `${p.diffstat.files} files` : null,
    p.usage ? `${kTokens(p.usage.input_tokens)} in · ${kTokens(p.usage.output_tokens)} out` : null,
    p.stop_reason && p.stop_reason !== 'end_turn' ? p.stop_reason.replace('_', ' ') : null,
  ].filter(Boolean);
  return (
    <View style={{ flexDirection: 'row', alignItems: 'center', marginLeft: -10 }}>
      <IconButton name="fork" label="Fork from this turn" color={colors.text2} iconSize={18} onPress={() => onFork(turn.ended!.seq)} />
      <View style={{ flex: 1 }} />
      <Mono style={{ color: colors.text3 }}>{parts.join(' · ')}</Mono>
    </View>
  );
}

// ---------------------------------------------------------------------------------------------

export function BlockView({ b }: { b: Block }) {
  switch (b.kind) {
    case 'text':
      return <Prose text={b.text} />;
    case 'diffs':
      return <DiffCard files={b.files} />;
    case 'terminal':
      return <TerminalCard step={b.step} />;
    case 'job':
      return <JobInline job={b.job} />;
    case 'approval':
      return <ApprovalInline b={b} />;
    case 'notice':
      return <NoticeRow text={b.text} tone={b.tone} />;
  }
}

const DIFF_PREVIEW = 40;

function DiffCard({ files }: { files: FileDiff[] }) {
  const added = files.reduce((n, f) => n + (f.payload.added ?? 0), 0);
  const removed = files.reduce((n, f) => n + (f.payload.removed ?? 0), 0);
  return (
    <View style={{ borderWidth: 1, borderColor: colors.hairline, borderRadius: 14, overflow: 'hidden' }}>
      <View
        style={{
          height: 40,
          flexDirection: 'row',
          alignItems: 'center',
          gap: 8,
          paddingLeft: 12,
          paddingRight: 12,
          backgroundColor: colors.surfaceMuted,
          borderBottomWidth: 1,
          borderBottomColor: colors.hairline,
        }}
      >
        <Text style={{ flex: 1, fontFamily: fonts.sansSemi, fontSize: 13, color: colors.text }}>
          {files.length} {files.length === 1 ? 'file' : 'files'} changed
        </Text>
        <Mono style={{ color: colors.running }}>+{added}</Mono>
        <Mono style={{ color: colors.failed }}>−{removed}</Mono>
      </View>
      {files.map((f, i) => (
        <DiffFile key={f.seq} f={f} first={i === 0} defaultOpen={files.length === 1} />
      ))}
    </View>
  );
}

function DiffFile({ f, first, defaultOpen }: { f: FileDiff; first: boolean; defaultOpen: boolean }) {
  const [open, setOpen] = useState(defaultOpen);
  const [all, setAll] = useState(false);
  const rows = open ? parseUnifiedDiff(f.payload.diff ?? '') : [];
  const shown = all ? rows : rows.slice(0, DIFF_PREVIEW);
  return (
    <View style={{ borderTopWidth: first ? 0 : 1, borderTopColor: colors.hairline }}>
      <Pressable
        accessibilityRole="button"
        accessibilityState={{ expanded: open }}
        onPress={() => setOpen(!open)}
        style={{ height: 44, flexDirection: 'row', alignItems: 'center', gap: 8, paddingHorizontal: 12, backgroundColor: colors.surface }}
      >
        <Icon name={open ? 'chevronDown' : 'chevronRight'} size={14} color={colors.text2} strokeWidth={2} />
        <Text numberOfLines={1} style={{ flex: 1, fontFamily: fonts.mono, fontSize: 12, color: colors.text }}>
          {f.payload.path}
        </Text>
        {f.payload.added ? <Mono style={{ color: colors.running }}>+{f.payload.added}</Mono> : null}
        {f.payload.removed ? <Mono style={{ color: colors.failed }}>−{f.payload.removed}</Mono> : null}
      </Pressable>
      {open ? (
        <>
          <ScrollView horizontal style={{ borderTopWidth: 1, borderTopColor: colors.hairline }}>
            <View style={{ paddingVertical: 2 }}>
              {shown.map((r, i) => {
                if (r.kind === 'hunk' || r.kind === 'meta') {
                  return (
                    <Text key={i} style={{ fontFamily: fonts.mono, fontSize: 12, lineHeight: 19, paddingHorizontal: 12, backgroundColor: colors.diffHunkBg, color: colors.text2 }}>
                      {r.text}
                    </Text>
                  );
                }
                const bg = r.kind === 'add' ? colors.diffAddBg : r.kind === 'del' ? colors.diffDelBg : colors.surface;
                const fg = r.kind === 'add' ? colors.diffAddFg : r.kind === 'del' ? colors.diffDelFg : colors.text;
                const gutter = r.kind === 'add' ? colors.diffAddGutter : r.kind === 'del' ? colors.diffDelGutter : colors.surfaceMuted;
                return (
                  <View key={i} style={{ flexDirection: 'row', backgroundColor: bg }}>
                    <Text style={{ width: 40, textAlign: 'right', paddingRight: 8, fontFamily: fonts.mono, fontSize: 12, lineHeight: 19, color: colors.text3, backgroundColor: gutter }}>
                      {r.n ?? ''}
                    </Text>
                    <Text style={{ width: 14, fontFamily: fonts.mono, fontSize: 12, lineHeight: 19, color: fg }}>
                      {r.kind === 'add' ? '+' : r.kind === 'del' ? '-' : ' '}
                    </Text>
                    <Text style={{ paddingRight: 12, fontFamily: fonts.mono, fontSize: 12, lineHeight: 19, color: fg }}>{r.text}</Text>
                  </View>
                );
              })}
            </View>
          </ScrollView>
          {f.payload_ref ? <Mono style={{ paddingHorizontal: 12, paddingVertical: 6 }}>diff truncated · blob {f.payload_ref}</Mono> : null}
          {!all && rows.length > DIFF_PREVIEW ? (
            <Pressable
              onPress={() => setAll(true)}
              style={{ height: 44, alignItems: 'center', justifyContent: 'center', borderTopWidth: 1, borderTopColor: colors.hairline, backgroundColor: colors.surfaceMuted }}
            >
              <Text style={{ fontFamily: fonts.sansSemi, fontSize: 12, color: colors.text2 }}>Show {rows.length - DIFF_PREVIEW} more lines</Text>
            </Pressable>
          ) : null}
        </>
      ) : null}
    </View>
  );
}

function TerminalCard({ step }: { step: Step }) {
  const [open, setOpen] = useState(step.ok === false);
  const [full, setFull] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const label = step.input || step.summary;
  const badge =
    step.ok === false ? <Badge label={step.exitCode !== undefined && step.exitCode !== null ? `exit ${step.exitCode}` : 'failed'} tone="fail" /> : <Badge label="ok" tone="ok" />;
  const loadFull = async () => {
    if (!step.outputRef) return;
    setLoading(true);
    try {
      setFull(await api.blobText(step.outputRef));
    } catch (e) {
      setErr(describeError(e));
    } finally {
      setLoading(false);
    }
  };
  return (
    <View style={{ borderWidth: 1, borderColor: colors.hairline, borderRadius: 14, overflow: 'hidden' }}>
      <Pressable
        accessibilityRole="button"
        accessibilityState={{ expanded: open }}
        onPress={() => setOpen(!open)}
        style={{ minHeight: 44, flexDirection: 'row', alignItems: 'center', gap: 8, paddingHorizontal: 12, backgroundColor: colors.surface }}
      >
        <Mono style={{ color: colors.text3 }}>$</Mono>
        <Text numberOfLines={1} style={{ flex: 1, fontFamily: fonts.mono, fontSize: 12, color: colors.text }}>
          {label}
        </Text>
        {badge}
        <Icon name={open ? 'chevronUp' : 'chevronDown'} size={14} color={colors.text2} strokeWidth={2} />
      </Pressable>
      {open ? (
        <>
          <ScrollView horizontal style={{ backgroundColor: colors.terminalBg }}>
            <Text selectable style={{ fontFamily: fonts.mono, fontSize: 12, lineHeight: 18, color: colors.terminalFg, paddingHorizontal: 12, paddingVertical: 10 }}>
              {full ?? step.output}
            </Text>
          </ScrollView>
          {step.outputRef && full === null ? (
            <Pressable
              onPress={loadFull}
              style={{ height: 44, alignItems: 'center', justifyContent: 'center', backgroundColor: colors.surfaceMuted, borderTopWidth: 1, borderTopColor: colors.hairline }}
            >
              {loading ? (
                <ActivityIndicator color={colors.accent} />
              ) : (
                <Text style={{ fontFamily: fonts.sansSemi, fontSize: 12, color: colors.text2 }}>{err ?? 'Output truncated · load full output'}</Text>
              )}
            </Pressable>
          ) : null}
        </>
      ) : null}
    </View>
  );
}

function jobColor(state: string): string {
  const s = state.toLowerCase();
  if (s === 'running') return colors.running;
  if (s === 'pending' || s === 'submitted') return colors.waiting;
  if (/fail|timeout|out_of_memory|cancel/.test(s)) return colors.failed;
  return colors.idle;
}

function JobInline({ job }: { job: JobInfo }) {
  const bits = [
    job.jobId,
    job.name,
    job.state.toLowerCase(),
    job.gpus ? `${job.gpus} GPU` : null,
    job.timeLimit,
    job.exitCode !== undefined && job.exitCode !== null ? `exit ${job.exitCode}` : null,
    job.reason,
  ].filter(Boolean);
  return (
    <Pressable
      accessibilityRole="button"
      onPress={() => router.push('/runs')}
      style={{
        minHeight: 44,
        flexDirection: 'row',
        alignItems: 'center',
        gap: 10,
        paddingHorizontal: 12,
        borderRadius: 12,
        backgroundColor: colors.surfaceMuted,
        borderWidth: 1,
        borderColor: colors.hairline,
      }}
    >
      <Dot color={jobColor(job.state)} />
      <Mono numberOfLines={1} style={{ flex: 1, color: colors.text }}>
        {bits.join(' · ')}
      </Mono>
      <Icon name="chevronRight" size={16} color={colors.text3} strokeWidth={2} />
    </Pressable>
  );
}

function ApprovalInline({ b }: { b: Extract<Block, { kind: 'approval' }> }) {
  const p = b.req.payload;
  const decided = b.decided?.payload.decision;
  return (
    <Pressable
      accessibilityRole="button"
      onPress={() => router.push(`/approval/${encodeURIComponent(p.approval_id)}`)}
      style={{
        borderRadius: 14,
        borderWidth: 1,
        borderColor: decided ? colors.hairline : colors.needsYou,
        backgroundColor: decided ? colors.surface : colors.needsYouTint,
        paddingHorizontal: 14,
        paddingVertical: 12,
        gap: 6,
      }}
    >
      <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
        <Dot color={decided ? (decided === 'allow' ? colors.running : colors.failed) : colors.needsYou} />
        <Text style={[type.status, { color: decided ? (decided === 'allow' ? colors.running : colors.failed) : colors.needsYou }]}>
          {decided ? (decided === 'allow' ? 'allowed' : 'denied') : 'needs you'}
        </Text>
        <Mono style={{ flex: 1 }} numberOfLines={1}>
          {p.action_type}
        </Mono>
      </View>
      <Text style={type.cardTitle}>{p.summary}</Text>
      {b.decided?.payload.note ? <Text style={type.small}>Note: {b.decided.payload.note}</Text> : null}
      {!decided ? <Text style={{ fontFamily: fonts.sansSemi, fontSize: 13, color: colors.accent }}>Review approval</Text> : null}
    </Pressable>
  );
}

function NoticeRow({ text, tone }: { text: string; tone: 'info' | 'warn' | 'fail' }) {
  const c = tone === 'fail' ? colors.failed : tone === 'warn' ? colors.needsYou : colors.text3;
  return (
    <View style={{ flexDirection: 'row', alignItems: 'center', gap: 10, minHeight: 28 }}>
      <View style={{ flex: 1, height: 1, backgroundColor: colors.hairline }} />
      <Text style={{ fontFamily: fonts.sans, fontSize: 12, lineHeight: 16, color: c, maxWidth: '80%', textAlign: 'center' }}>{text}</Text>
      <View style={{ flex: 1, height: 1, backgroundColor: colors.hairline }} />
    </View>
  );
}
