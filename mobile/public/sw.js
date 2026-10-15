/* Cluster Brain service worker: shows Web Push notifications and opens the right screen on tap.
   Push payloads are end-to-end encrypted by Brain (RFC 8291); the push service never sees them. */
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', (e) => e.waitUntil(self.clients.claim()));

self.addEventListener('push', (e) => {
  let d = {};
  try {
    d = e.data ? e.data.json() : {};
  } catch (_) {
    d = { title: 'Cluster Brain', body: e.data ? e.data.text() : '' };
  }
  const data = d.data || {};
  e.waitUntil(
    self.registration.showNotification(d.title || 'Cluster Brain', {
      body: d.body || '',
      tag: data.approval_id || data.listen_id || data.thread_id || data.kind || 'brain',
      renotify: true,
      requireInteraction: data.kind === 'approval' || data.kind === 'needs_you',
      icon: '/icon-192.png',
      badge: '/badge-96.png',
      data,
    }),
  );
});

self.addEventListener('notificationclick', (e) => {
  e.notification.close();
  const d = e.notification.data || {};
  const url = d.approval_id
    ? `/approval/${encodeURIComponent(d.approval_id)}`
    : d.listen_id
      ? `/listen/${encodeURIComponent(d.listen_id)}`
      : d.thread_id
      ? `/thread/${encodeURIComponent(d.thread_id)}`
      : d.job_id
        ? '/runs'
        : '/';
  e.waitUntil(
    (async () => {
      const wins = await self.clients.matchAll({ type: 'window', includeUncontrolled: true });
      for (const w of wins) {
        await w.focus();
        w.postMessage({ type: 'navigate', url });
        return;
      }
      await self.clients.openWindow(url);
    })(),
  );
});
