import { useCallback, useEffect, useRef, useState } from 'react';
import { AppState } from 'react-native';
import { useFocusEffect } from 'expo-router';
import { describeError } from './api';
import { brainSocket } from './ws';

/**
 * Refetch while focused whenever anything changes on Brain (the socket's `subscribe_all`), at most once per `ms`.
 * A burst of events (a turn streaming tools) becomes one reload.
 */
export function useLiveReload(reload: () => void, enabled = true, ms = 1500) {
  useFocusEffect(
    useCallback(() => {
      if (!enabled) return;
      let timer: ReturnType<typeof setTimeout> | null = null;
      const off = brainSocket.onChange(() => {
        if (!timer) timer = setTimeout(() => { timer = null; reload(); }, ms);
      });
      return () => { off(); if (timer) clearTimeout(timer); };
    }, [reload, enabled, ms]),
  );
}

/**
 * Fetch on focus, optional polling while focused + foregrounded, manual reload for pull-to-refresh. `live` also
 * refetches on any change Brain announces; the poll is then only a fallback for a socket that is down.
 */
export function useRemote<T>(fetcher: () => Promise<T>, opts: { pollMs?: number; initial?: T | null; live?: boolean } = {}) {
  const [data, setData] = useState<T | null>(opts.initial ?? null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const fetchRef = useRef(fetcher);
  fetchRef.current = fetcher;

  const reload = useCallback(async () => {
    setLoading(true);
    try {
      const d = await fetchRef.current();
      setData(d);
      setError(null);
    } catch (e) {
      setError(describeError(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useFocusEffect(
    useCallback(() => {
      reload();
      if (!opts.pollMs) return;
      const id = setInterval(() => {
        if (AppState.currentState === 'active') reload();
      }, opts.pollMs);
      return () => clearInterval(id);
    }, [reload, opts.pollMs]),
  );
  useLiveReload(reload, !!opts.live);

  return { data, setData, error, loading, reload };
}

/** Re-render every `ms` (for live "working · 1m 48s" clocks). */
export function useTick(ms: number, enabled = true): number {
  const [t, setT] = useState(Date.now());
  useEffect(() => {
    if (!enabled) return;
    const id = setInterval(() => setT(Date.now()), ms);
    return () => clearInterval(id);
  }, [ms, enabled]);
  return t;
}
