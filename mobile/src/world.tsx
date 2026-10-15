/**
 * The active Cinnamon world for this device: which world, day/night/match phone, companion on/off, showcase.
 * Prefs live in the local store (per device). Only the active world's fonts are loaded.
 * `useLive()` is the one gate every loop checks: nothing moves unless Brain is live, the app is in front,
 * no sheet covers the scene, and the system's reduce-motion setting is off (spec §7.2 motion rules).
 */
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react';
import { AccessibilityInfo, AppState, useColorScheme } from 'react-native';
import * as Font from 'expo-font';
import { statusOf, useConnection } from './connection';
import { getMeta, setMeta } from './store';
import { WORLDS, WORLD_IDS, type Tokens, type World, type WorldId } from './worlds';

export type Mode = 'day' | 'night' | 'auto';

type Ctx = {
  world: World;
  id: WorldId;
  mode: Mode;
  dark: boolean;
  tokens: Tokens;
  fontsReady: boolean;
  pet: boolean;
  showcase: boolean;
  setWorld: (id: WorldId) => void;
  setMode: (m: Mode) => void;
  setPet: (on: boolean) => void;
  setShowcase: (on: boolean) => void;
};

const WorldCtx = createContext<Ctx | null>(null);

export function WorldProvider({ children }: { children: ReactNode }) {
  const scheme = useColorScheme();
  const [id, setId] = useState<WorldId>('bakery');
  const [mode, setModeState] = useState<Mode>('day');
  const [pet, setPetState] = useState(true);
  const [showcase, setShowcaseState] = useState(false);
  const [loaded, setLoaded] = useState<Record<string, boolean>>({});

  useEffect(() => {
    (async () => {
      const [w, m, p, s] = await Promise.all([getMeta('world'), getMeta('world_mode'), getMeta('pet'), getMeta('showcase')]);
      if (w && (WORLD_IDS as readonly string[]).includes(w)) setId(w as WorldId);
      if (m === 'day' || m === 'night' || m === 'auto') setModeState(m);
      if (p === '0') setPetState(false);
      if (s === '1') setShowcaseState(true);
    })().catch(() => undefined);
  }, []);

  useEffect(() => {
    if (loaded[id]) return;
    Font.loadAsync(WORLDS[id].fontFiles)
      .then(() => setLoaded((l) => ({ ...l, [id]: true })))
      .catch(() => setLoaded((l) => ({ ...l, [id]: false })));
  }, [id, loaded]);

  const setWorld = useCallback((w: WorldId) => {
    setId(w);
    setMeta('world', w).catch(() => undefined);
  }, []);
  const setMode = useCallback((m: Mode) => {
    setModeState(m);
    setMeta('world_mode', m).catch(() => undefined);
  }, []);
  const setPet = useCallback((on: boolean) => {
    setPetState(on);
    setMeta('pet', on ? '1' : '0').catch(() => undefined);
  }, []);
  const setShowcase = useCallback((on: boolean) => {
    setShowcaseState(on);
    setMeta('showcase', on ? '1' : '0').catch(() => undefined);
  }, []);

  const dark = mode === 'night' || (mode === 'auto' && scheme === 'dark');
  const world = WORLDS[id];
  const value = useMemo<Ctx>(
    () => ({ world, id, mode, dark, tokens: dark ? world.night : world.day, fontsReady: !!loaded[id], pet, showcase,
             setWorld, setMode, setPet, setShowcase }),
    [world, id, mode, dark, loaded, pet, showcase, setWorld, setMode, setPet, setShowcase],
  );
  return <WorldCtx.Provider value={value}>{children}</WorldCtx.Provider>;
}

export function useWorld(): Ctx {
  const c = useContext(WorldCtx);
  if (!c) throw new Error('useWorld outside WorldProvider');
  return c;
}

/** Font family for a world role, falling back to the app's current fonts until the world's files are loaded. */
export function useWorldFont(role: keyof World['fonts']): string | undefined {
  const { world, fontsReady } = useWorld();
  return fontsReady || role === 'mono' ? world.fonts[role] : undefined;
}

let reduceMotion = false;
AccessibilityInfo.isReduceMotionEnabled?.().then((v) => (reduceMotion = v)).catch(() => undefined);

/** True only when motion is allowed: Brain live, app in front, reduce-motion off, and the caller's own condition. */
export function useLive(extra = true): boolean {
  const conn = useConnection();
  const [active, setActive] = useState(AppState.currentState !== 'background');
  const [reduce, setReduce] = useState(reduceMotion);
  useEffect(() => {
    const a = AppState.addEventListener('change', (s) => setActive(s === 'active'));
    const r = AccessibilityInfo.addEventListener?.('reduceMotionChanged', setReduce);
    return () => {
      a.remove();
      r?.remove();
    };
  }, []);
  return extra && active && !reduce && statusOf(conn) === 'live';
}
