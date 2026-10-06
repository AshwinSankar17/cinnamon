/**
 * Folds a thread's event log into render items: user bubbles, turns (steps + ordered blocks +
 * footer), and stand-alone rows for events that arrive outside any turn (e.g. a job finishing
 * hours later). Pure function of the events; screens recompute on every merge.
 */
import { providerName } from './theme';
import type {
  ApprovalDecided,
  ApprovalRequested,
  BrainEvent,
  FileDiff,
  JobStateEv,
  JobSubmitted,
  Provider,
  TurnEnded,
} from './types';

const FILE_KEYS = ['file_path', 'path', 'notebook_path', 'AbsolutePath', 'TargetFile', 'FilePath'];
const VERB: Record<string, string> = {
  Read: 'Read', view_file: 'Read', Write: 'Wrote', write_to_file: 'Wrote', Edit: 'Edited', MultiEdit: 'Edited',
  replace_file_content: 'Edited', multi_replace_file_content: 'Edited', NotebookEdit: 'Edited', Glob: 'Listed',
  list_dir: 'Listed', Grep: 'Searched', grep_search: 'Searched', WebFetch: 'Fetched', WebSearch: 'Searched the web',
};

function parseInput(input?: string): Record<string, unknown> | null {
  if (!input) return null;
  try {
    const v = JSON.parse(input);
    return v && typeof v === 'object' ? (v as Record<string, unknown>) : null;
  } catch {
    return null; // input_preview is cut at 1500 chars: fall back to the summary
  }
}

/** The shell command a step ran, if it ran one. */
export function stepCommand(s: Pick<Step, 'tool' | 'input' | 'summary'>): string | null {
  const inp = parseInput(s.input);
  const cmd = inp?.command ?? inp?.CommandLine ?? inp?.cmd;
  if (typeof cmd === 'string') return cmd;
  if (Array.isArray(cmd)) return cmd.join(' ');
  return s.tool === 'Bash' || s.tool === 'run_command' ? s.summary : null;
}

function shortPath(p: string): string {
  const parts = p.split('/').filter(Boolean);
  return parts.length > 3 ? `…/${parts.slice(-3).join('/')}` : p;
}

/** One readable line for a step: the command for shell steps, "Edited …/src/model.py" for file tools. */
export function stepLabel(s: Pick<Step, 'tool' | 'input' | 'summary'>): string {
  const cmd = stepCommand(s);
  if (cmd) return cmd;
  const inp = parseInput(s.input);
  const raw = (inp && FILE_KEYS.map((k) => inp[k]).find((v) => typeof v === 'string')) as string | undefined;
  const fromSummary = /^(?:file_path|path|notebook_path)=(.+)$/.exec(s.summary ?? '')?.[1];
  const path = raw ?? fromSummary;
  const verb = VERB[s.tool] ?? s.tool;
  if (path) return `${verb} ${shortPath(path)}`;
  const q = inp?.pattern ?? inp?.query ?? inp?.url;
  if (typeof q === 'string') return `${verb} ${q}`;
  return s.summary || s.tool;
}

export type Step = {
  id: string;
  tool: string;
  summary: string;
  input?: string;
  done: boolean;
  ok?: boolean;
  exitCode?: number | null;
  output?: string;
  outputRef?: string | null;
  seq: number;
};

export type JobInfo = {
  jobId: string;
  name?: string;
  partition?: string;
  gpus?: number;
  timeLimit?: string;
  state: string;
  exitCode?: number;
  reason?: string;
  seq: number;
};

export type Block =
  | { kind: 'text'; key: string; text: string; ref?: string | null }
  | { kind: 'diffs'; key: string; files: FileDiff[] }
  | { kind: 'terminal'; key: string; step: Step }
  | { kind: 'job'; key: string; job: JobInfo }
  | { kind: 'approval'; key: string; req: ApprovalRequested; decided?: ApprovalDecided }
  | { kind: 'notice'; key: string; text: string; tone: 'info' | 'warn' | 'fail' }
  | { kind: 'listen'; key: string; listenId: string; title: string; detail: string; summary?: string };

export type Turn = {
  kind: 'turn';
  key: string;
  turnId: string;
  index: number;
  provider: Provider | string;
  startedAt: string | number;
  startSeq: number;
  steps: Step[];
  blocks: Block[];
  ended?: TurnEnded;
};

export type Item =
  | { kind: 'user'; key: string; text: string; seq: number; fromDevice?: string }
  | Turn
  | { kind: 'row'; key: string; block: Block };

