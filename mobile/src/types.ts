/** Wire types for Cluster Brain protocol v0.2 (protocol/API.md). */
import type { ThreadState } from './theme';

export type Provider = 'claude' | 'codex' | 'gemini' | 'fake';
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
};

/** API.md does not define Repo; these are the fields the app reads, all others are kept as-is. */
export type Repo = {
  id: string;
  name?: string;
  path?: string;
  default_branch?: string;
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
export type TurnStarted = EventBase<'turn.started', { turn_id: string; provider: Provider }>;
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
  { provider: Provider; status: 'rate_limited' | 'retrying' | 'error'; resets_at?: string | number; message?: string }
>;
export type SystemNotice = EventBase<'system.notice', { text: string }>;
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
  name: string;
  state: string;
  gpus: number | null;
  elapsed: string | number | null;
  time_limit: string | null;
};

export type Wait = { wait_id?: string; kind?: string; predicate?: string; thread_id?: string; [k: string]: unknown };

/** `since` is not specified field-by-field in API.md; the app reads these if present. */
export type Since = {
  ts?: string | number;
  text?: string;
  summary?: string;
  jobs_finished?: number;
  jobs_failed?: number;
  files_changed?: number;
  turns?: number;
  [k: string]: unknown;
};

export type Pending = {
  approvals: Approval[];
  waits: Wait[];
  jobs: Job[];
  threads_needing_you: Thread[];
  since: Since;
};

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
  job_id?: string;
  endpoint?: string;
  endpoint_sig?: string;
  ts?: string;
};
