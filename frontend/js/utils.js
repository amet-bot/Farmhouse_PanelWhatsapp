/**
 * Farmhouse WhatsApp Center - Utilidades y Funciones de Seguridad
 */

const utils = {
  /**
   * Escapa caracteres especiales HTML para prevenir ataques XSS almacenados y reflejados.
   * @param {string|any} str Texto a escapar
   * @returns {string} Texto seguro para inserción en el DOM
   */
  escapeHtml(str) {
    if (str === null || str === undefined) return '';
    const s = String(str);
    const entityMap = {
      '&': '&amp;',
      '<': '&lt;',
      '>': '&gt;',
      '"': '&quot;',
      "'": '&#39;',
      '/': '&#x2F;',
      '`': '&#x60;',
      '=': '&#x3D;'
    };
    return s.replace(/[&<>"'`=\/]/g, char => entityMap[char] || char);
  },

  /**
   * Formatea una fecha/hora ISO en formato legible (ej: 10:45 AM)
   */
  formatTime(isoString) {
    if (!isoString) return '';
    try {
      const date = new Date(isoString);
      return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    } catch (e) {
      return '';
    }
  },

  /**
   * Formatea una fecha completa (ej: 28 Ago, 10:45 AM)
   */
  formatDateTime(isoString) {
    if (!isoString) return '';
    try {
      const date = new Date(isoString);
      return date.toLocaleDateString('es-PA', {
        month: 'short',
        day: 'numeric',
        hour: '2-digit',
        minute: '2-digit'
      });
    } catch (e) {
      return '';
    }
  },

  /**
   * Genera las iniciales de un nombre (ej: "Juan Pérez" -> "JP")
   */
  getInitials(name) {
    if (!name) return '??';
    return name
      .trim()
      .split(/\s+/)
      .map(part => part[0])
      .join('')
      .substring(0, 2)
      .toUpperCase();
  },

  /**
   * Genera un color estable a partir de un nombre, para diferenciar avatares por contacto.
   */
  getAvatarColor(name) {
    const palette = ['#16a34a', '#2563eb', '#9333ea', '#ea580c', '#0891b2', '#db2777', '#65a30d', '#7c3aed'];
    if (!name) return palette[0];
    let hash = 0;
    for (let i = 0; i < name.length; i++) {
      hash = name.charCodeAt(i) + ((hash << 5) - hash);
    }
    return palette[Math.abs(hash) % palette.length];
  },

  /**
   * Renderiza todos los íconos Lucide presentes en el DOM de forma segura.
   */
  renderIcons() {
    if (window.lucide && typeof window.lucide.createIcons === 'function') {
      window.lucide.createIcons();
    }
  },

  /**
   * Muestra una notificación Toast flotante en pantalla con íconos SVG consistentes
   */
  showToast(message, type = 'info') {
    const banner = document.getElementById('toastNotification');
    const msgElem = document.getElementById('toastMessage');
    const iconSlot = document.getElementById('toastIconSlot');
    if (!banner || !msgElem) return;

    msgElem.textContent = message;
    if (iconSlot) {
      let iconName = 'info';
      if (type === 'success') iconName = 'check-circle';
      else if (type === 'error') iconName = 'alert-circle';
      else if (type === 'warning') iconName = 'alert-triangle';
      iconSlot.innerHTML = `<i data-lucide="${iconName}"></i>`;
      this.renderIcons();
    }

    banner.className = `toast-banner toast-${type} active`;
    clearTimeout(this._toastTimeout);
    this._toastTimeout = setTimeout(() => {
      banner.classList.remove('active');
    }, 4000);
  }
};
