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
    const token = typeof auth !== 'undefined' ? auth.getWsToken() : null;
    if (token) {
      url += (url.includes('?') ? '&' : '?') + `token=${encodeURIComponent(token)}`;
    }
    return url;
  },


  getDeviceId() {
    return localStorage.getItem('fh_device_id') || '';
  },

  setDeviceId(id) {
    if (id) {
      localStorage.setItem('fh_device_id', id);
    } else {
      localStorage.removeItem('fh_device_id');
    }
  },

  /**
   * Convierte el campo `detail` de un error HTTP en un mensaje legible.
   * FastAPI/Pydantic devuelven `detail` como string en errores de negocio (400/404),
   * pero como un arreglo de objetos {loc, msg, type} en errores de validación (422),
   * lo que antes se mostraba al usuario como "[object Object]".
   */
  parseErrorDetail(detail) {
    if (!detail) return 'Ocurrió un error en el servidor.';
    if (typeof detail === 'string') return detail;
    if (Array.isArray(detail)) {
      return detail
        .map(e => (e && typeof e === 'object' && e.msg) ? e.msg : String(e))
        .join(' ');
    }
    return 'Ocurrió un error en el servidor.';
  },

  async request(endpoint, options = {}) {
    const url = `${this.baseUrl}${endpoint}`;
    const headers = {
      'Content-Type': 'application/json',
      'X-Requested-With': 'XMLHttpRequest', // Protección CSRF (Punto 3)
      ...(options.headers || {})
    };

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
          errData = { detail: `Error HTTP ${response.status}: ${response.statusText}` };
        }

        const errMsg = this.parseErrorDetail(errData.detail);

        if (response.status === 401) {
          window.dispatchEvent(new CustomEvent('auth:unauthorized', { detail: errMsg }));
        } else if (response.status === 403) {
          if (errMsg.toLowerCase().includes('dispositivo') || errMsg.toLowerCase().includes('equipo') || errMsg.toLowerCase().includes('sucursal')) {
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

  delete(endpoint) {
    return this.request(endpoint, { method: 'DELETE' });
  }
};
