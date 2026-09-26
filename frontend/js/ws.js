/**
 * Farmhouse WhatsApp Center - Cliente de WebSockets en Tiempo Real
 */

const wsClient = {
  socket: null,
  reconnectInterval: 4000,
  // Rechazo por credenciales (código 1008: dispositivo revocado, usuario inactivo): antes se
  // reintentaba cada 5 s para siempre, pidiendo un ticket nuevo en cada intento. Ahora se
  // espacia hasta un minuto, y vuelve a 5 s en cuanto una conexión se abre bien.
  authRetryDelay: 5000,
  maxAuthRetryDelay: 60000,
  pingTimer: null,
  reconnectTimer: null,
  lastPongAt: null,
  listeners: {},
  // connect() espera el ticket antes de crear el socket, y en ese intervalo `this.socket` sigue
  // en null: focus + visibilitychange + pageshow (que llegan juntos al volver a la pestaña), o
  // useDevice() + initApp(), pasaban los dos el chequeo y abrían DOS sockets — cada evento
  // llegaba duplicado (sonidos, contadores). `connecting` cierra esa ventana; `generation`
  // descarta un connect() que quedó esperando si mientras tanto se llamó disconnect().
  connecting: false,
  connectingGeneration: -1,
  generation: 0,

  async connect() {
    // Un connect() anterior que sigue esperando su ticket solo bloquea si es de la misma
    // generación: después de disconnect() (useDevice hace disconnect + connect) el pendiente
    // ya quedó descartado y este tiene que poder arrancar.
    if (this.connecting && this.connectingGeneration === this.generation) return;
    if (this.socket && (this.socket.readyState === WebSocket.OPEN || this.socket.readyState === WebSocket.CONNECTING)) {
      return;
    }
    const generation = this.generation;
    this.connecting = true;
    this.connectingGeneration = generation;
    try {
      await this._openSocket(generation);
    } finally {
      if (this.connectingGeneration === generation) this.connecting = false;
    }
  },

  scheduleReconnect(delay) {
    clearTimeout(this.reconnectTimer);
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      if (typeof auth !== 'undefined' && auth.isAuthenticated()) this.connect();
    }, delay);
  },

  async _openSocket(generation) {
    let token = null;

    // Intentar obtener un ticket de un solo uso para no exponer JWTs en query params (Punto 14)
    try {
      if (typeof api !== 'undefined' && api.request) {
        // Con tope de 10 s: mientras esta petición no termina, `connecting` bloquea cualquier
        // otro intento; si se colgaba (celular volviendo de segundo plano) nunca reconectaba.
        const controller = typeof AbortController !== 'undefined' ? new AbortController() : null;
        const timer = controller ? setTimeout(() => controller.abort(), 10000) : null;
        let ticketRes;
        try {
          ticketRes = await api.request('/auth/ws-token', { method: 'POST', signal: controller?.signal });
        } finally {
          clearTimeout(timer);
        }
        if (ticketRes && ticketRes.ws_ticket) {
          token = ticketRes.ws_ticket;
        }
      }
    } catch (e) {
      console.warn('[WS] No se pudo obtener ws_ticket efímero, usando token de respaldo:', e);
    }

    // Se llamó disconnect() (logout, cambio de dispositivo) mientras se esperaba el ticket.
    if (generation !== this.generation) return;

    if (!token && typeof auth !== 'undefined') {
      token = auth.getWsToken();
    }

    if (!token) {
      console.warn('[WS] No hay credenciales de autenticación para WebSocket.');
      // Sin ticket (red caída, petición abortada) se reintenta más tarde en vez de quedarse sin
      // tiempo real; scheduleReconnect no hace nada si la sesión ya se cerró.
      this.scheduleReconnect(this.reconnectInterval);
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
    this.updateStatus('connecting');
    let socket;
    try {
      socket = new WebSocket(wsUrl);
    } catch (err) {
      console.error('[WS] Error instanciando WebSocket:', err);
      this.updateStatus('disconnected');
      this.scheduleReconnect(this.reconnectInterval);
      return;
    }
    this.socket = socket;

    // Cada manejador ignora los eventos de un socket que ya no es el actual: sin esto, el
    // onclose de un socket reemplazado apagaba el ping del vigente y mostraba "Desconectado"
    // aunque la conexión real siguiera viva.
    socket.onopen = () => {
      if (this.socket !== socket) return;
      console.log('[WS] Conexión WebSocket establecida.');
      this.lastPongAt = Date.now();
      this.authRetryDelay = 5000;
      this.updateStatus('connected');
      this.startPing();
      this.emit('connected');
    };

    socket.onmessage = (event) => {
      if (this.socket !== socket) return;
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

    socket.onerror = (err) => {
      console.error('[WS] Error de WebSocket:', err);
    };

    socket.onclose = (event) => {
      if (this.socket !== socket) return;
      console.log(`[WS] Conexión cerrada (código: ${event.code}).`);
      this.socket = null;
      this.stopPing();
      this.updateStatus('disconnected');
      this.emit('disconnected');

      if (event.code === 1008) {
        const delay = this.authRetryDelay;
        this.authRetryDelay = Math.min(this.authRetryDelay * 2, this.maxAuthRetryDelay);
        this.scheduleReconnect(delay);
      } else {
        this.scheduleReconnect(this.reconnectInterval);
      }
    };
  },

  updateStatus(status) {
    const dot = document.getElementById('topLiveDot');
    const text = document.getElementById('topLiveText');
    const badge = document.getElementById('topLiveBadge');
    if (dot && text) {
      dot.className = `status-circle live-dot ${status}`;
      if (status === 'connected') {
        text.textContent = 'En vivo';
        if (badge) badge.title = '🟢 Conexión en tiempo real activa';
      } else if (status === 'connecting') {
        text.textContent = 'Reconectando...';
        if (badge) badge.title = '🟡 Reconectando a tiempo real...';
      } else {
        text.textContent = 'Desconectado';
        if (badge) badge.title = '🔴 Sin conexión en tiempo real (usando sincronización periódica)';
      }
    }
    this.emit('status_changed', status);
  },

  disconnect() {
    this.generation += 1;
    this.stopPing();
    clearTimeout(this.reconnectTimer);
    this.reconnectTimer = null;
    if (this.socket) {
      const socket = this.socket;
      // Se suelta la referencia ANTES de cerrar: así su onclose ve que ya no es el socket
      // actual y no agenda una reconexión propia.
      this.socket = null;
      socket.close();
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
