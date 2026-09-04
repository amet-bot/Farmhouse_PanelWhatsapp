/**
 * Farmhouse WhatsApp Center - Cliente de WebSockets en Tiempo Real
 */

const wsClient = {
  socket: null,
  reconnectInterval: 4000,
  pingTimer: null,
  lastPongAt: null,
  listeners: {},

  async connect() {
    if (this.socket && (this.socket.readyState === WebSocket.OPEN || this.socket.readyState === WebSocket.CONNECTING)) {
      return;
    }

    let token = null;
    let isTicket = false;

    // Intentar obtener un ticket de un solo uso para no exponer JWTs en query params (Punto 14)
    try {
      if (typeof api !== 'undefined' && api.request) {
        const ticketRes = await api.request('/auth/ws-token', { method: 'POST' });
        if (ticketRes && ticketRes.ws_ticket) {
          token = ticketRes.ws_ticket;
          isTicket = true;
        }
      }
    } catch (e) {
      console.warn('[WS] No se pudo obtener ws_ticket efímero, usando token de respaldo:', e);
    }

    if (!token && typeof auth !== 'undefined') {
      token = auth.getWsToken();
    }

    if (!token) {
      console.warn('[WS] No hay credenciales de autenticación para WebSocket.');
      return;
    }

    const deviceId = api.getDeviceId();
    const isDevServer = window.location.protocol === 'file:' || ['5500', '3000', '5173', '8080'].includes(window.location.port);
    const wsHost = isDevServer ? '127.0.0.1:8000' : window.location.host;
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    let wsUrl = `${protocol}//${wsHost}/ws?token=${encodeURIComponent(token)}`;
    if (deviceId) {
      wsUrl += `&device_id=${encodeURIComponent(deviceId)}`;
    }

    console.log('[WS] Conectando a sala en tiempo real...');
    this.socket = new WebSocket(wsUrl);


    this.socket.onopen = () => {
      console.log('[WS] Conexión WebSocket establecida.');
      this.lastPongAt = Date.now();
      this.startPing();
      this.emit('connected');
    };

    this.socket.onmessage = (event) => {
      if (event.data === 'pong') {
        this.lastPongAt = Date.now();
        return;
      }
      try {
        const data = JSON.parse(event.data);
        this.emit('message', data);
        if (data.type) {
          this.emit(data.type, data);
        }
      } catch (err) {
        console.error('[WS] Error parseando mensaje:', err);
      }
    };

    this.socket.onerror = (err) => {
      console.error('[WS] Error de WebSocket:', err);
    };

    this.socket.onclose = (event) => {
      console.log(`[WS] Conexión cerrada (código: ${event.code}).`);
      this.stopPing();
      this.emit('disconnected');

      // 1008 = Policy Violation (Dispositivo no autorizado o token inválido)
      if (event.code !== 1008 && auth.isAuthenticated()) {
        setTimeout(() => this.connect(), this.reconnectInterval);
      }
    };
  },

  disconnect() {
    this.stopPing();
    if (this.socket) {
      this.socket.close();
      this.socket = null;
    }
  },

  startPing() {
    this.stopPing();
    this.pingTimer = setInterval(() => {
      if (!this.socket || this.socket.readyState !== WebSocket.OPEN) return;

      // Detección de socket "zombi" (Punto móvil): al volver de segundo plano en un
      // celular, el sistema operativo suele cortar la conexión TCP subyacente sin avisar
      // a la pestaña — el navegador sigue reportando readyState === OPEN aunque el socket
      // ya no reciba nada. Si hace más de dos ciclos de ping que no llega un "pong", se
      // asume muerto y se fuerza una reconexión en vez de esperar indefinidamente.
      if (this.lastPongAt && Date.now() - this.lastPongAt > 55000) {
        console.warn('[WS] Sin respuesta "pong" reciente: la conexión parece muerta (zombi). Forzando reconexión.');
        this.disconnect();
        this.connect();
        return;
      }
      this.socket.send('ping');
    }, 25000);
  },

  stopPing() {
    if (this.pingTimer) {
      clearInterval(this.pingTimer);
      this.pingTimer = null;
    }
  },

  // Se llama al volver la pestaña a primer plano, recuperar el foco o recuperar red
  // (Punto móvil): en celular es habitual que el sistema operativo cierre el socket
  // mientras la app está en segundo plano (cambio de app, pantalla bloqueada, cambio
  // de WiFi a datos móviles) sin disparar 'onclose' de inmediato. Sin esta verificación
  // activa, el usuario no recibe mensajes nuevos hasta refrescar o volver a iniciar
  // sesión manualmente.
  ensureConnected() {
    if (typeof auth === 'undefined' || !auth.isAuthenticated()) return;
    if (this.socket && (this.socket.readyState === WebSocket.OPEN || this.socket.readyState === WebSocket.CONNECTING)) {
      return;
    }
    console.log('[WS] Verificación de reconexión (visibilidad/foco/red): reconectando...');
    this.connect();
  },

  on(event, callback) {
    if (!this.listeners[event]) this.listeners[event] = [];
    this.listeners[event].push(callback);
  },

  off(event, callback) {
    if (!this.listeners[event]) return;
    this.listeners[event] = this.listeners[event].filter(cb => cb !== callback);
  },

  emit(event, data) {
    if (!this.listeners[event]) return;
    this.listeners[event].forEach(cb => {
      try { cb(data); } catch (e) { console.error('[WS Listener Error]', e); }
    });
  }
};

// Reconexión activa al volver del segundo plano (Punto móvil, ver ensureConnected arriba).
document.addEventListener('visibilitychange', () => {
  if (document.visibilityState === 'visible') {
    wsClient.ensureConnected();
  }
});
window.addEventListener('pageshow', () => wsClient.ensureConnected());
window.addEventListener('online', () => wsClient.ensureConnected());
window.addEventListener('focus', () => wsClient.ensureConnected());
