/**
 * Farmhouse - Panel General (Hub de Sistemas)
 * Login liviano + portal selector de sistemas con vista previa grande (ver plan: launcher, no un
 * listado de tarjetas sueltas). Reutiliza auth.js/api.js tal cual los usa el Centro WhatsApp
 * (misma sesión por cookie), pero sin cargar ninguno de los módulos propios de esa app.
 *
 * Config centralizada de sistemas (SYSTEMS) en vez de repetir nombre/descripción/tags en varios
 * lugares — équivalente aquí, en JS plano sin framework, a lo que en un stack por componentes
 * sería un array de `SystemOption`. Cada `render*Preview()` hace de "componente" de vista previa.
 * Los datos de la vista previa de Inventario (ítems, stock, costos) son 100% mock/decorativos —
 * no vienen de ningún endpoint real, ver INVENTORY_MOCK_ITEMS.
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
    previewSubtitle: 'Conversá con tus clientes y gestioná pedidos en un solo lugar.',
  },
  {
    id: 'contabilidad',
    name: 'Contabilidad',
    description: 'Gastos, ingresos y reportes financieros de Farmhouse.',
    tags: ['Ingresos', 'Gastos', 'Reportes'],
    icon: 'calculator',
    status: 'coming-soon',
    previewSubtitle: 'Gastos, ingresos y reportes financieros de Farmhouse.',
  },
  {
    id: 'inventario',
    name: 'Inventario y Abastecimiento',
    description: 'Cargamentos por sucursal. Merma y gasto: próximamente.',
    tags: ['Stock', 'Sucursales', 'Abastecimiento'],
    icon: 'package',
    status: 'active',
    route: '/inventario',
    previewSubtitle: 'Controlá tus productos, stock y movimientos en tiempo real.',
  },
];

// Vista previa de Inventario: datos 100% decorativos para mostrar cómo se vería el módulo
// completo (Artículos/Movimientos/etc. todavía no existen de verdad — hoy solo existe Cargamento
// y Proveedores, ver /inventario). Nunca confundir con datos reales.
const INVENTORY_MOCK_ITEMS = [
  { sku: 'SH001', name: 'Shampoo', icon: 'droplet', stock: 120, status: 'in-stock', cost: 4.89, price: 10.00, minStock: 20, category: 'Cuidado Capilar', supplier: 'Distribuidora Belleza S.A.', unit: 'pcs (unidades)', location: 'Depósito Central', updatedAt: '12 Oct 2024 14:32', description: 'Limpieza y cuidado diario del cabello. Fórmula suave de uso frecuente.' },
  { sku: 'AC001', name: 'Acondicionador', icon: 'droplet', stock: 85, status: 'in-stock', cost: 5.20, price: 11.50, minStock: 20, category: 'Cuidado Capilar', supplier: 'Distribuidora Belleza S.A.', unit: 'pcs (unidades)', location: 'Depósito Central', updatedAt: '12 Oct 2024 14:32', description: 'Acondicionador suave para uso diario, libre de sulfatos.' },
  { sku: 'CR001', name: 'Crema Capilar', icon: 'sparkles', stock: 12, status: 'low-stock', cost: 6.10, price: 13.00, minStock: 15, category: 'Cuidado Capilar', supplier: 'Distribuidora Belleza S.A.', unit: 'pcs (unidades)', location: 'Depósito Central', updatedAt: '10 Oct 2024 09:10', description: 'Crema para peinar e hidratar el cabello.' },
  { sku: 'TR200', name: 'Tratamiento 200ml', icon: 'flask-conical', stock: 64, status: 'in-stock', cost: 7.80, price: 16.00, minStock: 20, category: 'Cuidado Capilar', supplier: 'Distribuidora Belleza S.A.', unit: 'pcs (unidades)', location: 'Depósito Central', updatedAt: '11 Oct 2024 17:45', description: 'Tratamiento intensivo de reparación capilar.' },
  { sku: 'AC002', name: 'Aceite Capilar', icon: 'droplet', stock: 93, status: 'in-stock', cost: 8.40, price: 17.50, minStock: 20, category: 'Cuidado Capilar', supplier: 'Distribuidora Belleza S.A.', unit: 'pcs (unidades)', location: 'Depósito Central', updatedAt: '09 Oct 2024 12:05', description: 'Aceite nutritivo para puntas abiertas.' },
  { sku: 'KIT001', name: 'Kit Anticaída', icon: 'package', stock: 8, status: 'low-stock', cost: 15.00, price: 32.00, minStock: 10, category: 'Cuidado Capilar', supplier: 'Distribuidora Belleza S.A.', unit: 'pcs (unidades)', location: 'Depósito Central', updatedAt: '08 Oct 2024 10:20', description: 'Kit completo anticaída: shampoo + tratamiento + serum.' },
];

const WHATSAPP_MOCK_CONVERSATIONS = [
  { name: 'María Fernández', lastMessage: 'Quiero el Bowl La Cosecha grande', time: '14:32', unread: 2 },
  { name: 'Carlos Ruiz', lastMessage: 'Perfecto, gracias!', time: '13:58', unread: 0 },
  { name: 'Sofía Herrera', lastMessage: '¿Hacen delivery a Costa del Este?', time: '13:40', unread: 1 },
];

const FEATURE_STRIPS = {
  inventario: [
    { icon: 'bar-chart-3', title: 'Control por sucursal', desc: 'Gestioná stock en todas tus ubicaciones.' },
    { icon: 'zap', title: 'Stock en tiempo real', desc: 'Información siempre actualizada.' },
    { icon: 'calendar-clock', title: 'Lotes y vencimientos', desc: 'Evitá pérdidas y optimizá tu inventario.' },
    { icon: 'settings', title: 'Ajustes de inventario', desc: 'Mantené tu stock siempre correcto.' },
  ],
  whatsapp: [
    { icon: 'inbox', title: 'Bandeja unificada', desc: 'Todas las conversaciones en un solo lugar.' },
    { icon: 'shopping-bag', title: 'Pedidos por chat', desc: 'Tu cliente arma su pedido sin salir de WhatsApp.' },
    { icon: 'map-pin', title: 'Multisucursal', desc: 'Cada sucursal atiende lo suyo.' },
    { icon: 'zap', title: 'Respuestas rápidas', desc: 'Menos tiempo de espera para el cliente.' },
  ],
};

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

  let selectedSystemId = 'inventario';
  let selectedMockItemSku = INVENTORY_MOCK_ITEMS[0].sku;

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
    selectSystem(selectedSystemId);
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

  // ==== Selector de sistemas (columna izquierda) ====
  function renderSystemList() {
    const container = document.getElementById('systemList');
    if (!container) return;
    container.innerHTML = SYSTEMS.map((sys) => {
      const isComingSoon = sys.status === 'coming-soon';
      const isSelected = sys.id === selectedSystemId;
      return `
        <button type="button"
          class="hub-system-card${isSelected ? ' selected' : ''}${isComingSoon ? ' coming-soon' : ''}"
          data-system-id="${sys.id}"
          aria-pressed="${isSelected}">
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
      card.addEventListener('click', () => selectSystem(card.dataset.systemId));
    });
    utils.renderIcons();
  }

  function selectSystem(id) {
    const system = SYSTEMS.find((s) => s.id === id) || SYSTEMS[0];
    selectedSystemId = system.id;
    renderSystemList();
    renderPreview(system);
  }

  // ==== Panel de vista previa (columna derecha) ====
  function renderPreview(system) {
    document.getElementById('previewIcon').innerHTML = `<i data-lucide="${system.icon}"></i>`;
    document.getElementById('previewTitle').textContent = `Vista previa — ${system.name}`;
    document.getElementById('previewSubtitle').textContent = system.previewSubtitle;

    const btnEnter = document.getElementById('btnEnterSystem');
    const btnLabel = document.getElementById('btnEnterSystemLabel');
    const isComingSoon = system.status === 'coming-soon';
    btnEnter.classList.toggle('disabled', isComingSoon);
    btnEnter.disabled = isComingSoon;
    btnLabel.textContent = isComingSoon ? 'Próximamente' : 'Entrar al sistema';
    btnEnter.onclick = () => {
      if (!isComingSoon && system.route) window.location.href = system.route;
    };

    const body = document.getElementById('previewBody');
    if (isComingSoon) {
      body.innerHTML = renderComingSoonPreview();
    } else if (system.id === 'inventario') {
      body.innerHTML = renderInventoryPreviewShell();
      renderMockItemList();
      renderMockItemDetail();
      const searchInput = document.getElementById('mockItemSearch');
      searchInput?.addEventListener('input', () => renderMockItemList(searchInput.value));
    } else if (system.id === 'whatsapp') {
      body.innerHTML = renderWhatsAppPreview();
    } else {
      body.innerHTML = '';
    }

    renderFeatureStrip(isComingSoon ? null : system.id);
    utils.renderIcons();
  }

  function renderComingSoonPreview() {
    return `
      <div class="hub-preview-coming-soon">
        <span class="hub-preview-lock"><i data-lucide="lock"></i></span>
        <h3>Próximamente</h3>
        <p>Este sistema está en construcción.</p>
      </div>
    `;
  }

  function renderWhatsAppPreview() {
    return `
      <div class="hub-preview-app hub-preview-app-whatsapp">
        <aside class="hub-mock-sidebar">
          <div class="hub-mock-sidebar-title"><i data-lucide="message-square"></i> WhatsApp</div>
          <nav class="hub-mock-nav">
            <span class="hub-mock-nav-item active"><i data-lucide="inbox"></i> Bandeja</span>
            <span class="hub-mock-nav-item"><i data-lucide="users"></i> Clientes</span>
            <span class="hub-mock-nav-item"><i data-lucide="shopping-bag"></i> Pedidos</span>
            <span class="hub-mock-nav-item"><i data-lucide="headphones"></i> Soporte</span>
          </nav>
        </aside>
        <div class="hub-mock-conv-list">
          ${WHATSAPP_MOCK_CONVERSATIONS.map((c, i) => `
            <div class="hub-mock-conv-row${i === 0 ? ' active' : ''}">
              <span class="hub-mock-conv-avatar">${utils.getInitials(c.name)}</span>
              <span class="hub-mock-conv-info">
                <strong>${utils.escapeHtml(c.name)}</strong>
                <small>${utils.escapeHtml(c.lastMessage)}</small>
              </span>
              <span class="hub-mock-conv-meta">
                <small>${c.time}</small>
                ${c.unread ? `<span class="hub-mock-unread-badge">${c.unread}</span>` : ''}
              </span>
            </div>
          `).join('')}
        </div>
        <div class="hub-mock-chat">
          <div class="hub-mock-chat-header">
            <span class="hub-mock-conv-avatar">MF</span>
            <span><strong>María Fernández</strong><small>+507 6123-4567</small></span>
          </div>
          <div class="hub-mock-chat-body">
            <div class="hub-mock-bubble in">Hola! ¿Tienen delivery hoy?</div>
            <div class="hub-mock-bubble out">¡Sí! Hacemos delivery hasta las 9:30pm 🚴</div>
            <div class="hub-mock-bubble in">Perfecto, quiero un Bowl La Cosecha grande</div>
            <div class="hub-mock-order-card">
              <div class="hub-mock-order-card-title"><i data-lucide="shopping-bag"></i> Pedido #FH-0412</div>
              <div class="hub-mock-order-card-row"><span>Bowl La Cosecha (grande)</span><span>$13.95</span></div>
              <div class="hub-mock-order-card-total"><span>Total</span><span>$14.92</span></div>
            </div>
          </div>
        </div>
      </div>
    `;
  }

  function renderInventoryPreviewShell() {
    return `
      <div class="hub-preview-app hub-preview-app-inventory">
        <aside class="hub-mock-sidebar">
          <div class="hub-mock-sidebar-title"><i data-lucide="package"></i> Inventario</div>
          <nav class="hub-mock-nav">
            <span class="hub-mock-nav-item active"><i data-lucide="layout-list"></i> Artículos</span>
            <span class="hub-mock-nav-item"><i data-lucide="repeat"></i> Movimientos</span>
            <span class="hub-mock-nav-item"><i data-lucide="arrow-left-right"></i> Transferencias</span>
            <span class="hub-mock-nav-item"><i data-lucide="shopping-cart"></i> Compras</span>
            <span class="hub-mock-nav-item"><i data-lucide="trending-up"></i> Ventas</span>
            <span class="hub-mock-nav-item"><i data-lucide="calendar-clock"></i> Lotes y Vencimientos</span>
            <span class="hub-mock-nav-item"><i data-lucide="settings"></i> Ajustes de Inventario</span>
            <span class="hub-mock-nav-item"><i data-lucide="bar-chart-3"></i> Reportes</span>
          </nav>
        </aside>
        <div class="hub-mock-items-col">
          <div class="hub-mock-toolbar">
            <div class="hub-mock-search"><i data-lucide="search"></i><input type="text" id="mockItemSearch" placeholder="Buscar artículos, SKU o proveedor..."></div>
            <button type="button" class="hub-mock-new-btn"><i data-lucide="plus"></i> Nuevo artículo</button>
            <button type="button" class="hub-mock-more-btn" tabindex="-1"><i data-lucide="more-vertical"></i></button>
          </div>
          <div class="hub-mock-list-header">
            <span id="mockItemsCount">Artículos (${INVENTORY_MOCK_ITEMS.length})</span>
            <span class="hub-mock-branch-filter">Todas las sucursales <i data-lucide="chevron-down"></i></span>
          </div>
          <div class="hub-mock-item-list" id="mockItemList"></div>
        </div>
        <div class="hub-mock-detail" id="mockItemDetail"></div>
      </div>
    `;
  }

  function mockItemRowHtml(item) {
    const statusLabel = item.status === 'low-stock' ? 'Bajo stock' : 'En stock';
    const statusClass = item.status === 'low-stock' ? 'low' : 'ok';
    const active = item.sku === selectedMockItemSku ? ' active' : '';
    return `
      <button type="button" class="hub-mock-item-row${active}" data-sku="${item.sku}">
        <span class="hub-mock-item-thumb"><i data-lucide="${item.icon}"></i></span>
        <span class="hub-mock-item-info">
          <strong>${utils.escapeHtml(item.name)}</strong>
          <small>SKU: ${item.sku}</small>
        </span>
        <span class="hub-mock-item-status ${statusClass}">${statusLabel}</span>
        <span class="hub-mock-item-qty">${item.stock}</span>
      </button>
    `;
  }

  function renderMockItemList(filter = '') {
    const list = document.getElementById('mockItemList');
    if (!list) return;
    const q = filter.trim().toLowerCase();
    const filtered = INVENTORY_MOCK_ITEMS.filter((i) =>
      !q || i.name.toLowerCase().includes(q) || i.sku.toLowerCase().includes(q) || i.supplier.toLowerCase().includes(q)
    );
    list.innerHTML = filtered.map(mockItemRowHtml).join('') || `<p class="hub-mock-empty">Sin resultados.</p>`;
    list.querySelectorAll('.hub-mock-item-row').forEach((row) => {
      row.addEventListener('click', () => {
        selectedMockItemSku = row.dataset.sku;
        renderMockItemList(filter);
        renderMockItemDetail();
      });
    });
    utils.renderIcons();
  }

  function renderMockItemDetail() {
    const detail = document.getElementById('mockItemDetail');
    if (!detail) return;
    const item = INVENTORY_MOCK_ITEMS.find((i) => i.sku === selectedMockItemSku) || INVENTORY_MOCK_ITEMS[0];
    const statusLabel = item.status === 'low-stock' ? 'Bajo stock' : 'En stock';
    const statusClass = item.status === 'low-stock' ? 'low' : 'ok';
    detail.innerHTML = `
      <div class="hub-mock-detail-header">
        <span class="hub-mock-detail-thumb"><i data-lucide="${item.icon}"></i></span>
        <span class="hub-mock-item-status ${statusClass}">${statusLabel}</span>
      </div>
      <h3>${utils.escapeHtml(item.name)}</h3>
      <p class="hub-mock-detail-sku">SKU: ${item.sku}</p>
      <p class="hub-mock-detail-desc">${utils.escapeHtml(item.description)}</p>
      <div class="hub-mock-metrics">
        <div><span>Stock disponible</span><strong>${item.stock} <small>unidades</small></strong></div>
        <div><span>Costo</span><strong>$${item.cost.toFixed(2)} <small>por unidad</small></strong></div>
        <div><span>Precio sugerido</span><strong>$${item.price.toFixed(2)} <small>por unidad</small></strong></div>
        <div><span>Stock mínimo</span><strong>${item.minStock} <small>unidades</small></strong></div>
      </div>
      <div class="hub-mock-detail-section-header">
        <span>Detalles</span>
        <button type="button" class="hub-mock-edit-btn" tabindex="-1"><i data-lucide="pencil"></i> Editar</button>
      </div>
      <div class="hub-mock-detail-rows">
        <div><span>Categoría</span><strong>${utils.escapeHtml(item.category)}</strong></div>
        <div><span>Proveedor</span><strong>${utils.escapeHtml(item.supplier)}</strong></div>
        <div><span>Unidad</span><strong>${utils.escapeHtml(item.unit)}</strong></div>
        <div><span>Ubicación principal</span><strong>${utils.escapeHtml(item.location)}</strong></div>
        <div><span>Última actualización</span><strong>${item.updatedAt}</strong></div>
      </div>
    `;
    utils.renderIcons();
  }

  function renderFeatureStrip(systemId) {
    const strip = document.getElementById('featureStrip');
    if (!strip) return;
    const features = FEATURE_STRIPS[systemId];
    if (!features) {
      strip.hidden = true;
      strip.innerHTML = '';
      return;
    }
    strip.hidden = false;
    strip.innerHTML = features.map((f) => `
      <div class="hub-feature-tile">
        <span class="hub-feature-icon"><i data-lucide="${f.icon}"></i></span>
        <div><strong>${f.title}</strong><p>${f.desc}</p></div>
      </div>
    `).join('');
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
