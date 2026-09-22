/**
 * Farmhouse - Panel General (Hub de Sistemas)
 * Login liviano + selector de sistemas. Reutiliza auth.js/api.js tal cual los usa el Centro
 * WhatsApp (misma sesión por cookie), pero sin cargar ninguno de los módulos propios de esa app.
 *
 * Config centralizada de sistemas (SYSTEMS) en vez de repetir nombre/descripción/tags en varios
 * lugares — equivalente aquí, en JS plano sin framework, a lo que en un stack por componentes
 * sería un array de `SystemOption`.
 *
 * Acá vivía una vista previa grande de cada sistema, dibujada con datos inventados (ítems, stock,
 * costos, conversaciones). Se sacó: ocupaba toda la pantalla sin dejar usar nada, prometía un
 * inventario con stock que el backend no tiene, y en celular empujaba el botón de entrar fuera
 * de la vista. Ahora la tarjeta del sistema es el botón de entrar.
 */

const SYSTEMS = [
  {
    id: 'whatsapp',
    name: 'Centro WhatsApp',
    description: 'Conversaciones, pedidos y atención al cliente por WhatsApp.',
    tags: ['Clientes', 'Pedidos', 'Soporte'],
    icon: 'message-square',
    status: 'active',
    route: '/app',
  },
  {
    id: 'contabilidad',
    name: 'Contabilidad',
    description: 'Gastos, ingresos y reportes financieros de Farmhouse.',
    tags: ['Ingresos', 'Gastos', 'Reportes'],
    icon: 'calculator',
    status: 'coming-soon',
  },
  {
    id: 'interno',
    name: 'Comunicación Interna',
    description: 'Mensajería entre el personal de todas las sucursales.',
    tags: ['Equipo', 'Sucursales', 'Mensajes'],
    icon: 'messages-square',
    status: 'active',
    route: '/interno',
  },
  {
    id: 'inventario',
    name: 'Inventario y Abastecimiento',
    description: 'Cargamentos por sucursal. Merma y gasto: próximamente.',
    tags: ['Stock', 'Sucursales', 'Abastecimiento'],
    icon: 'package',
    status: 'active',
    route: '/inventario',
  },
];

