/**
 * Farmhouse Link - Panel de entrada
 * Login liviano + selector de sistemas. Reutiliza auth.js/api.js tal cual los usa Atención al
 * Cliente (misma sesión por cookie), pero sin cargar ninguno de los módulos propios de esa app.
 *
 * Config centralizada de sistemas (NAV_GROUPS) en vez de repetir nombre/descripción/tags en
 * varios lugares — equivalente aquí, en JS plano sin framework, a lo que en un stack por
 * componentes sería un árbol de navegación. Agrupado por dominio (Atención, Operación,
 * Inventario, Equipo, Reportes, Administración) en vez de una lista plana de tarjetas, para que
 * esto empiece a leerse como una plataforma y no como un conjunto de apps sueltas.
 *
 * Acá vivía una vista previa grande de cada sistema, dibujada con datos inventados (ítems, stock,
 * costos, conversaciones). Se sacó: ocupaba toda la pantalla sin dejar usar nada, prometía un
 * inventario con stock que el backend no tiene, y en celular empujaba el botón de entrar fuera
 * de la vista. La tarjeta del sistema sigue siendo el botón de entrar.
 *
 * "Reportes" absorbe lo que antes era la tarjeta suelta "Farmhouse Link" (el tablero de ventas de
 * Invu, ruta /link sin cambios) — ese nombre quedó libre para ser el de toda la plataforma.
 * Un grupo entero se oculta si ninguno de sus ítems aplica al rol del usuario (hoy solo
 * "Administración" se filtra así); el filtro fino por permiso llega con el catálogo de
 * permisos (Fase 2 del plan grande) — este es un filtro honesto pero más grueso, por rol.
 */

const NAV_GROUPS = [
  {
    id: 'atencion',
    label: 'Atención al Cliente',
    items: [
      {
        id: 'whatsapp',
        name: 'Centro WhatsApp',
        description: 'Conversaciones, pedidos y atención al cliente por WhatsApp.',
        tags: ['Clientes', 'Pedidos', 'Soporte'],
        icon: 'message-square',
        status: 'active',
        route: '/app',
      },
    ],
  },
  {
    id: 'operacion',
    label: 'Operación de Sucursal',
    items: [
      {
        id: 'operacion',
        name: 'Operación de Sucursal',
        description: 'Recibir mercancía, contar, registrar merma, solicitar insumos, transferir y reportar incidencias.',
        tags: ['Sucursales', 'Tareas'],
        icon: 'clipboard-list',
        status: 'active',
        route: '/operacion',
      },
    ],
  },
  {
    id: 'inventario',
    label: 'Inventario y Abastecimiento',
    items: [
      {
        id: 'inventario',
        name: 'Inventario y Abastecimiento',
        description: 'Cargamentos, merma y conteos por sucursal. Gasto: próximamente.',
        tags: ['Stock', 'Sucursales', 'Abastecimiento'],
        icon: 'package',
        status: 'active',
        route: '/inventario',
      },
    ],
  },
  {
    id: 'equipo',
    label: 'Equipo',
    items: [
      {
        id: 'interno',
        name: 'Comunicación Interna',
        description: 'Mensajería entre el personal de todas las sucursales.',
        tags: ['Equipo', 'Sucursales', 'Mensajes'],
        icon: 'messages-square',
        status: 'active',
        route: '/interno',
      },
    ],
  },
  {
    id: 'reportes',
    label: 'Reportes',
    items: [
      {
        id: 'link',
        name: 'Ventas Invu',
        description: 'Ventas de la caja de Invu por sucursal, día y plato. Recetas y consumo: próximamente.',
        tags: ['Ventas', 'Invu', 'Sucursales'],
        icon: 'line-chart',
        status: 'active',
        route: '/link',
      },
    ],
  },
  {
    id: 'administracion',
    label: 'Administración',
    requiredPermission: 'users.manage',
    items: [
      {
        id: 'contabilidad',
        name: 'Contabilidad',
        description: 'Gastos, ingresos y reportes financieros de Farmhouse.',
        tags: ['Ingresos', 'Gastos', 'Reportes'],
        icon: 'calculator',
        status: 'coming-soon',
      },
      {
        id: 'administracion',
        name: 'Sucursales, empleados y dispositivos',
        description: 'Alta y baja de usuarios, dispositivos autorizados y sucursales.',
        tags: ['Sucursales', 'Empleados', 'Roles'],
        icon: 'settings',
        status: 'active',
        route: '/administracion',
      },
      {
        id: 'integraciones',
        name: 'Integraciones',
        description: 'Estado de la sincronización con Invu, por sucursal y por día.',
        tags: ['Invu', 'Sincronización', 'Sucursales'],
        icon: 'plug-zap',
        status: 'active',
        route: '/link?view=sincronizacion',
      },
    ],
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

  FarmhouseShell.initTheme();

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
    FarmhouseShell.fillUserHeader({ nameId: 'hubAgentName', roleId: 'hubAgentRole', avatarId: 'hubAgentAvatar' }, user);
    renderSystemList(user);
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

  FarmhouseShell.initLogout({
    afterLogout: () => {
      if (passwordInput) passwordInput.value = '';
      showLoginScreen();
    },
  });

  window.addEventListener('auth:unauthorized', () => {
    showLoginScreen('Tu sesión expiró. Inicia sesión nuevamente.');
  });

  // ==== Selector de sistemas, agrupado por dominio ====
  // Cada tarjeta es la entrada al sistema: se toca y se entra. Antes había que elegirla acá y
  // después apretar "Entrar al sistema" dentro de la vista previa, que en celular quedaba a
  // dos mil píxeles de scroll — se tocaba una tarjeta y no parecía pasar nada.
  function renderSystemCard(sys) {
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
  }

  function renderSystemList(user) {
    const container = document.getElementById('systemList');
    if (!container) return;
    // Fase 2: navegación filtrada por permiso real (ver /auth/me → permissions), ya no por rol
    // a mano. Un grupo con requiredPermission se oculta si el usuario no tiene esa capacidad.
    const permissions = user.permissions || [];
    const groups = NAV_GROUPS.filter((g) => !g.requiredPermission || permissions.includes(g.requiredPermission));

    container.innerHTML = groups.map((g) => `
      <div class="hub-nav-group">
        <p class="hub-nav-group-label">${utils.escapeHtml(g.label)}</p>
        <div class="hub-system-list">${g.items.map(renderSystemCard).join('')}</div>
      </div>
    `).join('');

    container.querySelectorAll('.hub-system-card').forEach((card) => {
      card.addEventListener('click', () => {
        const system = NAV_GROUPS.flatMap((g) => g.items).find((s) => s.id === card.dataset.systemId);
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
