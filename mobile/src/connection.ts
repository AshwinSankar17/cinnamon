/**
 * Tiny observable store for "how connected are we, honestly". Fed by api.ts (REST outcomes),
 * ws.ts (socket state) and expo-network (device connectivity). Read with useConnection().
 */
import { useSyncExternalStore } from 'react';
import * as Network from 'expo-network';

export type RestState = 'unknown' | 'ok' | 'unreachable' | 'identity_mismatch' | 'revoked';
export type WsState = 'closed' | 'connecting' | 'open';

export type ConnectionSnapshot = {
  paired: boolean;
  label: string | null; // short cluster label derived from the endpoint host
  online: boolean | null; // device network; null = unknown
  rest: RestState;
  ws: WsState;
  paused: boolean;
  detail: string | null;
};

/** The single state the banner shows, in priority order. */
export type ConnectionStatus =
  | 'unpaired'
  | 'offline'
  | 'identity_mismatch'
  | 'revoked'
  | 'unreachable'
  | 'paused'
  | 'live'
  | 'connecting';

let snap: ConnectionSnapshot = {
  paired: false,
  label: null,
  online: null,
  rest: 'unknown',
  ws: 'closed',
  paused: false,
  detail: null,
};
const listeners = new Set<() => void>();

export function getConnection(): ConnectionSnapshot {
  return snap;
}

export function setConnection(patch: Partial<ConnectionSnapshot>): void {
  let changed = false;
  for (const k of Object.keys(patch) as (keyof ConnectionSnapshot)[]) {
    if (snap[k] !== patch[k]) changed = true;
  }
  if (!changed) return;
  snap = { ...snap, ...patch };
  listeners.forEach((l) => l());
}

export function subscribeConnection(l: () => void): () => void {
  listeners.add(l);
  return () => listeners.delete(l);
}

export function useConnection(): ConnectionSnapshot {
  return useSyncExternalStore(subscribeConnection, getConnection, getConnection);
}

export function statusOf(s: ConnectionSnapshot): ConnectionStatus {
  if (!s.paired) return 'unpaired';
  if (s.online === false) return 'offline';
  if (s.rest === 'identity_mismatch') return 'identity_mismatch';
  if (s.rest === 'revoked') return 'revoked';
  if (s.rest === 'unreachable') return 'unreachable';
  if (s.paused) return 'paused';
  if (s.ws === 'open') return 'live';
  return 'connecting';
}

let netSub: { remove(): void } | null = null;

/** Starts tracking device connectivity. Idempotent. */
export function startNetworkTracking(): void {
  if (netSub) return;
  Network.getNetworkStateAsync()
    .then((st) => setConnection({ online: st.isConnected ?? null }))
    .catch(() => undefined);
  netSub = Network.addNetworkStateListener((st) => {
    setConnection({ online: st.isConnected ?? null });
  });
}
