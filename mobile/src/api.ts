/**
 * Typed client for Cluster Brain protocol v0.2 (protocol/API.md).
 *
 * - Pairing state (endpoint, device_id, pinned brain_pub) and session tokens live in ./secure (Keystore / localStorage).
 * - Every authenticated call goes through authed(): it obtains a fresh access token (refresh, or a full
 *   challenge/token handshake that verifies Brain's signature over the Host we used), and on a 401
 *   re-establishes the session once and retries.
 * - Every POST/PATCH carries an Idempotency-Key that is reused across that call's retries.
 */
import { deleteSecret, getSecret, setSecret } from './secure';
import { b64urlDecode } from './b64';
import { setConnection, getConnection } from './connection';
import {
  buildPairComplete,
  hostOf,
  signDecision,
  signToken,
  uuid,
  verifyChallenge,
  verifyPairBrainSig,
  type PairingPayload,
} from './crypto';
import type {
  Approval,
  BrainEvent,
  Health,
  Job,
  Pending,
  Provider,
  ProviderInfo,
  FileListing,
  ItemRating,
  ListeningTest,
  ListenResult,
  Repo,
  SkillInfo,
  Thread,
  ThreadMode,
  TokenPair,
} from './types';

// ---------------------------------------------------------------------------------------------
// Errors

export class ApiError extends Error {
  constructor(
    public status: number,
    public body: unknown,
    message?: string,
  ) {
    super(message ?? `HTTP ${status}`);
  }
}
export class NetworkError extends Error {}
export class IdentityMismatchError extends Error {}
export class RevokedError extends Error {}
export class NotPairedError extends Error {}
/** Paired, but an already-paired device has not approved this one yet. */
export class PendingApprovalError extends Error {}

// ---------------------------------------------------------------------------------------------
// Persistent state

/** endpoint = primary relay; alternates = failover relays (from the QR's `alt` and GET /v1/endpoints). */
export type Pairing = { endpoint: string; deviceId: string; brainPub: string; pairedAt: number; alternates?: string[] };
type Tokens = { access: string; accessExp: number; refresh: string; refreshExp: number };

const PAIRING_KEY = 'cb.pairing.v1';
const TOKENS_KEY = 'cb.tokens.v1';
const TIMEOUT_MS = 15_000;
const SKEW_MS = 30_000;

let pairing: Pairing | null = null;
let tokens: Tokens | null = null;
let sessionInflight: Promise<string> | null = null;
/** The relay that last answered. Requests try it first, then the others; sessions are Brain-wide, so a token
 *  obtained through one relay works through another (the challenge is verified against the relay that served it). */
let active: string | null = null;

function candidates(): string[] {
  if (!pairing) return [];
  return [...new Set([active, pairing.endpoint, ...(pairing.alternates ?? [])].filter((x): x is string => !!x))];
}

export function getActiveEndpoint(): string | null {
  return active ?? pairing?.endpoint ?? null;
}

function setActive(endpoint: string): void {
  if (active === endpoint) return;
  active = endpoint;
  setConnection({ label: clusterLabel(endpoint) });
}

export function clusterLabel(endpoint: string): string {
  try {
    return hostOf(endpoint).split('.')[0];
  } catch {
    return endpoint;
  }
}

export async function loadPairing(): Promise<Pairing | null> {
  const raw = await getSecret(PAIRING_KEY);
  pairing = raw ? (JSON.parse(raw) as Pairing) : null;
  active = pairing?.endpoint ?? null;
  const t = await getSecret(TOKENS_KEY);
  tokens = t ? (JSON.parse(t) as Tokens) : null;
  setConnection({ paired: !!pairing, label: pairing ? clusterLabel(pairing.endpoint) : null });
  return pairing;
}

export function getPairing(): Pairing | null {
  return pairing;
}

async function savePairing(p: Pairing | null): Promise<void> {
  pairing = p;
  if (p) await setSecret(PAIRING_KEY, JSON.stringify(p));
  else await deleteSecret(PAIRING_KEY);
  setConnection({ paired: !!p, label: p ? clusterLabel(p.endpoint) : null });
}

async function saveTokens(t: Tokens | null): Promise<void> {
  tokens = t;
  if (t) await setSecret(TOKENS_KEY, JSON.stringify(t));
  else await deleteSecret(TOKENS_KEY);
}

/** Only used for a verified kind:"transport" push: Brain moved to a new relay URL. */
export async function updateEndpoint(endpoint: string): Promise<void> {
  if (!pairing) return;
  await savePairing({ ...pairing, endpoint });
  active = endpoint;
  await saveTokens(null); // challenge must be re-signed for the new Host
}

