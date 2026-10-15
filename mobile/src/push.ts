/**
 * FCM push (expo-notifications). Brain sends FCM data+notification messages with
 * data = {v:"1", kind, thread_id?, approval_id?, job_id?, endpoint?, endpoint_sig?} — never a token.
 *
 * - Creates the six Android channels from API.md.
 * - Gets the NATIVE device token (getDevicePushTokenAsync → FCM registration token on Android) and
 *   POSTs it to /v1/devices/push-token; re-posts when the token rotates.
 * - Routes notification taps to the approval or thread.
 * - kind:"transport" updates the stored endpoint only if endpoint_sig verifies against the pinned key.
 */
import { useEffect } from 'react';
import { Platform } from 'react-native';
import * as Device from 'expo-device';
import * as Notifications from 'expo-notifications';
import { router } from 'expo-router';
import { api, getPairing, updateEndpoint } from './api';
import { b64urlDecode } from './b64';
import { verifyEndpointSig } from './crypto';
import type { PushData } from './types';

export const CHANNELS: { id: string; name: string; importance: Notifications.AndroidImportance; sound: boolean }[] = [
  { id: 'needs_you', name: 'Needs you', importance: Notifications.AndroidImportance.HIGH, sound: true },
  { id: 'failed', name: 'Failed', importance: Notifications.AndroidImportance.HIGH, sound: true },
  { id: 'done', name: 'Done', importance: Notifications.AndroidImportance.DEFAULT, sound: true },
  { id: 'budget', name: 'Budget', importance: Notifications.AndroidImportance.DEFAULT, sound: true },
  { id: 'changed', name: 'Changed', importance: Notifications.AndroidImportance.LOW, sound: false },
  { id: 'transport', name: 'Connection', importance: Notifications.AndroidImportance.MIN, sound: false },
];

Notifications.setNotificationHandler({
  handleNotification: async (n) => {
    const data = dataOf(n.request);
    const silent = data?.kind === 'transport' || data?.kind === 'changed';
    if (data?.kind === 'transport') void handleTransport(data);
    return {
      shouldShowBanner: !silent,
      shouldShowList: data?.kind !== 'transport',
      shouldPlaySound: data?.kind === 'approval' || data?.kind === 'failed',
      shouldSetBadge: false,
    };
  },
});

export async function ensureChannels(): Promise<void> {
  if (Platform.OS !== 'android') return;
  for (const c of CHANNELS) {
    await Notifications.setNotificationChannelAsync(c.id, {
      name: c.name,
      importance: c.importance,
      sound: c.sound ? 'default' : null,
      enableVibrate: c.sound,
      showBadge: c.id === 'needs_you' || c.id === 'failed',
    });
  }
}

export type PushSetup = { status: 'ready' | 'denied' | 'unsupported' | 'error'; detail?: string };

let tokenSub: { remove(): void } | null = null;

/** Ask permission, fetch the native token, and register it with Brain. Safe to call repeatedly. */
export async function registerForPush(prompt = true): Promise<PushSetup> {
  try {
    await ensureChannels();
    if (!Device.isDevice) return { status: 'unsupported', detail: 'Push needs a physical device.' };
    let perm = await Notifications.getPermissionsAsync();
    if (!perm.granted && prompt) perm = await Notifications.requestPermissionsAsync();
    if (!perm.granted) return { status: 'denied', detail: 'Notifications are off for this app.' };

    const tok = await Notifications.getDevicePushTokenAsync();
    // Android: FCM registration token. iOS: APNs token (Brain's FCM sender cannot use it as-is).
    await api.pushToken(String(tok.data));

    if (!tokenSub) {
      tokenSub = Notifications.addPushTokenListener((t) => {
        api.pushToken(String(t.data)).catch(() => undefined);
      });
    }
    return { status: 'ready', detail: Platform.OS === 'ios' ? 'APNs token registered' : 'FCM token registered' };
  } catch (e) {
    return { status: 'error', detail: e instanceof Error ? e.message : String(e) };
  }
}

function dataOf(req: Notifications.NotificationRequest): PushData | null {
  const fromContent = req.content.data as Record<string, unknown> | undefined;
  // FCM data may also surface on the remote-message trigger on Android.
  const trig = req.trigger as { remoteMessage?: { data?: Record<string, unknown> } } | null;
  const d = (fromContent && Object.keys(fromContent).length ? fromContent : trig?.remoteMessage?.data) ?? null;
  if (!d || typeof d.kind !== 'string') return null;
  return d as unknown as PushData;
}

async function handleTransport(d: PushData): Promise<void> {
  const p = getPairing();
  if (!p || !d.endpoint || !d.endpoint_sig || !d.ts) return;
  if (verifyEndpointSig(d.endpoint, String(d.ts), p.deviceId, d.endpoint_sig, b64urlDecode(p.brainPub))) {
    await updateEndpoint(d.endpoint);
  }
}

export function routeForPush(d: PushData | null): string | null {
  if (!d) return null;
  if (d.approval_id) return `/approval/${encodeURIComponent(d.approval_id)}`;
  if (d.listen_id) return `/listen/${encodeURIComponent(d.listen_id)}`;
  if (d.thread_id) return `/thread/${encodeURIComponent(d.thread_id)}`;
  if (d.job_id) return '/runs';
  return '/';
}

/** Mount once in the root layout: handles cold-start taps and taps while running. */
export function useNotificationRouting(enabled: boolean): void {
  useEffect(() => {
    if (!enabled) return;
    let handled: string | null = null;
    const go = (resp: Notifications.NotificationResponse | null) => {
      if (!resp) return;
      const id = resp.notification.request.identifier;
      if (handled === id) return;
      handled = id;
      const data = dataOf(resp.notification.request);
      if (data?.kind === 'transport') {
        void handleTransport(data);
        return;
      }
      const href = routeForPush(data);
      if (href) router.push(href as never);
    };
    go(Notifications.getLastNotificationResponse());
    const sub = Notifications.addNotificationResponseReceivedListener(go);
    return () => sub.remove();
  }, [enabled]);
}
