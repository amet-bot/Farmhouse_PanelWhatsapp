/**
 * Dentro de la app de Android (Capacitor, carpeta mobile/): notificaciones nativas, tocar un aviso
 * abre su pantalla y el botón "atrás" del teléfono navega dentro del sistema. En el navegador no
 * hace nada: `window.Capacitor` solo existe en la app.
 *
 * La app abre este mismo sistema en vivo, así que este archivo viaja con cada publicación en
 * Railway; lo único que vive en el APK es el canal de avisos, el ícono y los permisos.
 */
(function () {
  const C = window.Capacitor;
  if (!C || typeof C.isNativePlatform !== 'function' || !C.isNativePlatform()) return;
  if (window.top !== window) return;   // una sola vez, no en cada sistema abierto adentro

  document.documentElement.classList.add('fh-native-app');
  const Push = C.Plugins && C.Plugins.PushNotifications;
  const App = C.Plugins && C.Plugins.App;
  // La app solo trae esta marca si se compiló con Firebase (mobile/scripts/prepare-config.js):
  // sin Firebase, pedir el permiso de notificaciones cierra la app.
  const conFirebase = /\bFHPush\b/.test(navigator.userAgent);
  const TOKEN_KEY = 'fh_native_push_token';

  const guardar = (k, v) => { try { v == null ? localStorage.removeItem(k) : localStorage.setItem(k, v); } catch (e) { /* sin almacenamiento */ } };
  const leer = (k) => { try { return localStorage.getItem(k); } catch (e) { return null; } };
  const hasApi = () => typeof api !== 'undefined' && api && typeof api.post === 'function';

  // ---- Botón "atrás" del teléfono ----
  // Primero cierra lo que esté abierto encima (un formulario, el visor de fotos); después vuelve
  // a la pantalla anterior; en el inicio, sale de la app.
  if (App) {
    App.addListener('backButton', ({ canGoBack }) => {
      // Lo que esté abierto encima se cierra como con Escape: así cada pantalla hace lo suyo
      // (preguntar "¿Salir sin guardar?", volver a Operación...). Si nadie lo cerró ni abrió
      // otra cosa, se cierra a mano como antes.
      const ENCIMA = '.modal-backdrop.active, .tablet-modal-overlay:not([hidden]), .mr-sheet:not([hidden]), .mr-done:not([hidden])';
      const antes = document.querySelectorAll(ENCIMA).length;
      if (antes) {
        const abierto = document.querySelector('.modal-backdrop.active');
        document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
        setTimeout(() => {
          if (abierto && abierto.classList.contains('active') && document.querySelectorAll(ENCIMA).length === antes) {
            abierto.classList.remove('active');
          }
        }, 60);
        return;
      }
      const visor = document.querySelector('.inv-photo-viewer:not([hidden]), .inv-camera:not([hidden])');
      if (visor) { visor.hidden = true; return; }
      const enInicio = /^\/(hub\/?)?$/.test(location.pathname);
      if (canGoBack && !enInicio) window.history.back();
      else App.exitApp();
    });
  }

  if (!Push || !conFirebase) return;

  // ---- Aviso que llega con la app abierta ----
  // Android no muestra la notificación del sistema si la app está delante: se muestra una franja
  // arriba, que se toca para ir a lo que avisa.
  function mostrarFranja(titulo, texto, url) {
    const franja = document.createElement('button');
    franja.type = 'button';
    franja.className = 'fh-native-banner';
    franja.innerHTML = '<strong></strong><span></span>';
    franja.querySelector('strong').textContent = titulo || 'Farmhouse Link';
    franja.querySelector('span').textContent = texto || '';
    franja.addEventListener('click', () => { franja.remove(); if (url && url.startsWith('/')) location.href = url; });
    document.body.appendChild(franja);
    setTimeout(() => franja.classList.add('is-in'), 20);
    setTimeout(() => { franja.classList.remove('is-in'); setTimeout(() => franja.remove(), 400); }, 7000);
    if (navigator.vibrate) navigator.vibrate([0, 200, 100, 200]);
  }

  Push.addListener('registration', async ({ value }) => {
    if (!hasApi()) return;
    try {
      await api.post('/push/native/register', { token: value, platform: 'android' });
      guardar(TOKEN_KEY, value);
    } catch (e) { /* sin sesión o sin conexión: se reintenta al abrir la próxima pantalla */ }
  });
  Push.addListener('registrationError', (err) => console.warn('[App] No se pudo registrar para avisos:', err));
  Push.addListener('pushNotificationReceived', (n) => {
    mostrarFranja(n.title, n.body, n.data && n.data.url);
  });
  Push.addListener('pushNotificationActionPerformed', ({ notification }) => {
    const url = notification && notification.data && notification.data.url;
    if (url && url.startsWith('/') && url !== location.pathname + location.search) location.href = url;
  });

  // Se activa al abrir cada pantalla con la sesión abierta, apenas se inicia sesión (en el inicio
  // el login no cambia de página) y al volver a la app. Registrarse de nuevo es inofensivo: el
  // servidor actualiza el mismo token.
  let activando = false;
  async function activar() {
    if (!hasApi() || activando) return;
    activando = true;
    try { await api.get('/auth/me'); } catch (e) { activando = false; return; }   // solo con la sesión abierta
    try {
      let permiso = await Push.checkPermissions();
      if (permiso.receive === 'prompt' || permiso.receive === 'prompt-with-rationale') {
        permiso = await Push.requestPermissions();
      }
      if (permiso.receive !== 'granted') return;
      await Push.register();
    } catch (e) {
      console.warn('[App] Avisos no disponibles:', e);
    } finally {
      activando = false;
    }
  }

  if (typeof auth !== 'undefined' && auth && typeof auth.login === 'function') {
    const loginOriginal = auth.login.bind(auth);
    auth.login = async function () {
      const res = await loginOriginal.apply(null, arguments);
      setTimeout(activar, 800);
      return res;
    };
  }
  if (App) {
    App.addListener('resume', () => setTimeout(activar, 800));
  }

  // Al cerrar sesión, ese celular deja de recibir los avisos de ese usuario.
  if (typeof auth !== 'undefined' && auth && typeof auth.logout === 'function') {
    const logoutOriginal = auth.logout.bind(auth);
    auth.logout = async function () {
      const token = leer(TOKEN_KEY);
      if (token && hasApi()) {
        try { await api.post('/push/native/unregister', { token, platform: 'android' }); } catch (e) { /* igual se cierra */ }
      }
      guardar(TOKEN_KEY, null);
      return logoutOriginal();
    };
  }

  window.FarmhouseNative = {
    isApp: true,
    /** Manda una notificación de prueba a este celular. */
    test: () => api.post('/push/native/test', {}),
    activar,
  };

  if (document.readyState === 'complete') setTimeout(activar, 1200);
  else window.addEventListener('load', () => setTimeout(activar, 1200));
})();
