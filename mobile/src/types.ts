/** Wire types for Cluster Brain protocol v0.2 (protocol/API.md). */
import type { Item as AttentionItem } from './attention';
import type { ThreadState } from './theme';

export type Provider = 'claude' | 'codex' | 'agy' | 'fake';

/** GET /v1/threads/{id}/skills: what the thread's provider runs as "/name args". */
export type SkillInfo = {
  name: string;
  description: string;
  kind: 'skill' | 'command';
  scope: 'project' | 'user' | 'plugin' | 'builtin';
  args?: string | null;
};

/** GET /v1/providers */
export type ProviderInfo = { name: Provider; native_handoff: boolean; defer: boolean; default?: boolean };
export type ThreadMode = 'readonly' | 'worktree';

export type Thread = {
  id: string;
  repo_id: string;
  title: string;
  mode: ThreadMode;
  branch: string | null;
  provider: Provider;
  state: ThreadState;
  pinned: boolean;
  archived: boolean;
  muted: boolean;
  parent_id: string | null;
  fork_turn: number | null;
  last_seq: number;
  updated_at: string | number;
  last_event?: BrainEvent | null;
  /** The agent replied since the owner last opened the thread (Brain's read marker). */
  unseen?: boolean;
  /** Set for training runs: the run's own vault folder on the cluster. */
  vault_dir?: string | null;
};

/** API.md does not define Repo; these are the fields the app reads, all others are kept as-is. */
export type Repo = {
  id: string;
  name?: string;
  path?: string;
  default_branch?: string;
  vault_root?: string;
  [k: string]: unknown;
};

export type Actor = { kind: string; device_id?: string };

type EventBase<T extends string, P> = {
  event_id: string;
  thread_id: string;
  seq: number;
  ts: string | number;
  type: T;
  visibility?: string;
  turn_id?: string | null;
  actor?: Actor;
  payload: P;
  payload_ref?: string | null;
  hash?: string;
  prev_hash?: string;
};

export type UserMessage = EventBase<'user.message', { text: string; attachments?: unknown[] }>;
export type TurnStarted = EventBase<'turn.started', { turn_id: string; provider: Provider; wake_reason?: string | null; retry_of?: string | null }>;
export type AssistantText = EventBase<'assistant.text', { text: string }>;
export type ToolStarted = EventBase<
  'tool.started',
  { tool_use_id: string; tool: string; summary: string; input_preview?: string }
>;
export type ToolCompleted = EventBase<
  'tool.completed',
  { tool_use_id: string; tool: string; ok: boolean; exit_code?: number | null; output_preview?: string }
>;
export type FileDiff = EventBase<'file.diff', { path: string; added: number; removed: number; diff: string }>;
export type TurnEnded = EventBase<
  'turn.ended',
  {
    turn_id: string;
    text?: string;
    counts?: { reads?: number; edits?: number; commands?: number; failed?: number };
    diffstat?: { files?: number; added?: number; removed?: number };
    duration_ms?: number;
    usage?: { input_tokens?: number; output_tokens?: number };
    stop_reason?: string;
  }
>;
export type ApprovalRequested = EventBase<
  'approval.requested',
  { approval_id: string; action_type: string; summary: string; decision_summary?: DecisionSummary }
>;
export type ApprovalDecided = EventBase<
  'approval.decided',
  { approval_id: string; decision: 'allow' | 'deny'; note?: string }
>;
export type JobSubmitted = EventBase<
  'job.submitted',
  { job_id: string; name: string; partition?: string; gpus?: number; time_limit?: string }
>;
export type JobStateEv = EventBase<'job.state', { job_id: string; state: string; exit_code?: number; reason?: string }>;
export type WaitEv = EventBase<'wait.created' | 'wait.fired', { wait_id: string; kind: string; predicate: string }>;
export type ProviderStatus = EventBase<
  'provider.status',
  { provider: Provider; status: 'rate_limited' | 'retrying' | 'error' | 'blocked'; resets_at?: string | number; message?: string }
>;
export type ProviderSwitched = EventBase<'provider.switched', { from: Provider; to: Provider; history_items: number }>;
export type SystemNotice = EventBase<'system.notice', { text: string }>;
export type ListenReady = EventBase<'listen.ready', { listen_id: string; title: string; items: number; systems: number }>;
export type ListenRated = EventBase<'listen.rated', { listen_id: string; title: string; summary: string; rated_items: number }>;
export type UnknownEvent = EventBase<string, Record<string, unknown>>;

export type BrainEvent =
  | UserMessage
  | TurnStarted
  | AssistantText
  | ToolStarted
  | ToolCompleted
  | FileDiff
  | TurnEnded
  | ApprovalRequested
  | ApprovalDecided
  | JobSubmitted
  | JobStateEv
  | WaitEv
  | ProviderStatus
  | ProviderSwitched
  | ListenReady
  | ListenRated
  | SystemNotice;

