/**
 * Farmhouse WhatsApp Center - Cliente de API HTTP
 * Utiliza cookies HttpOnly seguras como método principal de sesión (Punto 3)
 * Incluye encabezado de protección CSRF (X-Requested-With) y X-Device-ID.
 */

const api = {
  baseUrl: (() => {
    if (typeof window !== 'undefined') {
      if (window.location.protocol === 'file:' || ['5500', '3000', '5173', '8080'].includes(window.location.port)) {
        return 'http://127.0.0.1:8000/api';
      }
    }
    return '/api';
  })(),

  mediaBaseUrl: (() => {
    if (typeof window !== 'undefined') {
      if (window.location.protocol === 'file:' || ['5500', '3000', '5173', '8080'].includes(window.location.port)) {
        return 'http://127.0.0.1:8000';
      }
    }
    return '';
  })(),

  resolveMediaUrl(path) {
    if (!path) return '';
    if (path.startsWith('http://') || path.startsWith('https://')) return path;
    let cleanPath = String(path).trim();
    if (cleanPath.startsWith('/api/media/')) {
      cleanPath = cleanPath.replace('/api/media/', '');
    } else if (cleanPath.startsWith('/media/')) {
      cleanPath = cleanPath.replace('/media/', '');
    } else if (cleanPath.startsWith('api/media/')) {
      cleanPath = cleanPath.replace('api/media/', '');
    } else if (cleanPath.startsWith('media/')) {
      cleanPath = cleanPath.replace('media/', '');
    }
    // BUG histórico corregido: `this.baseUrl` YA incluye "/api" (p.ej. "/api" o
    // "http://127.0.0.1:8000/api"), así que anteponerlo aquí producía "/api/api/media/..."
    // (404 siempre). El endpoint de medios vive en <origen>/api/media/..., así que se arma
    // con `mediaBaseUrl` (origen sin "/api") en vez de `baseUrl`.
    let url = `${this.mediaBaseUrl}/api/media/${cleanPath}`;
    // Mismo origen (producción): la cookie HttpOnly de sesión ya viaja sola con <img>/<audio>/
    // descargas, así que el token NO se pone en la URL — antes el JWT de sesión completo (8 h)
    // quedaba en los logs del servidor, el historial del navegador y cualquier enlace copiado.
    // Solo en desarrollo con el frontend en otro puerto (sin cookie) hace falta el ?token=.
    const token = this.mediaBaseUrl && typeof auth !== 'undefined' ? auth.getWsToken() : null;
    if (token) {
      url += (url.includes('?') ? '&' : '?') + `token=${encodeURIComponent(token)}`;
    }
    return url;
  },


  /**
   * Token secreto del equipo vinculado (lo entregó POST /devices/enroll una sola vez). Es lo que
   * viaja en X-Device-ID y en el `device_id` del WebSocket. El código público FH-DEVICE-… ya no
   * autoriza nada: se guarda aparte (fh_device_code) solo para mostrar qué equipo es este.
   */
  getDeviceId() {
    try {
      return localStorage.getItem('fh_device_token') || '';
    } catch (e) {
      return ''; // Almacenamiento bloqueado (navegación privada estricta): sin dispositivo guardado.
    }
  },

  setDeviceId(token) {
    try {
      if (token) {
        localStorage.setItem('fh_device_token', token);
      } else {
        localStorage.removeItem('fh_device_token');
      }
      // El valor viejo (código público) no sirve y confundiría al módulo de dispositivos.
      localStorage.removeItem('fh_device_id');
    } catch (e) {
      /* Sin almacenamiento el dispositivo no persiste entre visitas; la sesión sigue funcionando. */
    }
  },

  getDeviceCode() {
    try { return localStorage.getItem('fh_device_code') || ''; } catch (e) { return ''; }
  },

  setDeviceCode(code) {
    try {
      if (code) localStorage.setItem('fh_device_code', code);
      else localStorage.removeItem('fh_device_code');
    } catch (e) { /* igual que arriba */ }
  },

  /**
   * Convierte el campo `detail` de un error HTTP en un mensaje legible.
   * FastAPI/Pydantic devuelven `detail` como string en errores de negocio (400/404),
   * pero como un arreglo de objetos {loc, msg, type} en errores de validación (422),
   * lo que antes se mostraba al usuario como "[object Object]".
   */
  parseErrorDetail(detail) {
    if (!detail) return 'Algo falló en el servidor. Intenta de nuevo en un momento.';
    if (typeof detail === 'string') {
      // Los mensajes que FastAPI pone solo (en inglés), en palabras.
      const genericos = {
        'Not Found': 'No se encontró lo que buscabas. Puede que ya no exista.',
        'Method Not Allowed': 'Esa acción no está disponible.',
        'Internal Server Error': 'El servidor no respondió bien. Espera un momento e intenta de nuevo.',
        'Not authenticated': 'Tu sesión se cerró. Vuelve a entrar.',
        'Forbidden': 'No tienes permiso para hacer esto.',
      };
      return genericos[detail] || detail;
    }
    if (Array.isArray(detail)) {
      // Errores de validación (422): vienen en inglés ("Field required"). Se dice en palabras
      // qué dato revisar, con el nombre del campo si se puede.
      const campos = [...new Set(detail
        .map((e) => (e && Array.isArray(e.loc) ? e.loc[e.loc.length - 1] : null))
        .filter((c) => typeof c === 'string' && c !== 'body'))];
      const nombre = (c) => this.FIELD_NAMES[c] || c.replace(/_/g, ' ');
      return campos.length
        ? `Revisa ${campos.length === 1 ? 'este dato' : 'estos datos'}: ${campos.map(nombre).join(', ')}. Falta o no es válido.`
        : 'Revisa los datos: hay uno que falta o no es válido.';
    }
    return 'Algo falló en el servidor. Intenta de nuevo en un momento.';
  },

  /** Nombres en palabras de los campos que más aparecen en los errores de validación. */
  FIELD_NAMES: {
    quantity: 'cantidad', counted_quantity: 'cantidad contada', unit_cost: 'costo', price: 'precio',
    branch_id: 'sucursal', inventory_item_id: 'insumo', supplier_id: 'proveedor', reason: 'motivo',
    name: 'nombre', title: 'título', description: 'descripción', due_at: 'vence', items: 'insumos',
    username: 'usuario', password: 'contraseña', email: 'correo', phone: 'teléfono', role: 'rol',
    piece_size: 'peso de una pieza', grams_per_ml: 'gramos por ml', min_qty: 'mínimo', par_qty: 'par',
  },

  async request(endpoint, options = {}) {
    const url = `${this.baseUrl}${endpoint}`;
    const headers = {
      'X-Requested-With': 'XMLHttpRequest', // Protección CSRF (Punto 3)
      ...(options.headers || {})
    };

    // El navegador debe generar automáticamente el boundary de multipart/form-data.
    // Forzar application/json aquí dañaría las subidas de imágenes o documentos.
    const isFormData = typeof FormData !== 'undefined' && options.body instanceof FormData;
    if (!isFormData && !headers['Content-Type']) {
      headers['Content-Type'] = 'application/json';
    }

    const deviceId = this.getDeviceId();
    if (deviceId) {
      headers['X-Device-ID'] = deviceId;
    }

    const config = {
      credentials: 'include', // Envía la cookie HttpOnly access_token automáticamente
      ...options,
      headers
    };

    try {
      const response = await fetch(url, config);

      // Manejo de errores HTTP
      if (!response.ok) {
        let errData = {};
        try {
          errData = await response.json();
        } catch (e) {
          errData = {};
        }

        // Sin un mensaje del servidor, uno que se entienda (antes: "Error HTTP 502: Bad Gateway").
        let errMsg = this.parseErrorDetail(errData.detail);
        if (!errData.detail) {
          if (response.status >= 500) errMsg = 'El servidor no respondió bien. Espera un momento e intenta de nuevo.';
          else if (response.status === 404) errMsg = 'No se encontró lo que buscabas. Puede que ya no exista.';
          else if (response.status === 413) errMsg = 'El archivo es demasiado grande.';
        }

        if (response.status === 401) {
          window.dispatchEvent(new CustomEvent('auth:unauthorized', { detail: errMsg }));
        } else if (response.status === 403) {
          // Solo los rechazos por dispositivo (todos dicen "dispositivo", ver
          // services/device_access.py). Antes también "sucursal": un 403 normal como "No tienes
          // acceso a conversaciones de otra sucursal" (p. ej. justo después de una transferencia)
          // abría el modal de dispositivo no autorizado, y el refresco de cada 6 s lo reabría.
          if (errMsg.toLowerCase().includes('dispositivo')) {
            window.dispatchEvent(new CustomEvent('auth:device_forbidden', { detail: errMsg }));
          }
        } else if (response.status === 429) {
          utils.showToast(`⏳ ${errMsg}`, 'warning');
        }

        throw new Error(errMsg);
      }

      // Si la respuesta no tiene contenido (204 No Content)
      if (response.status === 204) return null;

      const contentType = response.headers.get('content-type') || '';
      if (contentType.includes('application/json')) {
        return await response.json();
      }
      return await response.text();
    } catch (err) {
      console.error(`[API Error] ${options.method || 'GET'} ${url}:`, err);
      // Sin internet o el servidor caído: el navegador da "Failed to fetch" / "Load failed" en
      // inglés. Se dice qué pasa y qué hacer.
      if (err instanceof TypeError) {
        throw new Error('No hay conexión con el sistema. Revisa el internet e intenta de nuevo.');
      }
      throw err;
    }
  },

  get(endpoint) {
    return this.request(endpoint, { method: 'GET' });
  },

  post(endpoint, body) {
    return this.request(endpoint, {
      method: 'POST',
      body: JSON.stringify(body)
    });
  },

  put(endpoint, body) {
    return this.request(endpoint, {
      method: 'PUT',
      body: JSON.stringify(body)
    });
  },

  // Faltaba: varios módulos (reasignar tarea en gestion.js, pausar/reanudar tarea recurrente)
  // ya llamaban a `api.patch(...)` sin que este método existiera — fallaba en silencio con
  // "api.patch is not a function" apenas se usaba.
  patch(endpoint, body) {
    return this.request(endpoint, {
      method: 'PATCH',
      body: JSON.stringify(body)
    });
  },

  delete(endpoint) {
    return this.request(endpoint, { method: 'DELETE' });
  }
};