export function buildItems(events: BrainEvent[]): Item[] {
  const items: Item[] = [];
  const turns = new Map<string, Turn>();
  const steps = new Map<string, Step>();
  const approvals = new Map<string, Extract<Block, { kind: 'approval' }>>();
  const jobs = new Map<string, Extract<Block, { kind: 'job' }>>();
  let current: Turn | null = null;
  let turnCount = 0;

  const container = (e: BrainEvent): Block[] | null => {
    const byId = e.turn_id ? turns.get(e.turn_id) : undefined;
    if (byId) return byId.blocks;
    if (current && !current.ended) return current.blocks;
    return null;
  };
  const place = (e: BrainEvent, b: Block) => {
    const c = container(e);
    if (c) c.push(b);
    else items.push({ kind: 'row', key: b.key, block: b });
  };

  for (const e of events) {
    const key = `${e.thread_id}:${e.seq}`;
    switch (e.type) {
      case 'user.message':
        items.push({ kind: 'user', key, text: e.payload.text, seq: e.seq, fromDevice: e.actor?.device_id });
        break;

      case 'turn.started': {
        const turnId = e.payload.turn_id ?? e.turn_id ?? key;
        const t: Turn = {
          kind: 'turn',
          key,
          turnId,
          index: ++turnCount,
          provider: e.payload.provider,
          startedAt: e.ts,
          startSeq: e.seq,
          steps: [],
          blocks: [],
        };
        turns.set(turnId, t);
        items.push(t);
        current = t;
        break;
      }

      case 'tool.started': {
        const s: Step = {
          id: e.payload.tool_use_id,
          tool: e.payload.tool,
          summary: e.payload.summary,
          input: e.payload.input_preview,
          done: false,
          seq: e.seq,
        };
        steps.set(s.id, s);
        const t = (e.turn_id && turns.get(e.turn_id)) || (current && !current.ended ? current : null);
        if (t) t.steps.push(s);
        else items.push({ kind: 'row', key, block: { kind: 'notice', key, text: s.summary, tone: 'info' } });
        break;
      }

      case 'tool.completed': {
        let s = steps.get(e.payload.tool_use_id);
        if (!s) {
          s = { id: e.payload.tool_use_id, tool: e.payload.tool, summary: e.payload.tool, done: true, seq: e.seq };
          steps.set(s.id, s);
          const t = (e.turn_id && turns.get(e.turn_id)) || current;
          t?.steps.push(s);
        }
        s.done = true;
        s.ok = e.payload.ok;
        s.exitCode = e.payload.exit_code;
        s.output = e.payload.output_preview;
        s.outputRef = e.payload_ref ?? null;
        // only failures get a terminal card in the flow; other output opens from the step row
        if (s.ok === false && s.output && s.output.trim()) place(e, { kind: 'terminal', key, step: s });
        break;
      }

      case 'assistant.text':
        place(e, { kind: 'text', key, text: e.payload.text, ref: e.payload_ref });
        break;

      case 'file.diff': {
        const c = container(e);
        const last = c?.[c.length - 1];
        if (last && last.kind === 'diffs') last.files.push(e);
        else place(e, { kind: 'diffs', key, files: [e] });
        break;
      }

      case 'turn.ended': {
        const t: Turn | null | undefined = turns.get(e.payload.turn_id) ?? (e.turn_id ? turns.get(e.turn_id) : undefined) ?? current;
        if (t) t.ended = e;
        if (current === t) current = null;
        break;
      }

      case 'approval.requested': {
        const b: Extract<Block, { kind: 'approval' }> = { kind: 'approval', key, req: e };
        approvals.set(e.payload.approval_id, b);
        place(e, b);
        break;
      }

      case 'approval.decided': {
        const b = approvals.get(e.payload.approval_id);
        if (b) b.decided = e;
        else
          place(e, {
            kind: 'notice',
            key,
            text: `Approval ${e.payload.approval_id} ${e.payload.decision === 'allow' ? 'allowed' : 'denied'}`,
            tone: 'info',
          });
        break;
      }

      case 'job.submitted':
      case 'job.state': {
        const j = jobInfo(e);
        const existing = jobs.get(j.jobId);
        if (existing) {
          existing.job = { ...existing.job, ...stripUndefined(j) };
        } else {
          const b: Extract<Block, { kind: 'job' }> = { kind: 'job', key, job: j };
          jobs.set(j.jobId, b);
          place(e, b);
        }
        break;
      }

      case 'wait.created':
        place(e, { kind: 'notice', key, text: `Waiting: ${e.payload.predicate}`, tone: 'info' });
        break;
      case 'wait.fired':
        place(e, { kind: 'notice', key, text: `Wait fired: ${e.payload.predicate}`, tone: 'info' });
        break;
      case 'provider.status':
        place(e, {
          kind: 'notice',
          key,
          text: `${e.payload.provider} ${e.payload.status.replace('_', ' ')}${e.payload.message ? ` · ${e.payload.message}` : ''}`,
          tone: e.payload.status === 'error' ? 'fail' : 'warn',
        });
        break;
      case 'system.notice':
        if (/^Thread created on /.test(e.payload.text)) break; // the header already says where the thread lives
        place(e, { kind: 'notice', key, text: e.payload.text, tone: 'info' });
        break;
      case 'listen.ready':
        place(e, {
          kind: 'listen',
          key,
          listenId: e.payload.listen_id,
          title: e.payload.title,
          detail: `${e.payload.items} sentences × ${e.payload.systems} systems`,
        });
        break;
      case 'listen.rated': {
        const b = [...items].reverse().flatMap((i) => (i.kind === 'turn' ? i.blocks : i.kind === 'row' ? [i.block] : []))
          .find((x) => x.kind === 'listen' && x.listenId === e.payload.listen_id);
        if (b && b.kind === 'listen') b.summary = e.payload.summary;
        else place(e, { kind: 'listen', key, listenId: e.payload.listen_id, title: e.payload.title, detail: 'rated', summary: e.payload.summary });
        break;
      }
      case 'provider.switched':
        items.push({
          kind: 'row',
          key,
          block: {
            kind: 'notice',
            key,
            text: `Switched ${providerName[e.payload.from] ?? e.payload.from} → ${providerName[e.payload.to] ?? e.payload.to}` +
              ` · ${e.payload.history_items} history items carry over`,
            tone: 'info',
          },
        });
        break;
      default:
        break;
    }
  }
  return items;
}