export type DecisionSummary = {
  verb: string;
  target: string;
  blast_radius: string;
  reversible: boolean | string;
  fallback: string;
};

export type Approval = {
  id: string;
  thread_id: string;
  action_type: string;
  summary: string;
  decision_summary: DecisionSummary;
  state: 'pending' | 'allowed' | 'denied';
  created_at: string | number;
  decided_at: string | number | null;
};

export type Job = {
  job_id: string;
  thread_id: string | null;
  name: string; // the SLURM job name (brain.<thread8>.<run8>), for sacct
  label?: string; // what it is: the script name
  thread_title?: string | null;
  state: string;
  gpus: number | null;
  elapsed: string | number | null;
  time_limit: string | null;
  exit_code?: number | null;
  submitted_at?: number;
  updated_at?: number;
};

export type Wait = { wait_id?: string; kind?: string; predicate?: string; thread_id?: string; [k: string]: unknown };

/** `since` is not specified field-by-field in API.md; the app reads these if present. */
export type Since = {
  ts?: string | number;
  text?: string;
  summary?: string;
  jobs_finished?: number;
  jobs_failed?: number;
  edits?: number;
  turns?: number;
  [k: string]: unknown;
};

export type ListeningSummary = { id: string; thread_id: string; title: string; n_items: number; n_systems: number; created_at: number };

export type Pending = {
  approvals: Approval[];
  waits: Wait[];
  jobs: Job[];
  threads_needing_you: Thread[];
  listening?: ListeningSummary[];
  attention?: AttentionItem[]; // Brain's ranked list of what needs the owner (src/attention.ts)
  since: Since;
};

/** GET /v1/listening/{id}: a blind listening test (see runtime/brain/listening.py for listen.json). */
export type ListenItem = {
  id: string;
  text: string;
  samples: Record<string, string>;
  reference: string | null;
  prompt: string | null;
  metrics: Record<string, Record<string, number | string>>;
};
export type ItemRating = { scores?: Record<string, number>; best?: string | null; note?: string };
export type ListenResult = {
  rated_items: number;
  total_items: number;
  ties: number;
  ranking: string[];
  text: string;
  notes: string[];
  systems: Record<string, { label: string; mos: number | null; mos_ci95: number | null; n_scores: number; best: number }>;
};
export type ListeningTest = {
  id: string;
  thread_id: string;
  title: string;
  description: string;
  state: 'pending' | 'rated' | 'skipped';
  systems: { id: string; label: string }[];
  items: ListenItem[];
  metrics: Record<string, Record<string, number | string>>;
  ratings: Record<string, ItemRating> | null;
  summary: ListenResult | null;
};

export type FileEntry = { name: string; dir: boolean; size: number; mtime: number };
export type FileListing = { root: string; path: string; abs: string; entries: FileEntry[]; roots: string[] };

export type Health = { ok: boolean; brain_pub: string; version: string; rung: string; paused: boolean };

export type TokenPair = {
  access_token: string;
  expires_in: number;
  refresh_token: string;
  refresh_expires_in: number;
};

export type PushData = {
  v: '1';
  kind: 'approval' | 'failed' | 'done' | 'budget' | 'changed' | 'transport';
  thread_id?: string;
  approval_id?: string;
  listen_id?: string;
  job_id?: string;
  endpoint?: string;
  endpoint_sig?: string;
  ts?: string;
};

/** GET /v1/imports: one of the owner's terminal sessions Brain can pick up (runtime/brain/importer.py). */
export type ImportCandidate = {
  provider: 'claude' | 'codex';
  session_id: string;
  path: string;
  cwd: string;
  repo_path: string;
  title: string;
  last_ts: number;
  live: boolean;
  thread_id: string | null;
};

/** GET /v1/threads/{id}/activity: what the roll draws (src/roll.ts). */
export type { Activity } from './roll';

export type QuotaWindow = { name: string; used_pct: number | null; resets_at: number | null };
export type QuotaProvider = { name: string; plan: string | null; windows: QuotaWindow[]; source: string; error: string | null };
/** GET /v1/quota: what each provider itself reported; nothing estimated. */
export type Quota = { as_of: number; providers: QuotaProvider[] };

export type RuleItem = { text: string; chip: 'asks you' | 'blocked' };
export type QuietHours = { enabled: boolean; from: string; to: string };
/** GET /v1/rules: what Brain enforces, plus the quiet-hours switch. */
export type Rules = { groups: { head: string; items: RuleItem[] }[]; quiet_hours: QuietHours };

/** GET /v1/devices/self */
export type DeviceSelf = { id: string; name: string; created_at: number; last_seen: number | null; web_push: boolean; revoked_others: number };
