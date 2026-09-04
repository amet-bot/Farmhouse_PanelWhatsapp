/**
 * Farmhouse WhatsApp Center - Notificaciones Push del Navegador
 * Registra el Service Worker y la suscripción Web Push (VAPID) para que los
 * encargados de cada sucursal reciban notificaciones aunque la app esté cerrada
 * o el celular bloqueado (requiere HTTPS o localhost: los navegadores exigen
 * un "contexto seguro" para Service Worker + Push API).
 */

const pushModule = {
  _registration: null,

  isSupported() {
    return 'serviceWorker' in navigator && 'PushManager' in window && window.isSecureContext;
  },

  urlBase64ToUint8Array(base64String) {
    const padding = '='.repeat((4 - (base64String.length % 4)) % 4);
    const base64 = (base64String + padding).replace(/-/g, '+').replace(/_/g, '/');
    const rawData = atob(base64);
    const outputArray = new Uint8Array(rawData.length);
    for (let i = 0; i < rawData.length; i++) {
      outputArray[i] = rawData.charCodeAt(i);
    }
    return outputArray;
  },

  async init() {
    if (!this.isSupported()) {
      console.warn('[Push] Este navegador/contexto no soporta notificaciones push (requiere HTTPS o localhost).');
      return;
    }

    try {
      this._registration = await navigator.serviceWorker.register('/sw.js');
    } catch (err) {
      console.warn('[Push] No se pudo registrar el Service Worker:', err);
      return;
    }

    // Si el usuario ya había concedido el permiso antes, re-sincroniza la suscripción en silencio.
    if (Notification.permission === 'granted') {
      await this.subscribe();
    } else if (Notification.permission === 'default') {
      // Mostrar banner amistoso para invitar a activar notificaciones en celular
      setTimeout(() => this.showMobilePromptBanner(), 2000);
    }
  },

  showMobilePromptBanner() {
    if (document.getElementById('mobilePushPromptBanner')) return;
    if (typeof Notification === 'undefined' || Notification.permission !== 'default') return;

    const banner = document.createElement('div');
    banner.id = 'mobilePushPromptBanner';
    banner.className = 'mobile-push-prompt-banner';
    banner.innerHTML = `
      <div class="prompt-icon"><i data-lucide="bell-ring"></i></div>
      <div class="prompt-content">
        <strong>¿Recibir avisos con la pantalla apagada?</strong>
        <p>Activa las notificaciones push para no perderte ningún pedido o mensaje de WhatsApp.</p>
        <div class="prompt-actions">
          <button class="btn-prompt-activate" id="btnPromptActivatePush">Activar Notificaciones</button>
          <button class="btn-prompt-dismiss" id="btnPromptDismissPush">Más tarde</button>
        </div>
      </div>
    `;

    document.body.appendChild(banner);
    utils.renderIcons();

    document.getElementById('btnPromptActivatePush')?.addEventListener('click', async () => {
      banner.remove();
      const ok = await this.requestPermissionAndSubscribe();
      if (ok && typeof updateNotifBellIcon === 'function') {
        updateNotifBellIcon();
      }
    });

    document.getElementById('btnPromptDismissPush')?.addEventListener('click', () => {
      banner.remove();
    });
  },

  /**
   * Solicita permiso de notificaciones al usuario (debe llamarse desde una interacción,
   * ej. un botón "Activar notificaciones") y crea/registra la suscripción en el backend.
   */
  async requestPermissionAndSubscribe() {
    if (!this.isSupported()) {
      utils.showToast('Tu navegador no soporta notificaciones push en este contexto.', 'warning');
      return false;
    }
    const permission = await Notification.requestPermission();
    if (permission !== 'granted') {
      utils.showToast('No se activaron las notificaciones (permiso denegado).', 'warning');
      return false;
    }
    const subscribed = await this.subscribe();
    if (subscribed) {
      utils.showToast('🔔 Notificaciones push del celular activadas con éxito.', 'success');
      const btn = document.getElementById('btnEnableNotifications');
      if (btn) {
        btn.classList.add('notif-active');
        btn.title = 'Notificaciones push activadas';
      }
      const iconSlot = document.getElementById('notifBellIconSlot');
      if (iconSlot) {
        iconSlot.innerHTML = '<i data-lucide="bell-ring"></i>';
      }
      utils.renderIcons();
    }
    return subscribed;
  },

  async subscribe() {
    if (!this._registration) {
      this._registration = await navigator.serviceWorker.ready;
    }
    try {
      const { public_key } = await api.get('/push/vapid-public-key');

      let subscription = await this._registration.pushManager.getSubscription();
      if (!subscription) {
        subscription = await this._registration.pushManager.subscribe({
          userVisibleOnly: true,
          applicationServerKey: this.urlBase64ToUint8Array(public_key)
        });
      }

      const subJson = subscription.toJSON();
      await api.post('/push/subscribe', {
        endpoint: subJson.endpoint,
        keys: subJson.keys,
        user_agent: navigator.userAgent.substring(0, 250)
      });
      return true;
    } catch (err) {
      console.warn('[Push] No se pudo completar la suscripción push:', err);
      return false;
    }
  },

  async unsubscribe() {
    if (!this._registration) return;
    try {
      const subscription = await this._registration.pushManager.getSubscription();
      if (subscription) {
        await api.post('/push/unsubscribe', { endpoint: subscription.endpoint });
        await subscription.unsubscribe();
      }
    } catch (err) {
      console.warn('[Push] Error al cancelar la suscripción push:', err);
    }
  }
};
