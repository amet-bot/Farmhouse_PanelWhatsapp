/**
 * Farmhouse - Inventario y Abastecimiento
 *
 * La página real calca la vista previa del Panel General (ver hub.js → renderInventoryPreview*):
 * rail de navegación + columna de lista + panel de detalle. Cuatro vistas reales sobre los
 * endpoints que ya existen — Resumen, Cargamentos, Insumos y Proveedores. Merma, Gasto por
 * sucursal, Lotes y Reportes siguen siendo "Próximamente" en el rail, sin vista propia.
 *
 * Nada de stock acá: el backend solo registra ENTRADAS (Shipment), así que la página nunca habla
 * de existencias ni de "bajo stock" — eso lo promete la vista previa del hub con datos mock y
 * necesita salidas (Merma) para ser cierto. Lo que sí se puede decir con lo que hay es cuánto
 * entró, cuándo, de quién y a qué costo; sobre eso se arman las métricas.
 *
 * Dos conjuntos de datos, a propósito:
 *   · state.shipments  — la lista paginada de la vista Cargamentos. Respeta el filtro de
 *                        sucursal pidiéndoselo al backend (`branch_id`), no filtrando en el
 *                        navegador, para no mentir cuando hay más páginas sin cargar.
 *   · state.analytics  — una sola tanda sin filtrar que alimenta Resumen, Insumos y Proveedores,
 *                        así las métricas no cambian al mover el filtro de la otra vista.
 */

