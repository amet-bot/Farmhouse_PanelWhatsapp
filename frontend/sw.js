/**
 * Farmhouse Link - Service Worker
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
  let payload = { title: 'Farmhouse Link', body: 'Tienes un mensaje nuevo.', url: '/' };
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
    // `tag` explícito del servidor (lo usa Comunicación Interna, uno por hilo) o el derivado
    // de la conversación de WhatsApp. Agrupa: el aviso nuevo reemplaza al anterior del mismo
    // chat en vez de apilar diez en la pantalla de bloqueo.
    tag: payload.tag || (payload.conversation_id ? `fh-conv-${payload.conversation_id}` : undefined),
    renotify: !!(payload.tag || payload.conversation_id),
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

  // Se enfoca una pestaña de la MISMA página que el aviso (/app para WhatsApp, /interno para
  // Comunicación Interna). Antes se tomaba la primera pestaña abierta cualquiera: con el hub o
  // Inventario abiertos, el clic enfocaba esa página y no se abría ninguna conversación.
  const targetPath = new URL(targetUrl, self.location.origin).pathname;

  event.waitUntil(
    self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then((clientsArr) => {
      for (const client of clientsArr) {
        if (!('focus' in client)) continue;
        if (new URL(client.url).pathname === targetPath) {
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