/** Pull the current relay list over the authenticated session and keep it for failover. */
export async function refreshEndpoints(): Promise<string[]> {
  const r = await authed<{ endpoints: string[] }>('GET', '/v1/endpoints');
  if (pairing && r.endpoints.length) {
    const [primary, ...alternates] = r.endpoints;
    if (primary !== pairing.endpoint || JSON.stringify(alternates) !== JSON.stringify(pairing.alternates ?? [])) {
      await savePairing({ ...pairing, endpoint: primary, alternates });
    }
  }
  return r.endpoints;
}

export async function forgetPairing(): Promise<void> {
  await saveTokens(null);
  await savePairing(null);
}

// ---------------------------------------------------------------------------------------------
// Transport

type Method = 'GET' | 'POST' | 'PATCH' | 'DELETE';
type ReqOpts = {
  body?: unknown;
  query?: Record<string, string | number | boolean | undefined | null>;
  token?: string;
  idempotencyKey?: string;
  endpoint?: string;
  raw?: boolean;
  /** Upload a file body as-is instead of JSON. */
  blob?: Blob;
  headers?: Record<string, string>;
  timeoutMs?: number;
};

function buildUrl(base: string, path: string, query?: ReqOpts['query']): string {
  const b = base.replace(/\/+$/, '');
  let url = b + path;
  if (query) {
    const qs = Object.entries(query)
      .filter(([, v]) => v !== undefined && v !== null && v !== '')
      .map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(String(v))}`)
      .join('&');
    if (qs) url += `?${qs}`;
  }
  return url;
}

/** Tries the active relay, then the others. Safe for POSTs: every POST carries an Idempotency-Key that Brain
 *  dedupes, so a request that reached Brain but lost its response is not applied twice. */
async function send(method: Method, path: string, o: ReqOpts = {}): Promise<Response> {
  const list = o.endpoint ? [o.endpoint] : candidates();
  if (!list.length) throw new NotPairedError('Not paired with a Brain');
  let last: unknown = null;
  for (const endpoint of list) {
    try {
      const res = await sendTo(endpoint, method, path, o);
      if (!o.endpoint) setActive(endpoint);
      return res;
    } catch (e) {
      last = e;
    }
  }
  if (getConnection().online !== false) setConnection({ rest: 'unreachable', detail: String(last) });
  throw new NetworkError(`No relay reachable (${list.length} tried): ${String(last)}`);
}

async function sendTo(endpoint: string, method: Method, path: string, o: ReqOpts): Promise<Response> {
  const headers: Record<string, string> = { Accept: 'application/json', ...o.headers };
  if (o.body !== undefined) headers['Content-Type'] = 'application/json';
  if (o.token) headers.Authorization = `Bearer ${o.token}`;
  if (o.idempotencyKey) headers['Idempotency-Key'] = o.idempotencyKey;
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), o.timeoutMs ?? TIMEOUT_MS);
  try {
    return await fetch(buildUrl(endpoint, path, o.query), {
      method,
      headers,
      body: o.blob ?? (o.body === undefined ? undefined : JSON.stringify(o.body)),
      signal: ctrl.signal,
    });
  } finally {
    clearTimeout(timer);
  }
}

async function parse<T>(res: Response): Promise<T> {
  const text = await res.text();
  let body: unknown = null;
  if (text) {
    try {
      body = JSON.parse(text);
    } catch {
      body = text;
    }
  }
  if (!res.ok) {
    const msg =
      body && typeof body === 'object' && 'error' in body ? String((body as { error: unknown }).error) : undefined;
    throw new ApiError(res.status, body, msg ?? `HTTP ${res.status}`);
  }
  return body as T;
}

/** Unauthenticated JSON call (health, pairing, session endpoints). */
async function plain<T>(method: Method, path: string, o: ReqOpts = {}): Promise<T> {
  if (method !== 'GET' && !o.idempotencyKey) o = { ...o, idempotencyKey: uuid() };
  const res = await send(method, path, o);
  const out = await parse<T>(res);
  if (getConnection().rest === 'unreachable') setConnection({ rest: 'ok', detail: null });
  return out;
}

// ---------------------------------------------------------------------------------------------
// Session

function now(): number {
  return Date.now();
}

async function establishSession(): Promise<string> {
  if (!pairing) throw new NotPairedError('Not paired with a Brain');
  const p = pairing;

  // 1. Refresh if we can.
  if (tokens && tokens.refreshExp - SKEW_MS > now()) {
    try {
      const pair = await plain<TokenPair>('POST', '/v1/session/refresh', {
        body: { device_id: p.deviceId, refresh_token: tokens.refresh },
      });
      await storeTokenPair(pair);
      return pair.access_token;
    } catch (e) {
      if (!(e instanceof ApiError && (e.status === 401 || e.status === 403))) throw e;
      await saveTokens(null); // fall through to a full handshake
    }
  }

  // 2. Challenge: Brain proves it holds the pinned key AND that we reached it through this Host.
  const chal = await plain<{ nonce: string; brain_sig: string }>('POST', '/v1/session/challenge', {
    body: { device_id: p.deviceId },
  });
  const host = hostOf(getActiveEndpoint() ?? p.endpoint); // the relay that actually served the challenge
  if (!verifyChallenge(chal.nonce, host, chal.brain_sig, b64urlDecode(p.brainPub))) {
    setConnection({ rest: 'identity_mismatch', detail: `challenge signature did not verify for ${host}` });
    throw new IdentityMismatchError('The endpoint did not prove it is your paired Brain.');
  }

  // 3. Token.
  try {
    const pair = await plain<TokenPair>('POST', '/v1/session/token', {
      body: { device_id: p.deviceId, nonce: chal.nonce, sig: await signToken(chal.nonce) },
    });
    await storeTokenPair(pair);
    setConnection({ rest: 'ok', detail: null });
    return pair.access_token;
  } catch (e) {
    if (e instanceof ApiError && e.status === 401) {
      setConnection({ rest: 'revoked', detail: 'Brain rejected this device' });
      throw new RevokedError('This device is no longer authorised on Brain.');
    }
    const detail = (e instanceof ApiError && (e.body as { detail?: { error?: string; message?: string } })?.detail) || null;
    if (e instanceof ApiError && e.status === 403 && detail?.error === 'pending_approval') {
      setConnection({ detail: 'Waiting for approval on your paired phone' });
      throw new PendingApprovalError(
        'Almost there: approve this device on your already-paired phone (it got a notification), or run `brain devices approve` on the cluster. Then tap Try again.',
      );
    }
    throw e;
  }
}

async function storeTokenPair(pair: TokenPair): Promise<void> {
  const t = now();
  await saveTokens({
    access: pair.access_token,
    accessExp: t + pair.expires_in * 1000,
    refresh: pair.refresh_token,
    refreshExp: t + pair.refresh_expires_in * 1000,
  });
}

/** Returns a valid access token, refreshing or re-handshaking (single-flight) when needed. */
export async function getAccessToken(force = false): Promise<string> {
  if (!force && tokens && tokens.accessExp - SKEW_MS > now()) return tokens.access;
  if (force && tokens) tokens = { ...tokens, accessExp: 0 };
  if (!sessionInflight) {
    sessionInflight = establishSession().finally(() => {
      sessionInflight = null;
    });
  }
  return sessionInflight;
}

async function authedResponse(method: Method, path: string, o: ReqOpts = {}): Promise<Response> {
  if ((method === 'POST' || method === 'PATCH' || method === 'DELETE') && !o.idempotencyKey) {
    o = { ...o, idempotencyKey: uuid() };
  }
  let token = await getAccessToken();
  let res = await send(method, path, { ...o, token });
  if (res.status === 401) {
    token = await getAccessToken(true);
    res = await send(method, path, { ...o, token });
    if (res.status === 401) {
      setConnection({ rest: 'revoked', detail: 'Brain rejected this device' });
      throw new RevokedError('This device is no longer authorised on Brain.');
    }
  }
  const st = getConnection().rest;
  if (st === 'unreachable' || st === 'unknown') setConnection({ rest: 'ok', detail: null });
  return res;
}

async function authed<T>(method: Method, path: string, o: ReqOpts = {}): Promise<T> {
  return parse<T>(await authedResponse(method, path, o));
}

// ---------------------------------------------------------------------------------------------
// Pairing

export type PairResult = { deviceId: string; brainPub: string; endpoint: string };

/**
 * Completes pairing for a payload whose brain_pub the user has pinned (from the QR / pasted JSON).
 * Order: pin → health identity check → /pair/complete → verify brain_sig → persist → session.
 */
export async function completePairing(payload: PairingPayload, deviceName: string): Promise<PairResult> {
  if (payload.exp * 1000 < now()) throw new Error('This pairing code has expired. Run `brain pair` again.');

  const health = await plain<Health>('GET', '/v1/health', { endpoint: payload.endpoint });
  if (health.brain_pub !== payload.brain_pub) {
    setConnection({ rest: 'identity_mismatch', detail: 'health brain_pub differs from the pinned key' });
    throw new IdentityMismatchError('The endpoint answered with a different Brain key than the one you scanned.');
  }

  const { body, transcript, brainPub } = await buildPairComplete(payload, deviceName);
  const res = await plain<{ device_id: string; brain_sig: string }>('POST', '/v1/pair/complete', {
    endpoint: payload.endpoint,
    body,
  });
  if (!verifyPairBrainSig(transcript, res.brain_sig, brainPub)) {
    throw new IdentityMismatchError('Brain signature over the pairing transcript did not verify. Not pairing.');
  }

  await saveTokens(null);
  await savePairing({
    endpoint: payload.endpoint,
    deviceId: res.device_id,
    brainPub: payload.brain_pub,
    pairedAt: now(),
    alternates: payload.alt ?? [],
  });
  active = payload.endpoint;
  setConnection({ paused: health.paused });
  await getAccessToken(true);
  return { deviceId: res.device_id, brainPub: payload.brain_pub, endpoint: payload.endpoint };
}

/** Health for the paired endpoint; flags identity mismatch if the key changed. */
export async function health(endpoint?: string): Promise<Health> {
  const h = await plain<Health>('GET', '/v1/health', { endpoint });
  if (!endpoint && pairing && h.brain_pub !== pairing.brainPub) {
    setConnection({ rest: 'identity_mismatch', detail: 'health brain_pub differs from the pinned key' });
    throw new IdentityMismatchError('The endpoint is serving a different Brain key.');
  }
  setConnection({ paused: !!h.paused });
  return h;
}

// ---------------------------------------------------------------------------------------------
// Resources

export const api = {
  repos: () => authed<Repo[]>('GET', '/v1/repos'),

  threads: (q: { repo_id?: string; state?: string; archived?: 0 | 1 } = { archived: 0 }) =>
    authed<Thread[]>('GET', '/v1/threads', { query: q }),
  thread: (id: string) => authed<Thread>('GET', `/v1/threads/${encodeURIComponent(id)}`),
  /** run: a training run (worktree + its own vault folder). message: optional first message. */
  createThread: (b: {
    repo_id: string;
    title?: string;
    mode: ThreadMode;
    provider: Provider;
    base_ref?: string;
    run?: boolean;
    message?: string;
  }) => authed<Thread>('POST', '/v1/threads', { body: b }),
  /** A new idea from scratch: a fresh repo + vault on the cluster and a thread that starts on the idea. */
  createProject: (b: { name: string; idea: string; provider: Provider }) =>
    authed<{ repo: Repo; thread: Thread }>('POST', '/v1/projects', { body: b }),
  providers: () => authed<ProviderInfo[]>('GET', '/v1/providers'),
  /** Upload a file to the thread's workspace; returns where the agent can read it. */
  attach: async (id: string, file: { uri: string; name: string; mimeType?: string | null; file?: Blob }) => {
    const blob = file.file ?? (await (await fetch(file.uri)).blob());
    return authed<{ path: string; rel: string; name: string; size: number; mime: string }>(
      'POST',
      `/v1/threads/${encodeURIComponent(id)}/attachments`,
      { blob, timeoutMs: 180_000, headers: { 'Content-Type': file.mimeType || blob.type || 'application/octet-stream', 'X-Filename': encodeURIComponent(file.name) } },
    );
  },
  listening: (id: string) => authed<ListeningTest>('GET', `/v1/listening/${encodeURIComponent(id)}`),
  rateListening: (id: string, ratings: Record<string, ItemRating>) =>
    authed<ListenResult>('POST', `/v1/listening/${encodeURIComponent(id)}/ratings`, { body: { ratings } }),
  /** Audio needs auth headers, which <audio src> cannot send: fetch it and play a local blob URL. */
  listeningAudio: async (id: string, path: string) =>
    URL.createObjectURL(
      await (
        await authedResponse('GET', `/v1/listening/${encodeURIComponent(id)}/audio`, { query: { path }, timeoutMs: 60_000 })
      ).blob(),
    ),
  files: (tid: string, root: string, path = '') =>
    authed<FileListing>('GET', `/v1/threads/${encodeURIComponent(tid)}/files`, { query: { root, path } }),
  fileBlob: async (tid: string, root: string, path: string) => {
    const r = await authedResponse('GET', `/v1/threads/${encodeURIComponent(tid)}/file`, { query: { root, path }, timeoutMs: 60_000 });
    if (!r.ok) throw new ApiError(r.status, null, `HTTP ${r.status}`);
    return r.blob();
  },
  skills: (id: string) => authed<SkillInfo[]>('GET', `/v1/threads/${encodeURIComponent(id)}/skills`),
  switchProvider: (id: string, provider: Provider) =>
    authed<Thread>('POST', `/v1/threads/${encodeURIComponent(id)}/provider`, { body: { provider } }),
  patchThread: (id: string, b: { title?: string; pinned?: boolean; archived?: boolean; muted?: boolean }) =>
    authed<Thread>('PATCH', `/v1/threads/${encodeURIComponent(id)}`, { body: b }),
  forkThread: (id: string, b: { at_seq?: number; title: string; mode: ThreadMode }) =>
    authed<Thread>('POST', `/v1/threads/${encodeURIComponent(id)}/fork`, { body: b }),
  sendMessage: (id: string, text: string, idempotencyKey?: string) =>
    authed<{ seq: number }>('POST', `/v1/threads/${encodeURIComponent(id)}/messages`, {
      body: { text },
      idempotencyKey,
    }),
  stopThread: (id: string) => authed<{ ok: boolean; unsent?: string[] }>('POST', `/v1/threads/${encodeURIComponent(id)}/stop`, {}),

  events: (id: string, afterSeq = 0, limit = 200) =>
    authed<{ events: BrainEvent[]; last_seq: number }>('GET', `/v1/threads/${encodeURIComponent(id)}/events`, {
      query: { after_seq: afterSeq, limit },
    }),

  /** Pages through /events until caught up. Returns everything after `afterSeq`. */
  async catchUp(id: string, afterSeq: number, onPage?: (evs: BrainEvent[]) => Promise<void> | void) {
    const all: BrainEvent[] = [];
    let cursor = afterSeq;
    for (let i = 0; i < 50; i++) {
      const page = await api.events(id, cursor, 200);
      if (page.events.length) {
        all.push(...page.events);
        await onPage?.(page.events);
        cursor = page.events[page.events.length - 1].seq;
      }
      if (page.events.length < 200 || cursor >= page.last_seq) break;
    }
    return all;
  },

  pending: (sinceMs?: number | null) =>
    authed<Pending>('GET', sinceMs ? `/v1/pending?since=${Math.floor(sinceMs / 1000)}` : '/v1/pending'),

  /** POST /v1/approvals/{id}/decision with device_sig over "cb-dec-v1" ‖ id ‖ decision. */
  decide: async (id: string, decision: 'allow' | 'deny', note?: string) =>
    authed<Approval>('POST', `/v1/approvals/${encodeURIComponent(id)}/decision`, {
      body: { decision, ...(note ? { note } : {}), device_sig: await signDecision(id, decision) },
    }),

  jobs: () => authed<Job[]>('GET', '/v1/jobs'),
  retryTurn: (id: string) => authed<{ ok: boolean }>('POST', `/v1/threads/${encodeURIComponent(id)}/retry`, {}),
  cancelJob: (id: string) => authed<{ ok: boolean }>('POST', `/v1/jobs/${encodeURIComponent(id)}/cancel`, {}),

  pause: async () => {
    const r = await authed<{ paused: boolean }>('POST', '/v1/pause', {});
    setConnection({ paused: !!r.paused });
    return r;
  },
  resume: async () => {
    const r = await authed<{ paused: boolean }>('POST', '/v1/resume', {});
    setConnection({ paused: !!r.paused });
    return r;
  },

  pushToken: (fcmToken: string) => authed<{ ok: boolean }>('POST', '/v1/devices/push-token', { body: { fcm_token: fcmToken } }),
  /** PWA: Brain's VAPID public key, and this browser's Web Push subscription. */
  webPushKey: () => authed<{ public_key: string }>('GET', '/v1/push/vapid'),
  webPushSubscribe: (subscription: unknown) =>
    authed<{ ok: boolean }>('POST', '/v1/devices/web-push', { body: { subscription } }),

  blobText: async (id: string) => (await authedResponse('GET', `/v1/blobs/${encodeURIComponent(id)}`)).text(),
};

export function describeError(e: unknown): string {
  if (e instanceof IdentityMismatchError) return e.message;
  if (e instanceof RevokedError) return e.message;
  if (e instanceof PendingApprovalError) return e.message;
  if (e instanceof NetworkError) return 'Brain endpoint is unreachable.';
  if (e instanceof NotPairedError) return 'Not paired yet.';
  if (e instanceof ApiError) return `Brain answered ${e.status}: ${e.message}`;
  return e instanceof Error ? e.message : String(e);
}
