/**
 * Foreground-only WebSocket to Brain (`/v1/ws?token=<access_token>`).
 *
 * - Connects while the app is active and paired; closes on AppState background (push + REST
 *   catch-up cover the rest).
 * - Each subscription remembers the highest seq it has delivered; on (re)connect it resubscribes
 *   with that after_seq so Brain replays exactly the gap.
 * - Heartbeat: {"op":"ping"} every 30 s; if no message of any kind arrives within 2 intervals the
 *   socket is treated as dead and recycled.
 * - Reconnect: exponential backoff 1 s → 60 s with full jitter (delay = rand(0.5..1) × min(60, 2^n)).
 * - Tokens: a fresh access token is fetched (refresh / handshake via api.ts) before every connect.
 */
import { AppState, type AppStateStatus } from 'react-native';
import { getAccessToken, getActiveEndpoint, getPairing, health } from './api';
import { setConnection } from './connection';
import { upsertEvents } from './store';
import type { BrainEvent } from './types';

type EventHandler = (e: BrainEvent) => void;
type ReplayHandler = (throughSeq: number) => void;

type Sub = { afterSeq: number; handlers: Set<EventHandler>; replay: Set<ReplayHandler> };

const PING_MS = 30_000;
const MAX_BACKOFF_S = 60;

class BrainSocket {
  private ws: WebSocket | null = null;
  private subs = new Map<string, Sub>();
  private globalHandlers = new Set<EventHandler>();
  private attempt = 0;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private pingTimer: ReturnType<typeof setInterval> | null = null;
  private lastRx = 0;
  private wanted = false;
  private appStateSub: { remove(): void } | null = null;
  private appState: AppStateStatus = AppState.currentState;

  /** Begin managing the connection (call once after pairing is known). */
  start(): void {
    if (!this.appStateSub) {
      this.appStateSub = AppState.addEventListener('change', (s) => this.onAppState(s));
    }
    this.wanted = true;
    if (this.appState === 'active') this.connect();
  }

  /** Stop entirely (e.g. on unpair). */
  stop(): void {
    this.wanted = false;
    this.teardown();
    this.appStateSub?.remove();
    this.appStateSub = null;
  }

  /** Subscribe to one thread's live events from `afterSeq`. Returns an unsubscribe function. */
  subscribe(threadId: string, afterSeq: number, onEvent: EventHandler, onReplay?: ReplayHandler): () => void {
    let sub = this.subs.get(threadId);
    if (!sub) {
      sub = { afterSeq, handlers: new Set(), replay: new Set() };
      this.subs.set(threadId, sub);
    } else {
      sub.afterSeq = Math.max(sub.afterSeq, afterSeq);
    }
    sub.handlers.add(onEvent);
    if (onReplay) sub.replay.add(onReplay);
    this.sendSubscribe(threadId, sub.afterSeq);
    return () => {
      const s = this.subs.get(threadId);
      if (!s) return;
      s.handlers.delete(onEvent);
      if (onReplay) s.replay.delete(onReplay);
      if (s.handlers.size === 0) {
        this.subs.delete(threadId);
        this.sendRaw({ op: 'unsubscribe', thread_id: threadId });
      }
    };
  }

  /** Receive every event on any subscribed thread (used to refresh lists). */
  onAnyEvent(h: EventHandler): () => void {
    this.globalHandlers.add(h);
    return () => this.globalHandlers.delete(h);
  }

  get isOpen(): boolean {
    return this.ws?.readyState === WebSocket.OPEN;
  }

  // -------------------------------------------------------------------------------------------

  private onAppState(s: AppStateStatus) {
    this.appState = s;
    if (s === 'background') {
      this.teardown();
    } else if (s === 'active' && this.wanted) {
      this.attempt = 0;
      this.connect();
    }
  }

