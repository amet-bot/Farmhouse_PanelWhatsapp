/**
 * Farmhouse WhatsApp Center - Service Worker
 * Responsabilidad única: recibir notificaciones Web Push y abrir/enfocar la app al hacer clic.
 * No cachea nada (sin soporte offline intencional) para evitar servir datos de chat desactualizados.
 */

self.addEventListener('install', (event) => {
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(self.clients.claim());
});

self.addEventListener('push', (event) => {
  let payload = { title: 'Farmhouse WhatsApp Center', body: 'Tienes un mensaje nuevo.', url: '/' };
  if (event.data) {
    try {
      payload = { ...payload, ...event.data.json() };
    } catch (e) {
      payload.body = event.data.text() || payload.body;
    }
  }

  const options = {
    body: payload.body,
    icon: '/assets/images/farmhouse-logo.png',
    badge: '/assets/images/farmhouse-logo.png',
    tag: payload.conversation_id ? `fh-conv-${payload.conversation_id}` : undefined,
    renotify: !!payload.conversation_id,
    vibrate: [250, 100, 250, 100, 250],
    requireInteraction: true,
    data: { url: payload.url || '/' },
    actions: [
      { action: 'open', title: 'Abrir chat' }
    ]
  };

  event.waitUntil(self.registration.showNotification(payload.title, options));
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const targetUrl = (event.notification.data && event.notification.data.url) || '/';

  event.waitUntil(
    self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then((clientsArr) => {
      for (const client of clientsArr) {
        if ('focus' in client) {
          client.postMessage({ type: 'push_notification_click', url: targetUrl });
          return client.focus();
        }
      }
      if (self.clients.openWindow) {
        return self.clients.openWindow(targetUrl);
      }
    })
  );
});