document.addEventListener('DOMContentLoaded', async () => {

  const PAGE_SIZE = 50;          // tamaño de página de la lista de cargamentos
  const ANALYTICS_SIZE = 200;    // tope del backend; alcanza de sobra para las métricas
  const RECENT_DAYS = 30;

  const $ = (id) => document.getElementById(id);

  const state = {
    user: null,
    isGlobalScope: false,
    fixedBranchId: null,
    branches: [],
    shipments: [],
    shipmentsOffset: 0,
    shipmentsHasMore: false,
    analytics: [],
    analyticsTruncated: false,
    items: [],
    suppliers: [],
    view: 'resumen',
    branchFilter: '',
    selected: { shipment: null, item: null, supplier: null },
    search: { shipment: '', item: '', supplier: '' },
    selectedSupplierId: '',
  };

  // ==========================================================================
  // Utilidades de formato
  // ==========================================================================
  const moneyFormatter = new Intl.NumberFormat('es-PA', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const money = (n) => `$${moneyFormatter.format(Number(n || 0))}`;

  /** Cantidades con hasta 3 decimales pero sin ceros de relleno: 2.500 → "2.5", 3.000 → "3". */
  const qty = (n) => {
    const num = Number(n || 0);
    return String(Number(num.toFixed(3)));
  };

  const pluralize = (n, one, many) => `${n} ${n === 1 ? one : many}`;

  const daysAgoIso = (days) => {
    const d = new Date();
    d.setDate(d.getDate() - days);
    return d;
  };

  const toLocalInputValue = (date) => {
    const pad = (n) => String(n).padStart(2, '0');
    return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
  };

  const esc = (v) => utils.escapeHtml(v);

  const emptyStateHtml = (icon, title, text) => `
    <div class="inv-empty">
      <span class="inv-empty-icon"><i data-lucide="${icon}"></i></span>
      <strong>${esc(title)}</strong>
      <p>${esc(text)}</p>
    </div>`;

  const skeletonListHtml = (rows = 4) => Array.from({ length: rows }).map(() => `
    <div class="inv-skeleton-row">
      <span class="inv-sk inv-sk-thumb"></span>
      <span class="inv-sk inv-sk-line"></span>
      <span class="inv-sk inv-sk-line short"></span>
    </div>`).join('');

  // ==========================================================================
  // Tema y sesión
  // ==========================================================================
  const btnThemeToggle = $('btnThemeToggle');
  const themeIconSlot = $('themeIconSlot');
  const themeLabel = document.querySelector('#btnThemeToggle .theme-label');

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

  $('btnLogout')?.addEventListener('click', async () => {
    await auth.logout();
    window.location.href = '/';
  });

  // Sesión expirada a mitad de uso: vuelve al hub, que tiene su propia pantalla de login.
  window.addEventListener('auth:unauthorized', () => {
    window.location.href = '/';
  });

  // ==========================================================================
  // Modales
  // ==========================================================================
  function openModal(id) {
    $(id)?.classList.add('active');
    utils.renderIcons();
  }

  function closeModal(id) {
    $(id)?.classList.remove('active');
  }

  document.querySelectorAll('[data-close-modal]').forEach((btn) => {
    btn.addEventListener('click', () => closeModal(btn.dataset.closeModal));
  });

  document.querySelectorAll('.modal-backdrop').forEach((backdrop) => {
    backdrop.addEventListener('click', (e) => {
      if (e.target === backdrop) backdrop.classList.remove('active');
    });
  });

  document.addEventListener('keydown', (e) => {
    if (e.key !== 'Escape') return;
    // Si hay un autocomplete abierto, Escape lo cierra a él y no el modal entero.
    const openSuggestions = Array.from(document.querySelectorAll('.inv-item-suggestions')).filter((b) => !b.hidden);
    if (openSuggestions.length) {
      openSuggestions.forEach((b) => { b.hidden = true; b.innerHTML = ''; });
      return;
    }
    document.querySelectorAll('.modal-backdrop.active').forEach((m) => m.classList.remove('active'));
  });

  // Cierra cualquier autocomplete al hacer clic afuera (un solo listener delegado).
  document.addEventListener('click', (e) => {
    document.querySelectorAll('.inv-item-suggestions').forEach((box) => {
      const container = box.closest('.inv-line-row') || box.closest('.inv-supplier-wrap');
      if (container && !container.contains(e.target)) {
        box.hidden = true;
        box.innerHTML = '';
      }
    });
  });

  // ==========================================================================
  // Navegación entre vistas
  // ==========================================================================
  const VIEWS = { resumen: 'viewResumen', cargamentos: 'viewCargamentos', insumos: 'viewInsumos', proveedores: 'viewProveedores' };

  function setView(view) {
    if (!VIEWS[view]) return;
    state.view = view;
    Object.entries(VIEWS).forEach(([key, id]) => { $(id).hidden = key !== view; });
    document.querySelectorAll('#invNav .inv-nav-item').forEach((btn) => {
      btn.classList.toggle('active', btn.dataset.view === view);
    });
    utils.renderIcons();
  }

  document.querySelectorAll('#invNav .inv-nav-item').forEach((btn) => {
    btn.addEventListener('click', () => setView(btn.dataset.view));
  });

  document.querySelectorAll('[data-goto-view]').forEach((btn) => {
    btn.addEventListener('click', () => setView(btn.dataset.gotoView));
  });

  document.querySelectorAll('[data-open-shipment]').forEach((btn) => {
    btn.addEventListener('click', () => openShipmentModal());
  });

  // ==========================================================================
  // Carga de datos
  // ==========================================================================
  async function loadShipments({ reset = false } = {}) {
    if (reset) {
      state.shipments = [];
      state.shipmentsOffset = 0;
      $('shipmentList').innerHTML = skeletonListHtml();
    }
    const params = new URLSearchParams({ limit: String(PAGE_SIZE), offset: String(state.shipmentsOffset) });
    if (state.branchFilter) params.set('branch_id', state.branchFilter);
    try {
      const page = await api.get(`/inventory/shipments?${params.toString()}`);
      state.shipments = state.shipments.concat(page);
      state.shipmentsOffset += page.length;
      state.shipmentsHasMore = page.length === PAGE_SIZE;
    } catch (err) {
      utils.showToast(err.message || 'No se pudo cargar el historial.', 'error');
      state.shipmentsHasMore = false;
    }
    renderShipmentList();
  }

  async function loadAnalytics() {
    try {
      const rows = await api.get(`/inventory/shipments?limit=${ANALYTICS_SIZE}`);
      state.analytics = rows;
      state.analyticsTruncated = rows.length === ANALYTICS_SIZE;
    } catch (err) {
      state.analytics = [];
    }
  }

  async function loadCatalogs() {
    const [items, suppliers] = await Promise.all([
      api.get('/inventory/items?limit=200').catch(() => []),
      api.get('/inventory/suppliers?limit=200').catch(() => []),
    ]);
    state.items = items;
    state.suppliers = suppliers;

    // Alimenta el datalist de categorías del modal con las que ya existen.
    const categories = Array.from(new Set(items.map((i) => i.category).filter(Boolean))).sort();
    $('categoryOptions').innerHTML = categories.map((c) => `<option value="${esc(c)}"></option>`).join('');
  }

  // ==========================================================================
  // Métricas derivadas (siempre sobre state.analytics)
  // ==========================================================================
  function shipmentTotal(s) {
    return s.total_cost != null ? Number(s.total_cost) : 0;
  }

  function recentShipments() {
    const cutoff = daysAgoIso(RECENT_DAYS);
    return state.analytics.filter((s) => {
      const d = utils._parseServerDate(s.received_at);
      return d && d >= cutoff;
    });
  }

  function itemStats(itemId) {
    const stats = { shipments: 0, quantity: 0, spend: 0, costedQuantity: 0, last: null, suppliers: new Set(), branches: new Set(), unit: '' };
    state.analytics.forEach((s) => {
      const lines = s.items.filter((l) => l.inventory_item_id === itemId);
      if (!lines.length) return;
      stats.shipments += 1;
      if (s.supplier_name) stats.suppliers.add(s.supplier_name);
      stats.branches.add(s.branch_name);
      const date = utils._parseServerDate(s.received_at);
      if (date && (!stats.last || date > stats.last)) stats.last = date;
      lines.forEach((l) => {
        stats.unit = stats.unit || l.unit;
        stats.quantity += Number(l.quantity);
        if (l.unit_cost != null) {
          stats.spend += Number(l.quantity) * Number(l.unit_cost);
          stats.costedQuantity += Number(l.quantity);
        }
      });
    });
    return stats;
  }

  function supplierStats(supplierId) {
    const stats = { shipments: 0, spend: 0, last: null, items: new Set(), branches: new Set() };
    state.analytics.forEach((s) => {
      if (s.supplier_id !== supplierId) return;
      stats.shipments += 1;
      stats.spend += shipmentTotal(s);
      stats.branches.add(s.branch_name);
      s.items.forEach((l) => stats.items.add(l.item_name));
      const date = utils._parseServerDate(s.received_at);
      if (date && (!stats.last || date > stats.last)) stats.last = date;
    });
    return stats;
  }

  // ==========================================================================
  // Vista: Resumen
  // ==========================================================================
  function renderResumen() {
    const recent = recentShipments();
    const spend = recent.reduce((acc, s) => acc + shipmentTotal(s), 0);
    const costed = recent.filter((s) => s.total_cost != null).length;
    const distinctItems = new Set();
    const supplierIds = new Set();
    recent.forEach((s) => {
      s.items.forEach((l) => distinctItems.add(l.inventory_item_id));
      if (s.supplier_id) supplierIds.add(s.supplier_id);
    });

    $('resumenSubtitle').textContent = state.analyticsTruncated
      ? `Lo que entró a tus sucursales, sobre los últimos ${ANALYTICS_SIZE} cargamentos.`
      : 'Lo que entró a tus sucursales.';

    const kpis = [
      {
        icon: 'truck',
        label: `Cargamentos (${RECENT_DAYS} días)`,
        value: String(recent.length),
        sub: state.analytics.length ? `${state.analytics.length} en total registrados` : 'Todavía sin registros',
      },
      {
        icon: 'dollar-sign',
        label: 'Gasto registrado',
        value: money(spend),
        sub: recent.length ? `${costed} de ${recent.length} con costo cargado` : 'Cargá el costo unitario para verlo',
      },
      {
        icon: 'layout-list',
        label: 'Insumos distintos',
        value: String(distinctItems.size),
        sub: `${state.items.length} en el catálogo`,
      },
      {
        icon: 'building-2',
        label: 'Proveedores activos',
        value: String(supplierIds.size),
        sub: `${state.suppliers.length} en el catálogo`,
      },
    ];

    $('kpiRow').innerHTML = kpis.map((k) => `
      <div class="inv-kpi">
        <span class="inv-kpi-label"><i data-lucide="${k.icon}"></i> ${esc(k.label)}</span>
        <span class="inv-kpi-value">${esc(k.value)}</span>
        <span class="inv-kpi-sub">${esc(k.sub)}</span>
      </div>`).join('');

    renderTopItemsBars(recent);
    renderRecentShipments();
    renderBranchBars(recent);
    utils.renderIcons();
  }

  /**
   * Ranking de insumos. Se ordena por GASTO cuando hay costos cargados, porque las cantidades de
   * insumos distintos viven en unidades distintas (50 kg contra 200 unidades no se comparan). Si
   * no hay ni un costo en la ventana, cae a "veces recibido", que también es comparable.
   */
  function renderTopItemsBars(recent) {
    const byItem = new Map();
    recent.forEach((s) => {
      s.items.forEach((l) => {
        const entry = byItem.get(l.inventory_item_id) || { name: l.item_name, unit: l.unit, spend: 0, times: 0, quantity: 0 };
        entry.times += 1;
        entry.quantity += Number(l.quantity);
        if (l.unit_cost != null) entry.spend += Number(l.quantity) * Number(l.unit_cost);
        byItem.set(l.inventory_item_id, entry);
      });
    });

    const rows = Array.from(byItem.values());
    const hasSpend = rows.some((r) => r.spend > 0);
    const metric = hasSpend ? 'spend' : 'times';
    $('topItemsNote').textContent = hasSpend ? `Por gasto · últimos ${RECENT_DAYS} días` : `Por veces recibido · últimos ${RECENT_DAYS} días`;

    const top = rows.sort((a, b) => b[metric] - a[metric]).slice(0, 6);
    const max = top.length ? top[0][metric] : 0;

    if (!top.length) {
      $('topItemsBars').innerHTML = emptyStateHtml('bar-chart-3', 'Sin movimientos', `Nada recibido en los últimos ${RECENT_DAYS} días.`);
      return;
    }

    $('topItemsBars').innerHTML = top.map((r) => {
      const value = metric === 'spend' ? money(r.spend) : pluralize(r.times, 'vez', 'veces');
      const pct = max > 0 ? Math.max(2, Math.round((r[metric] / max) * 100)) : 0;
      return `
        <div class="inv-bar-row">
          <span class="inv-bar-name">${esc(r.name)}</span>
          <span class="inv-bar-value">${esc(value)}</span>
          <span class="inv-bar-track"><span class="inv-bar-fill" style="width:${pct}%"></span></span>
        </div>`;
    }).join('');
  }

  function renderBranchBars(recent) {
    const panel = $('branchPanel');
    if (!state.isGlobalScope) { panel.hidden = true; return; }

    const byBranch = new Map();
    recent.forEach((s) => byBranch.set(s.branch_name, (byBranch.get(s.branch_name) || 0) + 1));
    const rows = Array.from(byBranch.entries()).sort((a, b) => b[1] - a[1]);
    if (!rows.length) { panel.hidden = true; return; }

    panel.hidden = false;
    const max = rows[0][1];
    $('branchBars').innerHTML = rows.map(([name, count]) => {
      const pct = Math.max(2, Math.round((count / max) * 100));
      return `
        <div class="inv-bar-row">
          <span class="inv-bar-name">${esc(name)}</span>
          <span class="inv-bar-value">${count}</span>
          <span class="inv-bar-track"><span class="inv-bar-fill" style="width:${pct}%"></span></span>
        </div>`;
    }).join('');
  }

  function renderRecentShipments() {
    const rows = state.analytics.slice(0, 5);
    const container = $('recentShipments');
    if (!rows.length) {
      container.innerHTML = emptyStateHtml('truck', 'Todavía no hay cargamentos', 'Registrá el primero y aparecerá acá.');
      utils.renderIcons();
      return;
    }
    container.innerHTML = rows.map((s) => `
      <button type="button" class="inv-mini-row" data-shipment-id="${s.id}">
        <span class="inv-row-thumb"><i data-lucide="truck"></i></span>
        <span class="inv-mini-info">
          <strong>${esc(s.supplier_name || 'Sin proveedor')}</strong>
          <small>${esc(utils.formatDateTime(s.received_at))} · ${esc(s.branch_name)} · ${pluralize(s.items.length, 'ítem', 'ítems')}</small>
        </span>
        <span class="inv-mini-amount">${s.total_cost != null ? money(s.total_cost) : '—'}</span>
      </button>`).join('');

    container.querySelectorAll('.inv-mini-row').forEach((row) => {
      row.addEventListener('click', () => {
        state.selected.shipment = Number(row.dataset.shipmentId);
        setView('cargamentos');
        renderShipmentList();
      });
    });
    utils.renderIcons();
  }

  // ==========================================================================
  // Vista: Cargamentos
  // ==========================================================================
  function filteredShipments() {
    const q = state.search.shipment.trim().toLowerCase();
    if (!q) return state.shipments;
    return state.shipments.filter((s) => {
      const haystack = [
        s.supplier_name || '',
        s.branch_name,
        s.received_by_name,
        s.notes || '',
        ...s.items.map((l) => l.item_name),
      ].join(' ').toLowerCase();
      return haystack.includes(q);
    });
  }

  function renderShipmentList() {
    const rows = filteredShipments();
    const list = $('shipmentList');

    $('shipmentsCount').textContent = rows.length
      ? pluralize(rows.length, 'cargamento', 'cargamentos')
      : 'Cargamentos';
    $('shipmentsScopeLabel').textContent = state.isGlobalScope
      ? (state.branchFilter ? (state.branches.find((b) => String(b.id) === state.branchFilter)?.name || '') : 'Todas las sucursales')
      : '';

    if (!rows.length) {
      list.innerHTML = state.search.shipment
        ? emptyStateHtml('search-x', 'Sin resultados', 'Probá con otro proveedor, insumo o persona.')
        : emptyStateHtml('truck', 'Todavía no hay cargamentos', 'Registrá lo que llegó y va a quedar acá, con su detalle y su costo.');
      $('btnLoadMore').hidden = !state.shipmentsHasMore;
      renderShipmentDetail();
      utils.renderIcons();
      return;
    }

    // Si no hay nada seleccionado (o lo seleccionado se filtró), abre el primero de la lista.
    if (!rows.some((s) => s.id === state.selected.shipment)) {
      state.selected.shipment = rows[0].id;
    }

    list.innerHTML = rows.map((s) => {
      const active = s.id === state.selected.shipment ? ' active' : '';
      const sub = [utils.formatDateTime(s.received_at), state.isGlobalScope ? s.branch_name : null]
        .filter(Boolean).join(' · ');
      return `
        <button type="button" class="inv-row${active}" data-shipment-id="${s.id}">
          <span class="inv-row-thumb"><i data-lucide="truck"></i></span>
          <span class="inv-row-info">
            <strong>${esc(s.supplier_name || 'Sin proveedor')}</strong>
            <small>${esc(sub)}</small>
          </span>
          <span class="inv-badge muted">${pluralize(s.items.length, 'ítem', 'ítems')}</span>
          <span class="inv-row-amount">${s.total_cost != null ? money(s.total_cost) : '—'}</span>
        </button>`;
    }).join('');

    list.querySelectorAll('.inv-row').forEach((row) => {
      row.addEventListener('click', () => {
        state.selected.shipment = Number(row.dataset.shipmentId);
        renderShipmentList();
      });
    });

    $('btnLoadMore').hidden = !state.shipmentsHasMore;
    renderShipmentDetail();
    utils.renderIcons();
  }

  function renderShipmentDetail() {
    const detail = $('shipmentDetail');
    const s = state.shipments.find((x) => x.id === state.selected.shipment);
    if (!s) {
      detail.innerHTML = emptyStateHtml('mouse-pointer-click', 'Elegí un cargamento', 'Su detalle completo — insumos, cantidades y costos — aparece acá.');
      utils.renderIcons();
      return;
    }

    const rowsHtml = s.items.map((l) => {
      const subtotal = l.unit_cost != null ? money(Number(l.quantity) * Number(l.unit_cost)) : '—';
      return `
        <tr>
          <td>${esc(l.item_name)}</td>
          <td class="num">${esc(qty(l.quantity))} ${esc(l.unit)}</td>
          <td class="num">${l.unit_cost != null ? money(l.unit_cost) : '—'}</td>
          <td class="num">${subtotal}</td>
        </tr>`;
    }).join('');

    const footHtml = s.total_cost != null ? `
      <tfoot>
        <tr>
          <td colspan="3">Total</td>
          <td class="num">${money(s.total_cost)}</td>
        </tr>
      </tfoot>` : '';

    detail.innerHTML = `
      <div class="inv-detail-header">
        <span class="inv-detail-thumb"><i data-lucide="truck"></i></span>
        <span class="inv-badge ok">Cargamento #${s.id}</span>
      </div>
      <h3>${esc(s.supplier_name || 'Sin proveedor')}</h3>
      <p class="inv-detail-sub">${esc(utils.formatDateTime(s.received_at))} · ${esc(s.branch_name)}</p>
      ${s.notes ? `<p class="inv-detail-note">${esc(s.notes)}</p>` : ''}
      <div class="inv-metrics">
        <div><span>Ítems</span><strong>${s.items.length} <small>${s.items.length === 1 ? 'línea' : 'líneas'}</small></strong></div>
        <div><span>Total</span><strong>${s.total_cost != null ? money(s.total_cost) : '—'}</strong></div>
      </div>
      <div class="inv-detail-section-header"><span>Insumos recibidos</span></div>
      <table class="inv-detail-table">
        <thead>
          <tr><th>Insumo</th><th class="num">Cantidad</th><th class="num">Costo unit.</th><th class="num">Subtotal</th></tr>
        </thead>
        <tbody>${rowsHtml}</tbody>
        ${footHtml}
      </table>
      <div class="inv-detail-section-header"><span>Detalles</span></div>
      <div class="inv-detail-rows">
        <div><span>Sucursal</span><strong>${esc(s.branch_name)}</strong></div>
        <div><span>Registrado por</span><strong>${esc(s.received_by_name)}</strong></div>
        <div><span>Recibido el</span><strong>${esc(utils.formatDateTime(s.received_at))}</strong></div>
        <div><span>Cargado al sistema</span><strong>${esc(utils.formatDateTime(s.created_at))}</strong></div>
      </div>`;
    utils.renderIcons();
  }

  $('shipmentSearch')?.addEventListener('input', (e) => {
    state.search.shipment = e.target.value;
    renderShipmentList();
  });

  $('shipmentBranchFilter')?.addEventListener('change', (e) => {
    state.branchFilter = e.target.value;
    state.selected.shipment = null;
    loadShipments({ reset: true });
  });

  $('btnLoadMore')?.addEventListener('click', async () => {
    const btn = $('btnLoadMore');
    btn.disabled = true;
    btn.textContent = 'Cargando...';
    await loadShipments();
    btn.disabled = false;
    btn.textContent = 'Cargar más cargamentos';
  });

  // ==========================================================================
  // Vista: Insumos
  // ==========================================================================
  function renderItemList() {
    const q = state.search.item.trim().toLowerCase();
    const rows = state.items.filter((i) =>
      !q || `${i.name} ${i.unit} ${i.category || ''}`.toLowerCase().includes(q)
    );

    $('itemsCount').textContent = rows.length ? pluralize(rows.length, 'insumo', 'insumos') : 'Insumos';

    const list = $('itemList');
    if (!rows.length) {
      list.innerHTML = q
        ? emptyStateHtml('search-x', 'Sin resultados', 'Ningún insumo del catálogo coincide con esa búsqueda.')
        : emptyStateHtml('layout-list', 'Catálogo vacío', 'Creá tu primer insumo o agregalo al vuelo mientras registrás un cargamento.');
      renderItemDetail();
      utils.renderIcons();
      return;
    }

    if (!rows.some((i) => i.id === state.selected.item)) state.selected.item = rows[0].id;

    list.innerHTML = rows.map((i) => {
      const stats = itemStats(i.id);
      const active = i.id === state.selected.item ? ' active' : '';
      const badge = stats.shipments
        ? `<span class="inv-badge ok">Recibido</span>`
        : `<span class="inv-badge warn">Sin registros</span>`;
      return `
        <button type="button" class="inv-row${active}" data-item-id="${i.id}">
          <span class="inv-row-thumb"><i data-lucide="package"></i></span>
          <span class="inv-row-info">
            <strong>${esc(i.name)}</strong>
            <small>${esc(i.unit)}${i.category ? ` · ${esc(i.category)}` : ''}</small>
          </span>
          ${badge}
          <span class="inv-row-amount">${stats.shipments || ''}</span>
        </button>`;
    }).join('');

    list.querySelectorAll('.inv-row').forEach((row) => {
      row.addEventListener('click', () => {
        state.selected.item = Number(row.dataset.itemId);
        renderItemList();
      });
    });

    renderItemDetail();
    utils.renderIcons();
  }

  function renderItemDetail() {
    const detail = $('itemDetail');
    const item = state.items.find((i) => i.id === state.selected.item);
    if (!item) {
      detail.innerHTML = emptyStateHtml('mouse-pointer-click', 'Elegí un insumo', 'Cuánto entró, de quién y a qué costo aparece acá.');
      utils.renderIcons();
      return;
    }

    const stats = itemStats(item.id);
    const avgCost = stats.costedQuantity > 0 ? stats.spend / stats.costedQuantity : null;
    const suppliers = Array.from(stats.suppliers);
    const branches = Array.from(stats.branches);

    detail.innerHTML = `
      <div class="inv-detail-header">
        <span class="inv-detail-thumb"><i data-lucide="package"></i></span>
        ${stats.shipments ? '<span class="inv-badge ok">Recibido</span>' : '<span class="inv-badge warn">Sin registros</span>'}
      </div>
      <h3>${esc(item.name)}</h3>
      <p class="inv-detail-sub">Se cuenta en ${esc(item.unit)}${item.category ? ` · ${esc(item.category)}` : ''}</p>
      <div class="inv-metrics">
        <div><span>Recibido</span><strong>${esc(qty(stats.quantity))} <small>${esc(item.unit)}</small></strong></div>
        <div><span>Gasto acumulado</span><strong>${stats.spend > 0 ? money(stats.spend) : '—'}</strong></div>
        <div><span>Veces recibido</span><strong>${stats.shipments}</strong></div>
        <div><span>Costo promedio</span><strong>${avgCost != null ? money(avgCost) : '—'} <small>por ${esc(item.unit)}</small></strong></div>
      </div>
      ${suppliers.length ? `
        <div class="inv-detail-section-header"><span>Quién lo trae</span></div>
        <div class="inv-chip-row">${suppliers.map((n) => `<span class="inv-chip">${esc(n)}</span>`).join('')}</div>` : ''}
      <div class="inv-detail-section-header"><span>Detalles</span></div>
      <div class="inv-detail-rows">
        <div><span>Unidad</span><strong>${esc(item.unit)}</strong></div>
        <div><span>Categoría</span><strong>${esc(item.category || 'Sin categoría')}</strong></div>
        <div><span>Última recepción</span><strong>${stats.last ? esc(utils.formatDate(stats.last.toISOString())) : 'Nunca'}</strong></div>
        <div><span>Sucursales que lo reciben</span><strong>${branches.length ? esc(branches.join(', ')) : '—'}</strong></div>
        <div><span>Estado</span><strong>${item.active ? 'Activo' : 'Inactivo'}</strong></div>
      </div>`;
    utils.renderIcons();
  }

  $('itemSearch')?.addEventListener('input', (e) => {
    state.search.item = e.target.value;
    renderItemList();
  });

  // ==========================================================================
  // Vista: Proveedores
  // ==========================================================================
  function renderSupplierList() {
    const q = state.search.supplier.trim().toLowerCase();
    const rows = state.suppliers.filter((s) =>
      !q || `${s.name} ${s.phone || ''}`.toLowerCase().includes(q)
    );

    $('suppliersCount').textContent = rows.length ? pluralize(rows.length, 'proveedor', 'proveedores') : 'Proveedores';

    const list = $('supplierList');
    if (!rows.length) {
      list.innerHTML = q
        ? emptyStateHtml('search-x', 'Sin resultados', 'Ningún proveedor coincide con esa búsqueda.')
        : emptyStateHtml('building-2', 'Sin proveedores', 'Creá el primero o agregalo al vuelo mientras registrás un cargamento.');
      renderSupplierDetail();
      utils.renderIcons();
      return;
    }

    if (!rows.some((s) => s.id === state.selected.supplier)) state.selected.supplier = rows[0].id;

    list.innerHTML = rows.map((sup) => {
      const stats = supplierStats(sup.id);
      const active = sup.id === state.selected.supplier ? ' active' : '';
      return `
        <button type="button" class="inv-row${active}" data-supplier-id="${sup.id}">
          <span class="inv-row-thumb"><i data-lucide="building-2"></i></span>
          <span class="inv-row-info">
            <strong>${esc(sup.name)}</strong>
            <small>${esc(sup.phone || 'Sin teléfono')}</small>
          </span>
          <span class="inv-badge ${stats.shipments ? 'ok' : 'muted'}">${pluralize(stats.shipments, 'cargamento', 'cargamentos')}</span>
          <span class="inv-row-amount">${stats.spend > 0 ? money(stats.spend) : '—'}</span>
        </button>`;
    }).join('');

    list.querySelectorAll('.inv-row').forEach((row) => {
      row.addEventListener('click', () => {
        state.selected.supplier = Number(row.dataset.supplierId);
        renderSupplierList();
      });
    });

    renderSupplierDetail();
    utils.renderIcons();
  }

  function renderSupplierDetail() {
    const detail = $('supplierDetail');
    const sup = state.suppliers.find((s) => s.id === state.selected.supplier);
    if (!sup) {
      detail.innerHTML = emptyStateHtml('mouse-pointer-click', 'Elegí un proveedor', 'Cuánto te trae y cuánto te cuesta aparece acá.');
      utils.renderIcons();
      return;
    }

    const stats = supplierStats(sup.id);
    const avg = stats.shipments ? stats.spend / stats.shipments : 0;
    const items = Array.from(stats.items);

    detail.innerHTML = `
      <div class="inv-detail-header">
        <span class="inv-detail-thumb"><i data-lucide="building-2"></i></span>
        ${sup.active ? '<span class="inv-badge ok">Activo</span>' : '<span class="inv-badge muted">Inactivo</span>'}
      </div>
      <h3>${esc(sup.name)}</h3>
      <p class="inv-detail-sub">${sup.phone ? esc(sup.phone) : 'Sin teléfono registrado'}</p>
      <div class="inv-metrics">
        <div><span>Cargamentos</span><strong>${stats.shipments}</strong></div>
        <div><span>Gasto acumulado</span><strong>${stats.spend > 0 ? money(stats.spend) : '—'}</strong></div>
        <div><span>Insumos distintos</span><strong>${items.length}</strong></div>
        <div><span>Promedio por cargamento</span><strong>${avg > 0 ? money(avg) : '—'}</strong></div>
      </div>
      ${items.length ? `
        <div class="inv-detail-section-header"><span>Qué trae</span></div>
        <div class="inv-chip-row">${items.map((n) => `<span class="inv-chip">${esc(n)}</span>`).join('')}</div>` : ''}
      <div class="inv-detail-section-header"><span>Detalles</span></div>
      <div class="inv-detail-rows">
        <div><span>Teléfono</span><strong>${sup.phone ? `<a href="tel:${esc(sup.phone)}">${esc(sup.phone)}</a>` : '—'}</strong></div>
        <div><span>Último cargamento</span><strong>${stats.last ? esc(utils.formatDate(stats.last.toISOString())) : 'Nunca'}</strong></div>
        <div><span>Sucursales que atiende</span><strong>${stats.branches.size ? esc(Array.from(stats.branches).join(', ')) : '—'}</strong></div>
      </div>`;
    utils.renderIcons();
  }

  $('supplierSearch')?.addEventListener('input', (e) => {
    state.search.supplier = e.target.value;
    renderSupplierList();
  });

  // ==========================================================================
  // Modal: nuevo insumo / nuevo proveedor
  // ==========================================================================
  let pendingItemResolve = null;   // cuando el modal se abre desde el autocomplete de una línea

  function openItemModal(prefillName = '', onCreated = null) {
    pendingItemResolve = onCreated;
    $('itemError').style.display = 'none';
    $('newItemName').value = prefillName;
    $('newItemUnit').value = '';
    $('newItemCategory').value = '';
    openModal('modalItem');
    setTimeout(() => (prefillName ? $('newItemUnit') : $('newItemName')).focus(), 60);
  }

  $('btnNewItem')?.addEventListener('click', () => openItemModal());

  $('btnSaveItem')?.addEventListener('click', async () => {
    const name = $('newItemName').value.trim();
    const unit = $('newItemUnit').value.trim();
    const category = $('newItemCategory').value.trim();
    if (!name) { showModalError('itemError', 'Poné un nombre para el insumo.'); return; }
    if (!unit) { showModalError('itemError', 'Falta la unidad: cómo lo vas a contar (kg, caja, unidad...).'); return; }

    const btn = $('btnSaveItem');
    btn.disabled = true;
    btn.textContent = 'Creando...';
    try {
      const created = await api.post('/inventory/items', { name, unit, category: category || null });
      if (!state.items.some((i) => i.id === created.id)) {
        state.items.push(created);
        state.items.sort((a, b) => a.name.localeCompare(b.name));
      }
      closeModal('modalItem');
      utils.showToast(`"${created.name}" quedó en el catálogo.`, 'success');
      if (pendingItemResolve) {
        pendingItemResolve(created);
        pendingItemResolve = null;
      } else {
        state.selected.item = created.id;
        renderItemList();
      }
    } catch (err) {
      showModalError('itemError', err.message || 'No se pudo crear el insumo.');
    } finally {
      btn.disabled = false;
      btn.textContent = 'Crear insumo';
    }
  });

  let pendingSupplierResolve = null;

  function openSupplierModal(prefillName = '', onCreated = null) {
    pendingSupplierResolve = onCreated;
    $('supplierError').style.display = 'none';
    $('newSupplierName').value = prefillName;
    $('newSupplierPhone').value = '';
    openModal('modalSupplier');
    setTimeout(() => (prefillName ? $('newSupplierPhone') : $('newSupplierName')).focus(), 60);
  }

  $('btnNewSupplier')?.addEventListener('click', () => openSupplierModal());

  $('btnSaveSupplier')?.addEventListener('click', async () => {
    const name = $('newSupplierName').value.trim();
    const phone = $('newSupplierPhone').value.trim();
    if (!name) { showModalError('supplierError', 'Poné un nombre para el proveedor.'); return; }

    const btn = $('btnSaveSupplier');
    btn.disabled = true;
    btn.textContent = 'Creando...';
    try {
      const created = await api.post('/inventory/suppliers', { name, phone: phone || null });
      if (!state.suppliers.some((s) => s.id === created.id)) {
        state.suppliers.push(created);
        state.suppliers.sort((a, b) => a.name.localeCompare(b.name));
      }
      closeModal('modalSupplier');
      utils.showToast(`"${created.name}" quedó en el catálogo.`, 'success');
      if (pendingSupplierResolve) {
        pendingSupplierResolve(created);
        pendingSupplierResolve = null;
      } else {
        state.selected.supplier = created.id;
        renderSupplierList();
      }
    } catch (err) {
      showModalError('supplierError', err.message || 'No se pudo crear el proveedor.');
    } finally {
      btn.disabled = false;
      btn.textContent = 'Crear proveedor';
    }
  });

  function showModalError(id, msg) {
    const box = $(id);
    box.textContent = msg;
    box.style.display = 'block';
  }

  // ==========================================================================
  // Modal: registrar cargamento
  // ==========================================================================
  const linesContainer = $('shipmentLines');
  const lineTemplate = $('shipmentLineTemplate');
  const supplierInput = $('supplierInput');
  const supplierSuggestions = $('supplierSuggestions');

  function openShipmentModal() {
    resetShipmentForm();
    openModal('modalShipment');
  }

  function resetShipmentForm() {
    $('shipmentError').style.display = 'none';
    supplierInput.value = '';
    state.selectedSupplierId = '';
    $('notesInput').value = '';
    $('receivedAtInput').value = toLocalInputValue(new Date());
    linesContainer.innerHTML = '';
    createLineRow();
    updateShipmentTotal();
  }

  function updateShipmentTotal() {
    let total = 0;
    linesContainer.querySelectorAll('.inv-line-row').forEach((row) => {
      const qtyValue = Number(row.querySelector('.inv-line-qty').value);
      const costValue = row.querySelector('.inv-line-cost').value;
      const cell = row.querySelector('.inv-line-subtotal');
      if (qtyValue > 0 && costValue !== '') {
        const subtotal = qtyValue * Number(costValue);
        total += subtotal;
        cell.textContent = money(subtotal);
      } else {
        cell.textContent = '—';
      }
    });
    $('shipmentTotal').textContent = money(total);
  }

  /**
   * Autocomplete de insumos por fila. Sin AbortController porque api.js no expone `signal`:
   * un contador de petición por input descarta las respuestas viejas.
   */
  function createLineRow() {
    const frag = lineTemplate.content.cloneNode(true);
    const row = frag.querySelector('.inv-line-row');
    const itemInput = row.querySelector('.inv-item-input');
    const suggestBox = row.querySelector('.inv-item-suggestions');
    const unitLabel = row.querySelector('.inv-line-unit');
    const removeBtn = row.querySelector('.inv-line-remove');
    const qtyInput = row.querySelector('.inv-line-qty');
    const costInput = row.querySelector('.inv-line-cost');

    itemInput.dataset.itemId = '';
    itemInput._reqId = 0;

    function hideSuggestions() {
      suggestBox.hidden = true;
      suggestBox.innerHTML = '';
    }

    function selectItem(item) {
      itemInput.value = item.name;
      itemInput.dataset.itemId = String(item.id);
      unitLabel.textContent = item.unit ? `Se cuenta en ${item.unit}` : '';
      hideSuggestions();
      qtyInput.focus();
    }

    function renderSuggestions(results, query) {
      const trimmed = query.trim();
      const exactMatch = results.some((r) => r.name.trim().toLowerCase() === trimmed.toLowerCase());
      let html = results.map((r, i) =>
        `<button type="button" class="inv-item-suggestion" data-idx="${i}">${esc(r.name)} <small>(${esc(r.unit)})</small></button>`
      ).join('');
      if (!exactMatch) {
        html += `<button type="button" class="inv-item-suggestion inv-item-suggestion-create" data-create="1">+ Crear "${esc(trimmed)}"</button>`;
      }
      suggestBox.innerHTML = html;
      suggestBox.hidden = false;
      suggestBox.querySelectorAll('.inv-item-suggestion').forEach((btn) => {
        btn.addEventListener('click', () => {
          if (btn.dataset.create) {
            hideSuggestions();
            // Antes esto era un window.prompt() para pedir la unidad: cortaba el flujo y no
            // dejaba poner categoría. Ahora abre el mismo modal de "Nuevo insumo" y al guardar
            // vuelve a esta fila con el insumo ya elegido.
            openItemModal(trimmed, (created) => selectItem(created));
            return;
          }
          selectItem(results[Number(btn.dataset.idx)]);
        });
      });
    }

    async function fetchSuggestions(query) {
      if (!query || query.trim().length < 2) { hideSuggestions(); return; }
      const reqId = ++itemInput._reqId;
      try {
        const results = await api.get(`/inventory/items?q=${encodeURIComponent(query.trim())}`);
        if (reqId !== itemInput._reqId) return; // respuesta vieja, ya no aplica
        renderSuggestions(results, query);
      } catch (err) {
        if (reqId === itemInput._reqId) hideSuggestions();
      }
    }

    itemInput.addEventListener('input', () => {
      itemInput.dataset.itemId = '';
      unitLabel.textContent = '';
      clearTimeout(itemInput._debounce);
      const query = itemInput.value;
      itemInput._debounce = setTimeout(() => fetchSuggestions(query), 300);
    });

    qtyInput.addEventListener('input', updateShipmentTotal);
    costInput.addEventListener('input', updateShipmentTotal);

    removeBtn.addEventListener('click', () => {
      if (linesContainer.children.length > 1) {
        row.remove();
      } else {
        itemInput.value = '';
        itemInput.dataset.itemId = '';
        unitLabel.textContent = '';
        qtyInput.value = '';
        costInput.value = '';
      }
      updateShipmentTotal();
    });

    linesContainer.appendChild(row);
    utils.renderIcons();
  }

  $('btnAddLine')?.addEventListener('click', () => {
    createLineRow();
    const inputs = linesContainer.querySelectorAll('.inv-item-input');
    inputs[inputs.length - 1]?.focus();
  });

  // ---- Autocomplete de proveedor (uno por cargamento, no por línea) ----
  function hideSupplierSuggestions() {
    supplierSuggestions.hidden = true;
    supplierSuggestions.innerHTML = '';
  }

  function selectSupplier(supplier) {
    supplierInput.value = supplier.name;
    state.selectedSupplierId = String(supplier.id);
    hideSupplierSuggestions();
  }

  function renderSupplierSuggestions(results, query) {
    const trimmed = query.trim();
    const exactMatch = results.some((r) => r.name.trim().toLowerCase() === trimmed.toLowerCase());
    let html = results.map((r, i) =>
      `<button type="button" class="inv-item-suggestion" data-idx="${i}">${esc(r.name)}${r.phone ? ` <small>(${esc(r.phone)})</small>` : ''}</button>`
    ).join('');
    if (!exactMatch) {
      html += `<button type="button" class="inv-item-suggestion inv-item-suggestion-create" data-create="1">+ Crear "${esc(trimmed)}"</button>`;
    }
    supplierSuggestions.innerHTML = html;
    supplierSuggestions.hidden = false;
    supplierSuggestions.querySelectorAll('.inv-item-suggestion').forEach((btn) => {
      btn.addEventListener('click', () => {
        if (btn.dataset.create) {
          hideSupplierSuggestions();
          openSupplierModal(trimmed, (created) => selectSupplier(created));
          return;
        }
        selectSupplier(results[Number(btn.dataset.idx)]);
      });
    });
  }

  let supplierReqId = 0;
  async function fetchSupplierSuggestions(query) {
    if (!query || query.trim().length < 2) { hideSupplierSuggestions(); return; }
    const reqId = ++supplierReqId;
    try {
      const results = await api.get(`/inventory/suppliers?q=${encodeURIComponent(query.trim())}`);
      if (reqId !== supplierReqId) return;
      renderSupplierSuggestions(results, query);
    } catch (err) {
      if (reqId === supplierReqId) hideSupplierSuggestions();
    }
  }

  let supplierDebounce;
  supplierInput?.addEventListener('input', () => {
    state.selectedSupplierId = '';
    clearTimeout(supplierDebounce);
    const query = supplierInput.value;
    supplierDebounce = setTimeout(() => fetchSupplierSuggestions(query), 300);
  });

  // ---- Envío ----
  $('btnSubmitShipment')?.addEventListener('click', async () => {
    const errorBox = $('shipmentError');
    errorBox.style.display = 'none';

    const branchId = state.fixedBranchId || ($('branchSelect').value ? Number($('branchSelect').value) : null);
    if (!branchId) { showModalError('shipmentError', 'Elegí una sucursal.'); return; }

    const rows = Array.from(linesContainer.querySelectorAll('.inv-line-row'));
    const items = [];
    for (const row of rows) {
      const itemInput = row.querySelector('.inv-item-input');
      const qtyInput = row.querySelector('.inv-line-qty');
      const costInput = row.querySelector('.inv-line-cost');
      const itemId = itemInput.dataset.itemId;
      const qtyValue = qtyInput.value;
      if (!itemId && !qtyValue) continue; // fila vacía, se ignora
      if (!itemId) { showModalError('shipmentError', 'Elegí un insumo de la lista (o creá uno nuevo) en cada fila con cantidad.'); itemInput.focus(); return; }
      if (!qtyValue || Number(qtyValue) <= 0) { showModalError('shipmentError', `Falta la cantidad de "${itemInput.value}".`); qtyInput.focus(); return; }
      items.push({
        inventory_item_id: Number(itemId),
        quantity: qtyValue,
        unit_cost: costInput.value !== '' ? costInput.value : null,
      });
    }
    if (!items.length) { showModalError('shipmentError', 'Agregá al menos un insumo.'); return; }

    if (supplierInput.value.trim() && !state.selectedSupplierId) {
      showModalError('shipmentError', 'Elegí un proveedor de la lista (o creá uno nuevo), o dejá el campo vacío.');
      return;
    }

    const receivedAtValue = $('receivedAtInput').value;
    let receivedAt = null;
    if (receivedAtValue) {
      const parsed = new Date(receivedAtValue);
      if (Number.isNaN(parsed.getTime())) { showModalError('shipmentError', 'La fecha de recepción no es válida.'); return; }
      // El backend guarda en UTC (ver utils._parseServerDate): se manda con zona explícita para
      // que la hora que escribió el encargado sea la que después se muestra.
      receivedAt = parsed.toISOString();
    }

    const btn = $('btnSubmitShipment');
    btn.disabled = true;
    btn.textContent = 'Registrando...';
    try {
      await api.post('/inventory/shipments', {
        branch_id: branchId,
        received_at: receivedAt,
        supplier_id: state.selectedSupplierId ? Number(state.selectedSupplierId) : null,
        notes: $('notesInput').value.trim() || null,
        items,
      });
      closeModal('modalShipment');
      utils.showToast('Cargamento registrado.', 'success');
      state.selected.shipment = null;
      await Promise.all([loadShipments({ reset: true }), loadAnalytics()]);
      renderResumen();
      renderItemList();
      renderSupplierList();
    } catch (err) {
      showModalError('shipmentError', err.message || 'No se pudo registrar el cargamento.');
    } finally {
      btn.disabled = false;
      btn.textContent = 'Registrar cargamento';
    }
  });

  // ==========================================================================
  // Contexto de sucursal
  // ==========================================================================
  async function resolveBranchContext(user) {
    const badge = $('branchFixedBadge');
    const select = $('branchSelect');
    const filter = $('shipmentBranchFilter');

    if (user.branch_id) {
      state.fixedBranchId = user.branch_id;
      state.isGlobalScope = false;
      const branchName = user.branch ? user.branch.name : `Sucursal #${user.branch_id}`;
      badge.hidden = false;
      badge.textContent = branchName;
      select.hidden = true;
      filter.hidden = true;
      $('invScopeValue').textContent = branchName;
      $('invHeaderScope').textContent = `Cargamentos de ${branchName}`;
      return;
    }

    state.isGlobalScope = true;
    badge.hidden = true;
    select.hidden = false;
    $('invScopeValue').textContent = 'Todas las sucursales';
    $('invHeaderScope').textContent = 'Cargamentos de todas las sucursales';

    try {
      state.branches = await api.get('/branches/');
      const options = state.branches.map((b) => `<option value="${b.id}">${esc(b.name)}</option>`).join('');
      select.innerHTML = options;
      filter.innerHTML = `<option value="">Todas las sucursales</option>${options}`;
      filter.hidden = false;
    } catch (err) {
      utils.showToast('No se pudieron cargar las sucursales.', 'error');
    }
  }

  // ==========================================================================
  // Arranque
  // ==========================================================================
  const existingUser = await auth.checkSession();
  if (!existingUser) {
    window.location.href = '/';
    return;
  }

  state.user = existingUser;
  $('invGate').hidden = true;
  $('invMain').hidden = false;
  $('invAgentName').textContent = existingUser.name;
  $('invAgentRole').textContent = `${existingUser.role.toUpperCase()}${existingUser.branch ? ' • ' + existingUser.branch.name : ''}`;
  $('invAgentAvatar').textContent = utils.getInitials(existingUser.name);

  $('shipmentList').innerHTML = skeletonListHtml();
  $('itemList').innerHTML = skeletonListHtml(3);
  $('supplierList').innerHTML = skeletonListHtml(3);

  await resolveBranchContext(existingUser);
  await Promise.all([loadShipments({ reset: true }), loadAnalytics(), loadCatalogs()]);

  renderResumen();
  renderItemList();
  renderSupplierList();
  setView('resumen');
  utils.renderIcons();
});
