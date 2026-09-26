/**
 * Farmhouse Link - Panel General (hub)
 *
 * Login liviano + panel de entrada a los sistemas. Reutiliza auth.js/api.js tal cual (misma
 * sesión por cookie que el resto de las apps). Las piezas de la pantalla se arman con las
 * funciones de js/modules/hub_components.js; acá vive la configuración (qué módulos hay, a qué
 * ruta van, quién los ve) y la carga de datos.
 *
 * Dos estructuras según el dispositivo, no la misma apilada (ver hub.html): escritorio con
 * sidebar, grilla de módulos y actividad reciente; celular con accesos rápidos 2x2, pendientes y
 * navegación inferior. Se pintan las dos con los mismos datos y el CSS muestra la que va.
 *
 * Nada de datos inventados: "Actividad reciente" y "Pendiente hoy" salen de endpoints reales
 * (conversaciones, conteos, cargamentos, solicitudes de insumos, tareas). Si alguno no responde
 * (sin permiso, sin dispositivo autorizado, error), esa parte simplemente no aparece. Un panel
 * anterior se retiró justamente por mostrar cifras que el sistema no tenía.
 *
 * Rutas: las mismas de siempre. "Equipo" es Comunicación Interna (/interno); usuarios, roles,
 * dispositivos y sucursales viven en Administración (/administracion).
 */

// requiredPermission: se muestra solo si /auth/me lo trae en `permissions` (mismo catálogo que
// usa el backend, security/permissions.py). Reportes exige reports.view porque /link es solo
// para gerencia (admin y supervisores) — antes el hub se lo ofrecía también a los agentes, que
// entraban a una pantalla que les respondía 403.
const HUB_MODULES = [
  {
    id: 'whatsapp', name: 'Centro WhatsApp', shortName: 'WhatsApp',
    description: 'Conversaciones, pedidos y atención al cliente por WhatsApp.',
    shortDescription: 'Clientes, pedidos y soporte',
    tags: ['Clientes', 'Pedidos', 'Soporte'], icon: 'message-square', route: '/app',
  },
  {
    id: 'operacion', name: 'Operación de Sucursal', shortName: 'Operación',
    description: 'Recibir mercancía, contar, registrar merma, solicitar insumos, transferir y reportar incidencias.',
    shortDescription: 'Sucursales, tareas y reportes',
    tags: ['Sucursales', 'Tareas'], icon: 'clipboard-list', route: '/operacion',
  },
  {
    id: 'inventario', name: 'Inventario y Abastecimiento', shortName: 'Inventario',
    description: 'Cargamentos, merma y conteos por sucursal. Gasto: próximamente.',
    shortDescription: 'Stock y abastecimiento',
    tags: ['Stock', 'Sucursales', 'Abastecimiento'], icon: 'package', route: '/inventario',
  },
  {
    id: 'equipo', name: 'Equipo', shortName: 'Equipo',
    description: 'Comunicación interna entre el personal de todas las sucursales.',
    shortDescription: 'Mensajes y canales del equipo',
    tags: ['Mensajes', 'Canales', 'Sucursales'], icon: 'users', route: '/interno',
  },
  {
    id: 'administracion', name: 'Administración', shortName: 'Administración',
    description: 'Usuarios, roles, dispositivos autorizados y sucursales.',
    shortDescription: 'Usuarios y permisos',
    tags: ['Usuarios', 'Permisos', 'Sucursales'], icon: 'settings', route: '/administracion',
    requiredPermission: 'users.manage',
  },
  {
    id: 'reportes', name: 'Reportes de ventas', shortName: 'Reportes',
    description: 'Ventas de la caja de Invu por sucursal, día y plato.',
    shortDescription: 'Ventas por sucursal',
    tags: ['Ventas', 'Invu', 'Sucursales'], icon: 'line-chart', route: '/link',
    requiredPermission: 'reports.view',
  },
];

const HUB_SIDEBAR = [
  { id: 'inicio', label: 'Inicio', icon: 'house', route: '/hub' },
  { id: 'whatsapp', label: 'WhatsApp', icon: 'message-square', route: '/app' },
  { id: 'operacion', label: 'Operación', icon: 'clipboard-list', route: '/operacion' },
  { id: 'inventario', label: 'Inventario', icon: 'package', route: '/inventario' },
  { id: 'reportes', label: 'Reportes', icon: 'line-chart', route: '/link', requiredPermission: 'reports.view' },
  { id: 'equipo', label: 'Equipo', icon: 'users', route: '/interno' },
  { id: 'ajustes', label: 'Ajustes', icon: 'settings', route: '/administracion', requiredPermission: 'users.manage' },
  { id: 'integraciones', label: 'Integraciones', icon: 'plug-zap', route: '/link?view=sincronizacion', requiredPermission: 'integrations.manage' },
];