document.addEventListener('DOMContentLoaded', async () => {
  const screenLogin = document.getElementById('hubLoginScreen');
  const screenMain = document.getElementById('hubMain');
  const loginForm = document.getElementById('loginForm');
  const btnLoginSubmit = document.getElementById('btnLoginSubmit');
  const loginErrorBox = document.getElementById('loginError');
  const btnTogglePassword = document.getElementById('btnTogglePassword');
  const passwordInput = document.getElementById('password');
  const usernameInput = document.getElementById('username');
  const btnThemeToggle = document.getElementById('btnThemeToggle');
  const themeIconSlot = document.getElementById('themeIconSlot');
  const themeLabel = document.querySelector('#btnThemeToggle .theme-label');
  const btnLogout = document.getElementById('btnLogout');

  // ---- Tema ----
  function applyTheme(theme) {
    document.documentElement.setAttribute('data-theme', theme);
    localStorage.setItem('fh_theme', theme);
    if (themeIconSlot) themeIconSlot.innerHTML = `<i data-lucide="${theme === 'dark' ? 'sun' : 'moon'}"></i>`;
    if (themeLabel) themeLabel.textContent = theme === 'dark' ? 'Claro' : 'Oscuro';
    utils.renderIcons();
  }
  applyTheme(localStorage.getItem('fh_theme') || 'light');

  btnThemeToggle?.addEventListener('click', () => {
    const current = document.documentElement.getAttribute('data-theme');
    applyTheme(current === 'dark' ? 'light' : 'dark');
  });

  function showLoginScreen(errorMessage = '') {
    screenLogin.hidden = false;
    screenMain.hidden = true;
    if (loginErrorBox) {
      if (errorMessage) {
        loginErrorBox.textContent = errorMessage;
        loginErrorBox.style.display = 'block';
      } else {
        loginErrorBox.style.display = 'none';
      }
    }
    utils.renderIcons();
  }

  function showHub(user) {
    screenLogin.hidden = true;
    screenMain.hidden = false;
    document.getElementById('hubAgentName').textContent = user.name;
    document.getElementById('hubAgentRole').textContent = `${user.role.toUpperCase()}${user.branch ? ' • ' + user.branch.name : ''}`;
    document.getElementById('hubAgentAvatar').textContent = utils.getInitials(user.name);
    renderSystemList();
    utils.renderIcons();
  }

  if (btnTogglePassword && passwordInput) {
    btnTogglePassword.addEventListener('click', () => {
      if (passwordInput.type === 'password') {
        passwordInput.type = 'text';
        btnTogglePassword.innerHTML = '<i data-lucide="eye-off"></i>';
      } else {
        passwordInput.type = 'password';
        btnTogglePassword.innerHTML = '<i data-lucide="eye"></i>';
      }
      utils.renderIcons();
    });
  }

  async function handleLoginSubmit(e) {
    if (e) e.preventDefault();
    const username = usernameInput ? usernameInput.value.trim().toLowerCase() : '';
    const password = passwordInput ? passwordInput.value.trim() : '';

    if (!username || !password) {
      if (loginErrorBox) {
        loginErrorBox.textContent = 'Por favor ingresa tu nombre de usuario y contraseña.';
        loginErrorBox.style.display = 'block';
      }
      return;
    }

    try {
      if (loginErrorBox) loginErrorBox.style.display = 'none';
      if (btnLoginSubmit) {
        btnLoginSubmit.disabled = true;
        btnLoginSubmit.textContent = 'Validando credenciales...';
      }
      const data = await auth.login(username, password);
      showHub(data.user);
    } catch (err) {
      if (loginErrorBox) {
        loginErrorBox.textContent = err.message || 'Nombre de usuario o contraseña incorrectos.';
        loginErrorBox.style.display = 'block';
      }
    } finally {
      if (btnLoginSubmit) {
        btnLoginSubmit.disabled = false;
        btnLoginSubmit.textContent = 'Iniciar sesión';
      }
    }
  }
  loginForm?.addEventListener('submit', handleLoginSubmit);

  btnLogout?.addEventListener('click', async () => {
    await auth.logout();
    if (passwordInput) passwordInput.value = '';
    showLoginScreen();
  });

  window.addEventListener('auth:unauthorized', () => {
    showLoginScreen('Tu sesión expiró. Inicia sesión nuevamente.');
  });

  // ==== Selector de sistemas ====
  // Cada tarjeta es la entrada al sistema: se toca y se entra. Antes había que elegirla acá y
  // después apretar "Entrar al sistema" dentro de la vista previa, que en celular quedaba a
  // dos mil píxeles de scroll — se tocaba una tarjeta y no parecía pasar nada.
  function renderSystemList() {
    const container = document.getElementById('systemList');
    if (!container) return;
    container.innerHTML = SYSTEMS.map((sys) => {
      const isComingSoon = sys.status === 'coming-soon';
      return `
        <button type="button"
          class="hub-system-card${isComingSoon ? ' coming-soon' : ''}"
          data-system-id="${sys.id}"
          ${isComingSoon ? 'disabled aria-disabled="true"' : ''}>
          <span class="hub-system-icon"><i data-lucide="${sys.icon}"></i></span>
          <span class="hub-system-info">
            <span class="hub-system-title-row">
              <strong>${utils.escapeHtml(sys.name)}</strong>
              ${isComingSoon ? '<span class="hub-system-badge">Próximamente</span>' : ''}
            </span>
            <span class="hub-system-desc">${utils.escapeHtml(sys.description)}</span>
            <span class="hub-system-tags">${sys.tags.map((t) => `<span class="hub-system-tag">${utils.escapeHtml(t)}</span>`).join('')}</span>
          </span>
          <span class="hub-system-chevron"><i data-lucide="chevron-right"></i></span>
        </button>
      `;
    }).join('');

    container.querySelectorAll('.hub-system-card').forEach((card) => {
      card.addEventListener('click', () => {
        const system = SYSTEMS.find((s) => s.id === card.dataset.systemId);
        if (!system || system.status === 'coming-soon' || !system.route) return;
        window.location.href = system.route;
      });
    });
    utils.renderIcons();
  }

  // ---- Arranque ----
  const existingUser = await auth.checkSession();
  if (existingUser) {
    showHub(existingUser);
  } else {
    showLoginScreen();
  }
  utils.renderIcons();
});