function jobInfo(e: JobSubmitted | JobStateEv): JobInfo {
  if (e.type === 'job.submitted') {
    return {
      jobId: e.payload.job_id,
      name: e.payload.name,
      partition: e.payload.partition,
      gpus: e.payload.gpus,
      timeLimit: e.payload.time_limit,
      state: 'submitted',
      seq: e.seq,
    };
  }
  return {
    jobId: e.payload.job_id,
    state: e.payload.state,
    exitCode: e.payload.exit_code,
    reason: e.payload.reason,
    seq: e.seq,
  };
}

function stripUndefined<T extends object>(o: T): Partial<T> {
  return Object.fromEntries(Object.entries(o).filter(([, v]) => v !== undefined)) as Partial<T>;
}

// ---------------------------------------------------------------------------------------------
// Unified diff → rows

export type DiffRow = { kind: 'add' | 'del' | 'ctx' | 'hunk' | 'meta'; n: number | null; text: string };

export function parseUnifiedDiff(diff: string): DiffRow[] {
  const rows: DiffRow[] = [];
  let oldN = 0;
  let newN = 0;
  for (const line of diff.split('\n')) {
    if (line.startsWith('diff --git') || line.startsWith('index ') || line.startsWith('--- ') || line.startsWith('+++ ')) {
      continue;
    }
    const h = /^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/.exec(line);
    if (h) {
      oldN = Number(h[1]);
      newN = Number(h[2]);
      rows.push({ kind: 'hunk', n: null, text: line });
    } else if (line.startsWith('+')) {
      rows.push({ kind: 'add', n: newN++, text: line.slice(1) });
    } else if (line.startsWith('-')) {
      rows.push({ kind: 'del', n: oldN++, text: line.slice(1) });
    } else if (line.startsWith('\\')) {
      rows.push({ kind: 'meta', n: null, text: line });
    } else if (line.length || rows.length) {
      rows.push({ kind: 'ctx', n: newN, text: line.startsWith(' ') ? line.slice(1) : line });
      oldN++;
      newN++;
    }
  }
  while (rows.length && rows[rows.length - 1].kind === 'ctx' && rows[rows.length - 1].text === '') rows.pop();
  return rows;
}

/** Live thread state from the log: a started-but-not-ended turn means running. */
export function hasOpenTurn(items: Item[]): Turn | null {
  for (let i = items.length - 1; i >= 0; i--) {
    const it = items[i];
    if (it.kind === 'turn') return it.ended ? null : it;
  }
  return null;
}