  private async connect() {
    if (!this.wanted || this.appState !== 'active') return;
    if (this.ws && (this.ws.readyState === WebSocket.OPEN || this.ws.readyState === WebSocket.CONNECTING)) return;
    this.clearReconnect();
    const p = getPairing();
    if (!p) return;
    setConnection({ ws: 'connecting' });
    let token: string;
    try {
      if (this.attempt > 0) await health(); // a reconnect: let REST fail over to a relay that answers first
      token = await getAccessToken();
    } catch {
      this.scheduleReconnect();
      return;
    }
    if (!this.wanted || this.appState !== 'active') return;
    const base = getActiveEndpoint() ?? p.endpoint;
    const url = `${base.replace(/\/+$/, '').replace(/^http/i, 'ws')}/v1/ws?token=${encodeURIComponent(token)}`;
    const ws = new WebSocket(url);
    this.ws = ws;

    ws.onopen = () => {
      if (this.ws !== ws) return;
      this.attempt = 0;
      this.lastRx = Date.now();
      setConnection({ ws: 'open' });
      for (const [threadId, sub] of this.subs) this.sendSubscribe(threadId, sub.afterSeq);
      this.startPing();
    };
    ws.onmessage = (m) => {
      if (this.ws !== ws) return;
      this.lastRx = Date.now();
      let msg: { op?: string; event?: BrainEvent; thread_id?: string; through_seq?: number };
      try {
        msg = JSON.parse(String(m.data));
      } catch {
        return;
      }
      if (msg.op === 'event' && msg.event) this.deliver(msg.event);
      else if (msg.op === 'replay_complete' && msg.thread_id) {
        const sub = this.subs.get(msg.thread_id);
        if (sub && typeof msg.through_seq === 'number') {
          sub.afterSeq = Math.max(sub.afterSeq, msg.through_seq);
          sub.replay.forEach((h) => h(msg.through_seq as number));
        }
      }
      // 'pong' only refreshes lastRx
    };
    ws.onerror = () => {
      /* onclose follows */
    };
    ws.onclose = () => {
      if (this.ws !== ws) return;
      this.ws = null;
      this.stopPing();
      setConnection({ ws: 'closed' });
      this.scheduleReconnect();
    };
  }

  private deliver(e: BrainEvent) {
    const sub = this.subs.get(e.thread_id);
    // Persist first so a screen that remounts sees it; dedupe is by (thread_id, seq).
    upsertEvents([e]).catch(() => undefined);
    if (sub) {
      if (e.seq <= sub.afterSeq) return; // replay overlap
      sub.afterSeq = e.seq;
      sub.handlers.forEach((h) => h(e));
    }
    this.globalHandlers.forEach((h) => h(e));
  }

  private sendSubscribe(threadId: string, afterSeq: number) {
    this.sendRaw({ op: 'subscribe', thread_id: threadId, after_seq: afterSeq });
  }

  private sendRaw(obj: unknown) {
    if (this.ws?.readyState === WebSocket.OPEN) this.ws.send(JSON.stringify(obj));
  }

  private startPing() {
    this.stopPing();
    this.pingTimer = setInterval(() => {
      if (Date.now() - this.lastRx > PING_MS * 2) {
        this.ws?.close();
        return;
      }
      this.sendRaw({ op: 'ping' });
    }, PING_MS);
  }

  private stopPing() {
    if (this.pingTimer) clearInterval(this.pingTimer);
    this.pingTimer = null;
  }

  private scheduleReconnect() {
    if (!this.wanted || this.appState !== 'active') return;
    this.clearReconnect();
    const base = Math.min(MAX_BACKOFF_S, 2 ** this.attempt);
    const delay = base * (0.5 + Math.random() * 0.5) * 1000;
    this.attempt = Math.min(this.attempt + 1, 10);
    this.reconnectTimer = setTimeout(() => this.connect(), delay);
  }

  private clearReconnect() {
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer);
    this.reconnectTimer = null;
  }

  private teardown() {
    this.clearReconnect();
    this.stopPing();
    const ws = this.ws;
    this.ws = null;
    if (ws) {
      ws.onclose = null;
      ws.onmessage = null;
      ws.close();
    }
    setConnection({ ws: 'closed' });
  }
}

export const brainSocket = new BrainSocket();
