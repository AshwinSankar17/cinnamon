/**
 * PWA push: standard Web Push (VAPID). No Firebase: Brain encrypts each notification for this browser's
 * subscription and posts it to the browser's own push service. Android Chrome delivers it with the PWA closed;
 * iOS only once the PWA is installed to the Home Screen (16.4+).
 *
 * Same exports as push.ts so screens import '@/src/push' on every platform.
 */
import { useEffect } from 'react';
import { router } from 'expo-router';
import { api } from './api';

export type PushSetup = { status: 'ready' | 'denied' | 'unsupported' | 'error'; detail?: string };

function keyBytes(b64url: string): Uint8Array {
  const s = atob(b64url.replace(/-/g, '+').replace(/_/g, '/') + '==='.slice((b64url.length + 3) % 4));
  return Uint8Array.from(s, (c) => c.charCodeAt(0));
}

/** Register the service worker, ask permission, subscribe, and send the subscription to Brain. */
export async function registerForPush(prompt = true): Promise<PushSetup> {
  try {
    if (!('serviceWorker' in navigator) || !('PushManager' in window) || !('Notification' in window)) {
      return {
        status: 'unsupported',
        detail: /iPhone|iPad/.test(navigator.userAgent)
          ? 'On iPhone, add Cluster Brain to the Home Screen first, then open it from there.'
          : 'This browser does not support Web Push.',
      };
    }
    const reg = await navigator.serviceWorker.register('/sw.js', { scope: '/' });
    let perm = Notification.permission;
    if (perm === 'default' && prompt) perm = await Notification.requestPermission();
    if (perm !== 'granted') return { status: 'denied', detail: 'Notifications are off for this site.' };
    const { public_key } = await api.webPushKey();
    let sub = await reg.pushManager.getSubscription();
    const key = keyBytes(public_key);
    const current = sub?.options.applicationServerKey;
    if (sub && current && !sameKey(new Uint8Array(current), key)) {
      await sub.unsubscribe(); // Brain's VAPID key changed
      sub = null;
    }
    sub = sub ?? (await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: key as BufferSource }));
    await api.webPushSubscribe(sub.toJSON());
    return { status: 'ready', detail: 'Web Push subscription registered' };
  } catch (e) {
    return { status: 'error', detail: e instanceof Error ? e.message : String(e) };
  }
}

function sameKey(a: Uint8Array, b: Uint8Array): boolean {
  return a.length === b.length && a.every((x, i) => x === b[i]);
}

/** Notification taps: the service worker focuses this window and posts the route to open. */
export function useNotificationRouting(enabled: boolean): void {
  useEffect(() => {
    if (!enabled || !('serviceWorker' in navigator)) return;
    const onMsg = (ev: MessageEvent) => {
      if (ev.data?.type === 'navigate' && typeof ev.data.url === 'string') router.push(ev.data.url as never);
    };
    navigator.serviceWorker.addEventListener('message', onMsg);
    return () => navigator.serviceWorker.removeEventListener('message', onMsg);
  }, [enabled]);
}
