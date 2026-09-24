/**
 * Farmhouse Link - Utilidad compartida de shell (Fase 1 del plan de plataforma)
 *
 * Antes de esto, cada página (hub.js, app.js, internal.js, inventory.js, link.js) reimplementaba
 * por su cuenta: el toggle de tema claro/oscuro, el logout, y el llenado de nombre/rol/avatar del
 * usuario en el header — cinco copias casi idénticas. Esto las reemplaza sin renombrar ningún id
 * existente en el HTML de cada página (menor riesgo): cada página sigue pasando SUS PROPIOS ids
 * como parámetros, así que ni el CSS ni nada que ya apunte a esos ids se ve afectado.
 *
 * No es un módulo ES — se carga con un <script> normal, como el resto del proyecto (sin build
 * step), y expone todo bajo `window.FarmhouseShell`.
 */
(function () {
  function readStoredTheme() {
    try {
      return localStorage.getItem('fh_theme') || 'light';
    } catch (e) {
      return 'light'; // Almacenamiento bloqueado (navegación privada, etc.) — no es fatal.
    }
  }

  function applyTheme(theme, { themeIconId, themeLabelSelector, onThemeChange } = {}) {
    document.documentElement.setAttribute('data-theme', theme);
    try {
      localStorage.setItem('fh_theme', theme);
    } catch (e) {
      /* Sin almacenamiento: el tema no persiste entre visitas, pero la página sigue funcionando. */
    }
    const iconSlot = themeIconId ? document.getElementById(themeIconId) : null;
    if (iconSlot) iconSlot.innerHTML = `<i data-lucide="${theme === 'dark' ? 'sun' : 'moon'}"></i>`;
    const labelEl = themeLabelSelector ? document.querySelector(themeLabelSelector) : null;
    if (labelEl) labelEl.textContent = theme === 'dark' ? 'Claro' : 'Oscuro';
    if (window.utils) utils.renderIcons();
    if (typeof onThemeChange === 'function') onThemeChange(theme);
  }

  /**
   * Aplica el tema guardado y engancha el botón de toggle. Se llama una vez al cargar la página.
   * `onThemeChange` es para el caso de Reportes (Farmhouse Link): sus gráficos leen colores del
   * tema y necesitan repintarse cada vez que cambia.
   */
  function initTheme({ toggleId = 'btnThemeToggle', themeIconId = 'themeIconSlot', themeLabelSelector = '#btnThemeToggle .theme-label', onThemeChange } = {}) {
    applyTheme(readStoredTheme(), { themeIconId, themeLabelSelector, onThemeChange });
    const btn = toggleId ? document.getElementById(toggleId) : null;
    btn?.addEventListener('click', () => {
      const current = document.documentElement.getAttribute('data-theme');
      applyTheme(current === 'dark' ? 'light' : 'dark', { themeIconId, themeLabelSelector, onThemeChange });
    });
  }

  /** Llena nombre/rol/avatar del usuario logueado en los ids que le pase cada página. */
  function fillUserHeader({ nameId, roleId, avatarId }, user) {
    const nameEl = nameId ? document.getElementById(nameId) : null;
    const roleEl = roleId ? document.getElementById(roleId) : null;
    const avatarEl = avatarId ? document.getElementById(avatarId) : null;
    if (nameEl) nameEl.textContent = user.name;
    if (roleEl) roleEl.textContent = `${user.role.toUpperCase()}${user.branch ? ' • ' + user.branch.name : ''}`;
    if (avatarEl) avatarEl.textContent = utils.getInitials(user.name);
  }

  /**
   * Engancha el botón de logout. `beforeLogout`/`afterLogout` son opcionales para las páginas con
   * un paso extra (app.js corta websocket/push antes, y no redirige — vuelve a mostrar su propio
   * modal de login; hub.js tampoco redirige, vuelve a su propia pantalla de login).
   */
  function initLogout({ logoutBtnId = 'btnLogout', beforeLogout, afterLogout, redirectTo } = {}) {
    const btn = logoutBtnId ? document.getElementById(logoutBtnId) : null;
    btn?.addEventListener('click', async () => {
      if (typeof beforeLogout === 'function') {
        try {
          await beforeLogout();
        } catch (e) {
          console.warn('[shell] beforeLogout falló:', e);
        }
      }
      await auth.logout();
      if (typeof afterLogout === 'function') {
        afterLogout();
      } else if (redirectTo) {
        window.location.href = redirectTo;
      }
    });
  }

  window.FarmhouseShell = { applyTheme, initTheme, fillUserHeader, initLogout };
})();