// Los cuatro accesos del celular (en ese orden); el resto va dentro de "Perfil".
const HUB_MOBILE_QUICK = ['whatsapp', 'operacion', 'inventario', 'equipo'];
const ACTIVITY_VISIBLE = 3;
const ACTIVITY_MAX = 8;

document.addEventListener('DOMContentLoaded', async () => {
  const $ = (id) => document.getElementById(id);
  const C = window.HubComponents;

  const screenBoot = $('hubBoot');
  const screenLogin = $('hubLoginScreen');
  const screenMain = $('hubMain');
  const loginForm = $('loginForm');
  const btnLoginSubmit = $('btnLoginSubmit');
  const loginErrorBox = $('loginError');
  const btnTogglePassword = $('btnTogglePassword');
  const passwordInput = $('password');
  const usernameInput = $('username');

  let activityEntries = [];
  let activityExpanded = false;

  FarmhouseShell.initTheme({ onThemeChange: syncMobileThemeButton });

  $('hubHeroArt').innerHTML = C.heroArt();
  $('hubSidebarArt').innerHTML = C.leafArt();

  // ==========================================================================
  // Sesión
  // ==========================================================================
  function showLoginScreen(errorMessage = '') {
    screenBoot.hidden = true;
    screenLogin.hidden = false;
    screenMain.hidden = true;
    closeProfileSheet();
    if (errorMessage) {
      loginErrorBox.textContent = errorMessage;
      loginErrorBox.style.display = 'block';
    } else {
      loginErrorBox.style.display = 'none';
    }
    utils.renderIcons();
  }

  function showHub(user) {
    screenBoot.hidden = true;
    screenLogin.hidden = true;
    screenMain.hidden = false;
    FarmhouseShell.fillUserHeader({ nameId: 'hubAgentName', roleId: 'hubAgentRole', avatarId: 'hubAgentAvatar' }, user);
    FarmhouseShell.fillUserHeader({ nameId: 'hubProfileName', roleId: 'hubProfileRole', avatarId: 'hubProfileAvatar' }, user);
    $('hubMobileAvatar').textContent = utils.getInitials(user.name);
    $('hubGreeting').innerHTML = `${greetingForNow()}, <strong>${utils.escapeHtml(firstName(user.name))}</strong>`;

    const allowed = (entry) => !entry.requiredPermission || (user.permissions || []).includes(entry.requiredPermission);
    const modules = HUB_MODULES.filter(allowed);
    renderSidebar(HUB_SIDEBAR.filter(allowed));
    renderModuleGrid(modules);
    renderMobile(modules);
    utils.renderIcons();
    loadDashboardData();
  }

  if (btnTogglePassword && passwordInput) {
    btnTogglePassword.addEventListener('click', () => {
      const showing = passwordInput.type === 'text';
      passwordInput.type = showing ? 'password' : 'text';
      btnTogglePassword.innerHTML = `<i data-lucide="${showing ? 'eye' : 'eye-off'}"></i>`;
      utils.renderIcons();
    });
  }

  loginForm?.addEventListener('submit', async (e) => {
    e.preventDefault();
    const username = usernameInput.value.trim().toLowerCase();
    const password = passwordInput.value.trim();
    if (!username || !password) {
      loginErrorBox.textContent = 'Por favor ingresa tu nombre de usuario y contraseña.';
      loginErrorBox.style.display = 'block';
      return;
    }
    try {
      loginErrorBox.style.display = 'none';
      btnLoginSubmit.disabled = true;
      btnLoginSubmit.textContent = 'Validando credenciales...';
      const data = await auth.login(username, password);
      showHub(data.user);
    } catch (err) {
      loginErrorBox.textContent = err.message || 'Nombre de usuario o contraseña incorrectos.';
      loginErrorBox.style.display = 'block';
    } finally {
      btnLoginSubmit.disabled = false;
      btnLoginSubmit.textContent = 'Iniciar sesión';
    }
  });

  const afterLogout = () => {
    passwordInput.value = '';
    showLoginScreen();
  };
  FarmhouseShell.initLogout({ logoutBtnId: 'btnLogout', afterLogout });
  FarmhouseShell.initLogout({ logoutBtnId: 'btnLogoutMobile', afterLogout });

  window.addEventListener('auth:unauthorized', () => {
    if (!screenMain.hidden) showLoginScreen('Tu sesión expiró. Inicia sesión nuevamente.');
  });

  // ==========================================================================
  // Escritorio: sidebar, grilla, buscador
  // ==========================================================================
  function renderSidebar(items) {
    $('hubSidebarNav').innerHTML = C.sidebarNav(items, 'inicio');
  }

  function renderModuleGrid(modules) {
    // La tarjeta de marca completa la última fila de la grilla de 3 (ocupa 1, 2 o las 3
    // columnas según cuántos módulos le toquen a este usuario), así nunca queda un hueco.
    const rest = modules.length % 3;
    const brandSpan = rest === 0 ? 3 : 3 - rest;
    $('hubModuleGrid').innerHTML = modules.map(C.moduleCard).join('') + C.brandCard(brandSpan);
  }

  const searchInput = $('hubSearchInput');
  searchInput.addEventListener('input', () => filterModules(searchInput.value));
  $('hubSearchForm').addEventListener('submit', (e) => {
    e.preventDefault();
    const q = searchInput.value.trim();
    if (!q) return;
    const visible = [...document.querySelectorAll('.hub-module-card')].filter((c) => !c.hidden);
    // Si el texto coincide con un solo módulo, Enter entra a ese módulo; si no, busca el
    // cliente (nombre o teléfono) en la bandeja de Centro WhatsApp.
    if (visible.length === 1) {
      window.location.href = visible[0].getAttribute('href');
    } else {
      window.location.href = `/app?q=${encodeURIComponent(q)}`;
    }
  });

  function filterModules(raw) {
    const q = raw.trim().toLowerCase();
    let shown = 0;
    document.querySelectorAll('.hub-module-card').forEach((card) => {
      const match = !q || card.dataset.search.includes(q);
      card.hidden = !match;
      if (match) shown += 1;
    });
    const brand = document.querySelector('.hub-brand-card');
    if (brand) brand.hidden = !!q;
    $('hubSearchEmpty').hidden = !(q && shown === 0);
  }

  // Ctrl/⌘ + K enfoca el buscador (el atajo que muestra la etiqueta del campo).
  const isMac = /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent);
  $('hubSearchKbd').textContent = isMac ? '⌘ K' : 'Ctrl K';
  document.addEventListener('keydown', (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k' && !screenMain.hidden) {
      e.preventDefault();
      searchInput.focus();
      searchInput.select();
    }
  });

  // ==========================================================================
  // Celular: accesos rápidos, perfil, navegación inferior
  // ==========================================================================
  function renderMobile(modules) {
    const byId = Object.fromEntries(modules.map((m) => [m.id, m]));
    $('hubMobileGrid').innerHTML = HUB_MOBILE_QUICK.filter((id) => byId[id]).map((id) => C.mobileModuleCard(byId[id])).join('');

    // Lo que no entra en los cuatro accesos (Administración, Reportes, Integraciones) sigue a
    // mano dentro de Perfil: ningún módulo queda sin entrada en el celular.
    const extra = modules.filter((m) => !HUB_MOBILE_QUICK.includes(m.id));
    const integ = HUB_SIDEBAR.find((s) => s.id === 'integraciones');
    const user = auth.getUser();
    const extraLinks = extra.map((m) => ({ label: m.name, icon: m.icon, route: m.route }));
    if (integ && (user?.permissions || []).includes(integ.requiredPermission)) {
      extraLinks.push({ label: integ.label, icon: integ.icon, route: integ.route });
    }
    $('hubProfileLinks').innerHTML = extraLinks.map((l) => `
      <a class="hub-sheet-row" href="${utils.escapeHtml(l.route)}">
        <i data-lucide="${utils.escapeHtml(l.icon)}" aria-hidden="true"></i><span>${utils.escapeHtml(l.label)}</span>
      </a>`).join('');
  }

  const profileSheet = $('hubProfileSheet');
  function openProfileSheet() {
    profileSheet.hidden = false;
    syncMobileThemeButton(document.documentElement.getAttribute('data-theme'));
    setBottomActive('perfil');
    $('btnCloseProfile').focus();
  }
  function closeProfileSheet() {
    if (profileSheet.hidden) return;
    profileSheet.hidden = true;
    setBottomActive('inicio');
  }
  $('hubMobileAvatar').addEventListener('click', openProfileSheet);
  $('btnCloseProfile').addEventListener('click', closeProfileSheet);
  profileSheet.addEventListener('click', (e) => { if (e.target === profileSheet) closeProfileSheet(); });
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeProfileSheet(); });

  function syncMobileThemeButton(theme) {
    const dark = theme === 'dark';
    const slot = $('mobileThemeIconSlot');
    if (slot) slot.innerHTML = `<i data-lucide="${dark ? 'sun' : 'moon'}"></i>`;
    const label = $('mobileThemeLabel');
    if (label) label.textContent = dark ? 'Modo claro' : 'Modo oscuro';
    utils.renderIcons();
  }
  $('btnMobileTheme').addEventListener('click', () => {
    const current = document.documentElement.getAttribute('data-theme');
    FarmhouseShell.applyTheme(current === 'dark' ? 'light' : 'dark', {
      themeIconId: 'themeIconSlot', themeLabelSelector: '#btnThemeToggle .theme-label', onThemeChange: syncMobileThemeButton,
    });
  });

  function setBottomActive(id) {
    document.querySelectorAll('.hub-bottom-item').forEach((b) => {
      const on = b.dataset.bottom === id;
      b.classList.toggle('active', on);
      if (on) b.setAttribute('aria-current', 'page'); else b.removeAttribute('aria-current');
    });
  }
  function scrollToPending() {
    $('hubPending').scrollIntoView({ behavior: 'smooth', block: 'start' });
  }
  document.querySelector('[data-bottom="inicio"]').addEventListener('click', () => {
    closeProfileSheet();
    window.scrollTo({ top: 0, behavior: 'smooth' });
  });
  document.querySelector('[data-bottom="avisos"]').addEventListener('click', () => {
    closeProfileSheet();
    scrollToPending();
  });
  document.querySelector('[data-bottom="perfil"]').addEventListener('click', openProfileSheet);
  $('btnMobileBell').addEventListener('click', scrollToPending);

  // ==========================================================================
  // Datos reales: actividad reciente (escritorio) y pendientes de hoy (celular)
  // ==========================================================================
  async function loadDashboardData() {
    $('hubActivityList').innerHTML = '<div class="hub-skeleton"></div><div class="hub-skeleton"></div><div class="hub-skeleton"></div>';
    $('hubPendingList').innerHTML = '<div class="hub-skeleton"></div><div class="hub-skeleton"></div>';

    const [counts, tasks, requests, convs, stockCounts, shipments] = await Promise.allSettled([
      api.get('/conversations/counts'),
      api.get('/ops/tasks?status=pendiente&limit=200'),
      api.get('/ops/requests?status=open&limit=200'),
      api.get('/conversations/?status=todas&limit=4'),
      api.get('/inventory/counts?limit=3'),
      api.get('/inventory/shipments?limit=3'),
    ]);
    const ok = (r) => (r.status === 'fulfilled' ? r.value : null);

    renderPending(ok(counts), ok(tasks), ok(requests));
    activityEntries = buildActivity(ok(convs), ok(stockCounts), ok(shipments), ok(requests));
    renderActivity();
  }

  function renderPending(counts, tasks, requests) {
    const rows = [];
    if (counts && counts.no_asignadas > 0) {
      rows.push({ icon: 'message-square', tone: 'green', route: '/app',
        title: plural(counts.no_asignadas, 'conversación nueva', 'conversaciones nuevas'), subtitle: 'En Centro WhatsApp' });
    }
    if (Array.isArray(tasks) && tasks.length) {
      rows.push({ icon: 'clipboard-list', tone: 'orange', route: '/operacion',
        title: plural(tasks.length, 'tarea por completar', 'tareas por completar'), subtitle: 'En Operación de Sucursal' });
    }
    if (Array.isArray(requests) && requests.length) {
      rows.push({ icon: 'package', tone: 'blue', route: '/operacion',
        title: plural(requests.length, 'solicitud de insumos', 'solicitudes de insumos'), subtitle: 'Pendiente de revisión' });
    }
    $('hubPendingList').innerHTML = rows.length
      ? rows.map(C.pendingRow).join('')
      : C.activityEmpty('Nada pendiente por ahora.');
    const hasPending = rows.length > 0;
    $('hubBellDot').hidden = !hasPending;
    $('hubBottomBellDot').hidden = !hasPending;
    utils.renderIcons();
  }

  function buildActivity(convs, stockCounts, shipments, requests) {
    const entries = [];
    (convs || []).forEach((c) => {
      const last = (c.messages || []).filter((m) => !m.is_internal).slice(-1)[0];
      const incoming = last && last.direction === 'incoming';
      entries.push({
        at: c.updated_at, icon: 'message-square', tone: 'green', badge: 'WhatsApp',
        title: incoming ? `Mensaje de ${c.contact?.name || 'un cliente'}` : `Conversación con ${c.contact?.name || 'un cliente'}`,
        subtitle: last ? truncate(last.content, 70) : (c.branch ? c.branch.name : 'Sin sucursal asignada'),
        route: `/app?conversation_id=${c.id}`,
      });
    });
    (stockCounts || []).forEach((s) => entries.push({
      at: s.counted_at, icon: 'clipboard-check', tone: 'blue', badge: 'Inventario',
      title: 'Conteo de inventario completado', subtitle: `${s.branch_name} · ${s.counted_by_name}`, route: '/inventario',
    }));
    (shipments || []).forEach((s) => entries.push({
      at: s.received_at, icon: 'truck', tone: 'blue', badge: 'Inventario',
      title: 'Cargamento recibido', subtitle: [s.branch_name, s.supplier_name].filter(Boolean).join(' · '), route: '/inventario',
    }));
    (requests || []).slice(0, 3).forEach((r) => entries.push({
      at: r.created_at, icon: 'package-plus', tone: 'orange', badge: 'Operación',
      title: `Solicitud de insumos: ${r.item_name}`, subtitle: r.branch_name, route: '/operacion',
    }));
    return entries
      .filter((e) => e.at)
      .sort((a, b) => utils._parseServerDate(b.at) - utils._parseServerDate(a.at))
      .slice(0, ACTIVITY_MAX)
      .map((e) => ({ ...e, when: relativeWhen(e.at) }));
  }

  function renderActivity() {
    const list = $('hubActivityList');
    const btn = $('btnActivityAll');
    if (!activityEntries.length) {
      list.innerHTML = C.activityEmpty('Todavía no hay actividad para mostrar.');
      btn.hidden = true;
    } else {
      const shown = activityExpanded ? activityEntries : activityEntries.slice(0, ACTIVITY_VISIBLE);
      list.innerHTML = shown.map(C.activityRow).join('');
      btn.hidden = activityEntries.length <= ACTIVITY_VISIBLE;
      btn.innerHTML = activityExpanded
        ? 'Ver menos <i data-lucide="arrow-up" aria-hidden="true"></i>'
        : 'Ver todo <i data-lucide="arrow-right" aria-hidden="true"></i>';
    }
    utils.renderIcons();
  }
  $('btnActivityAll').addEventListener('click', () => {
    activityExpanded = !activityExpanded;
    renderActivity();
  });

  // ==========================================================================
  // Utilidades de texto
  // ==========================================================================
  function plural(n, one, many) { return `${n} ${n === 1 ? one : many}`; }
  function truncate(s, n) { const t = String(s || ''); return t.length > n ? `${t.slice(0, n - 1)}…` : t; }
  function firstName(name) { return String(name || '').trim().split(/\s+/)[0] || ''; }
  function greetingForNow() {
    const h = new Date().getHours();
    if (h < 12) return 'Buenos días';
    if (h < 19) return 'Buenas tardes';
    return 'Buenas noches';
  }
  function relativeWhen(iso) {
    const d = utils._parseServerDate(iso);
    if (!d || Number.isNaN(d.getTime())) return '';
    const time = d.toLocaleTimeString('es-PA', { hour: '2-digit', minute: '2-digit' });
    const { label } = utils.formatDateSeparator(iso);
    if (label === 'Hoy' || label === 'Ayer') return `${label}, ${time}`;
    return `${d.toLocaleDateString('es-PA', { day: 'numeric', month: 'short' })}, ${time}`;
  }

  // ---- Arranque ----
  const existingUser = await auth.checkSession();
  if (existingUser) {
    showHub(existingUser);
  } else {
    showLoginScreen();
  }
});
