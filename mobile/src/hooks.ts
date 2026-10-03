import { useCallback, useEffect, useRef, useState } from 'react';
import { AppState } from 'react-native';
import { useFocusEffect } from 'expo-router';
import { describeError } from './api';

/**
 * Fetch on focus, optional polling while focused + foregrounded, manual reload for pull-to-refresh.
 * Lists have no WebSocket channel in v0.2 (WS is per-thread), so Inbox/Threads/Runs poll gently.
 */
export function useRemote<T>(fetcher: () => Promise<T>, opts: { pollMs?: number; initial?: T | null } = {}) {
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
