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
    let url = `${this.baseUrl}/api/media/${cleanPath}`;
    const token = typeof auth !== 'undefined' ? auth.getToken() : null;
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

        const errMsg = errData.detail || 'Ocurrió un error en el servidor.';

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
