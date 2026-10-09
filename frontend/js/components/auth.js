/**
 * Farmhouse WhatsApp Center - Módulo de Autenticación
 * Sesión basada en Cookies HttpOnly Seguras (Punto 3)
 */

const auth = {
  _currentUser: null,
  _wsToken: null,

  getUser() {
    return this._currentUser;
  },

  getWsToken() {
    return this._wsToken;
  },

  isAuthenticated() {
    return this._currentUser !== null;
  },

  async checkSession() {
    try {
      const user = await api.get('/auth/me');
      this._currentUser = user;
      try {
        const wsTokenRes = await api.get('/auth/ws-token');
        this._wsToken = wsTokenRes.access_token;
      } catch (wsErr) {
        console.warn('[Auth] No se pudo obtener el token de WebSocket al restaurar sesión:', wsErr);
      }
      return user;
    } catch (e) {
      this._currentUser = null;
      this._wsToken = null;
      return null;
    }
  },

  async login(username, password) {
    const data = await api.post('/auth/login', {
      username: username.trim().toLowerCase(),
      password: password.trim()
    });

    this._currentUser = data.user;
    this._wsToken = data.access_token; // Mantenido solo en memoria para el WebSocket
    return data;
  },

  async logout() {
    try {
      await api.post('/auth/logout', {});
    } catch (e) {
      console.warn('Error en logout:', e);
    }
    this._currentUser = null;
    this._wsToken = null;
  }
};
