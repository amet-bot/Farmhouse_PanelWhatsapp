/**
 * Farmhouse - Inventario y Abastecimiento
 *
 * Rail de navegación + columna de lista + panel de detalle, con siete vistas reales — Resumen,
 * Cargamentos, Insumos, Proveedores, Merma, Existencias y Conteo. Gasto por sucursal, Lotes y
 * Reportes siguen siendo "Próximamente" en el rail, sin vista propia.
 *
 * La existencia de un insumo es lo que entró, menos lo que salió por merma, más las diferencias de
 * los conteos físicos. El servidor la calcula y la sirve en /inventory/stock; acá no se recalcula
 * nada, para que la pantalla y los reportes nunca se contradigan.
 *
 * La existencia puede ser NEGATIVA y se muestra así a propósito: pasa cuando se mermó algo que
 * entró antes de que el sistema llevara la cuenta. Se arregla con un conteo — el primero de cada
 * sucursal hace de inventario de arranque —, no bloqueando la merma.
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
    selected: { shipment: null, item: null, supplier: null, waste: null, count: null },
    search: { shipment: '', item: '', supplier: '', waste: '', stock: '', count: '', countItem: '' },
    selectedSupplierId: '',

    // ---- Merma y existencias ----
    waste: [],
    wasteOffset: 0,
    wasteHasMore: false,
    // Tanda aparte y SIN filtrar, igual que state.analytics para los cargamentos: el Resumen
    // no puede cambiar de cifras porque alguien movió el filtro de sucursal en la vista Merma.
    wasteAnalytics: [],
    wasteReasons: [],
    wasteBranchFilter: '',
    wasteReasonFilter: '',
    stock: [],
    stockBranchFilter: '',
    stockOnlyMoved: true,
    // Existencias de la sucursal elegida en el modal de merma, para poder mostrar "te quedan 4"
    // al lado de cada línea sin pedirle una consulta al servidor por cada tecla.
    wasteStock: new Map(),

    // ---- Conteo físico ----
    counts: [],
    countsOffset: 0,
    countsHasMore: false,
    countBranchFilter: '',
    // Modal: el catálogo con la existencia de la sucursal elegida, lo anotado por insumo, y si
    // es el primer conteo de esa sucursal (el de arranque).
    countStock: [],
    countEntries: new Map(),
    countIsFirst: false,
    countOnlyStocked: false,

    // ---- Invu POS ----
    // Cuando la integración está configurada, Invu es la fuente de verdad de los proveedores:
    // el panel deja de crearlos y pasa a sincronizarlos. Lo decide el servidor, no la pantalla.
    invu: { configured: false, last_synced_at: null, synced_count: 0, local_count: 0 },
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

  /** Diferencia con signo: "+2", "-3", "0". El signo es el dato, no un adorno. */
  const signedQty = (n) => {
    const num = Number(n || 0);
    if (num > 0) return `+${qty(num)}`;
    return qty(num);
  };

  const pluralize = (n, one, many) => `${n} ${n === 1 ? one : many}`;

  // Invu guarda el día de entrega como número. Su documentación no dice desde qué día cuenta,
  // así que se muestra la traducción más común (1 = lunes) y, si el número se sale del rango,
  // el número pelado en vez de inventar un día.
  const DIAS = ['lunes', 'martes', 'miércoles', 'jueves', 'viernes', 'sábado', 'domingo'];
  const diaDeEntrega = (n) => DIAS[Number(n) - 1] || `Día ${n}`;

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

  // ==========================================================================
  // Maestro-detalle en celular
  // En pantalla ancha la lista y el detalle conviven lado a lado. En celular no caben: el
  // detalle quedaba debajo de toda la lista, fuera de la pantalla, y tocar una fila no parecía
  // hacer nada. Acá la lista y el detalle pasan a ser dos pantallas que se turnan (la clase
  // `is-detail` sobre el .inv-workspace decide cuál se ve) y el detalle abre con "Volver".
  // ==========================================================================
  const isMobileLayout = () => window.matchMedia('(max-width: 900px)').matches;

  const detailBackHtml = () => `
    <button type="button" class="inv-detail-back">
      <i data-lucide="arrow-left"></i> Volver a la lista
    </button>`;

  function openDetailOnMobile(fromEl) {
    if (!isMobileLayout()) return;
    fromEl.closest('.inv-workspace')?.classList.add('is-detail');
    window.scrollTo({ top: 0 });
  }

  function closeAllMobileDetails() {
    document.querySelectorAll('.inv-workspace.is-detail').forEach((ws) => ws.classList.remove('is-detail'));
  }

  document.addEventListener('click', (e) => {
    const back = e.target.closest('.inv-detail-back');
    if (!back) return;
    back.closest('.inv-workspace')?.classList.remove('is-detail');
    window.scrollTo({ top: 0 });
  });

  // Al volver a pantalla ancha, lista y detalle vuelven a convivir: la clase ya no aplica.
  window.matchMedia('(max-width: 900px)').addEventListener('change', (ev) => {
    if (!ev.matches) closeAllMobileDetails();
  });

  /**
   * Enciende la animación de entrada de un contenedor, una sola vez.
   *
   * Se llama SOLO cuando los datos cambiaron: una carga, un filtro que volvió al servidor, un
   * cambio de vista. Nunca al repintar por otra razón — estas listas se vuelven a dibujar
   * enteras cuando alguien elige una fila, y animar ahí haría que la lista se sacuda con cada
   * clic. La clase se quita al terminar para que el próximo repintado no la arrastre.
   */
  function animarEntrada(...contenedores) {
    contenedores.forEach((el) => {
      if (!el) return;
      el.classList.remove('inv-anim');
      // Leer una propiedad de layout fuerza el reinicio de la animación: sin esto, volver a
      // poner la misma clase en el mismo cuadro no la vuelve a disparar.
      void el.offsetWidth;
      el.classList.add('inv-anim');
      clearTimeout(el._animTimeout);
      el._animTimeout = setTimeout(() => el.classList.remove('inv-anim'), 900);
    });
  }

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
  const VIEWS = {
    resumen: 'viewResumen',
    cargamentos: 'viewCargamentos',
    insumos: 'viewInsumos',
    proveedores: 'viewProveedores',
    merma: 'viewMerma',
    existencias: 'viewExistencias',
    conteo: 'viewConteo',
  };

  function setView(view) {
    if (!VIEWS[view]) return;
    state.view = view;
    closeAllMobileDetails();
    Object.entries(VIEWS).forEach(([key, id]) => { $(id).hidden = key !== view; });
    document.querySelectorAll('#invNav .inv-nav-item').forEach((btn) => {
      btn.classList.toggle('active', btn.dataset.view === view);
    });

    // Entrar a una vista es un cambio de contenido tan real como una carga: lo que hay delante
    // es otra cosa, y conviene que se vea llegar.
    animarEntrada({
      resumen: $('viewResumen'),
      cargamentos: $('shipmentList'),
      insumos: $('itemList'),
      proveedores: $('supplierList'),
      merma: $('wasteList'),
      existencias: $('stockTable'),
      conteo: $('countList'),
    }[view]);

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

  document.querySelectorAll('[data-open-waste]').forEach((btn) => {
    btn.addEventListener('click', () => openWasteModal());
  });

  document.querySelectorAll('[data-open-count]').forEach((btn) => {
    btn.addEventListener('click', () => openCountModal());
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
    animarEntrada($('shipmentList'));
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

  async function loadWasteReasons() {
    try {
      state.wasteReasons = await api.get('/inventory/waste/reasons');
    } catch (err) {
      state.wasteReasons = [];
    }
    // El filtro y el formulario se llenan desde el servidor: los motivos son vocabulario de
    // negocio y no pueden vivir duplicados en el frontend.
    const options = state.wasteReasons.map((r) => `<option value="${esc(r.code)}">${esc(r.label)}</option>`).join('');
    $('wasteReasonFilter').innerHTML = `<option value="">Todos los motivos</option>${options}`;
    $('wasteReasonSelect').innerHTML = options;
  }

  async function loadWaste({ reset = false } = {}) {
    if (reset) {
      state.waste = [];
      state.wasteOffset = 0;
      $('wasteList').innerHTML = skeletonListHtml();
    }
    const params = new URLSearchParams({ limit: String(PAGE_SIZE), offset: String(state.wasteOffset) });
    if (state.wasteBranchFilter) params.set('branch_id', state.wasteBranchFilter);
    if (state.wasteReasonFilter) params.set('reason', state.wasteReasonFilter);
    try {
      const page = await api.get(`/inventory/waste?${params.toString()}`);
      state.waste = state.waste.concat(page);
      state.wasteOffset += page.length;
      state.wasteHasMore = page.length === PAGE_SIZE;
    } catch (err) {
      utils.showToast(err.message || 'No se pudo cargar la merma.', 'error');
      state.wasteHasMore = false;
    }
    renderWasteList();
    animarEntrada($('wasteList'));
  }

  async function loadCounts({ reset = false } = {}) {
    if (reset) {
      state.counts = [];
      state.countsOffset = 0;
      $('countList').innerHTML = skeletonListHtml();
    }
    const params = new URLSearchParams({ limit: String(PAGE_SIZE), offset: String(state.countsOffset) });
    if (state.countBranchFilter) params.set('branch_id', state.countBranchFilter);
    try {
      const page = await api.get(`/inventory/counts?${params.toString()}`);
      state.counts = state.counts.concat(page);
      state.countsOffset += page.length;
      state.countsHasMore = page.length === PAGE_SIZE;
    } catch (err) {
      utils.showToast(err.message || 'No se pudieron cargar los conteos.', 'error');
      state.countsHasMore = false;
    }
    renderCountList();
    animarEntrada($('countList'));
  }

  async function loadInvuStatus() {
    try {
      state.invu = await api.get('/inventory/invu/status');
    } catch (err) {
      // Si no se puede preguntar, se asume que el panel manda: es el comportamiento de
      // siempre y el que no deja a nadie sin poder cargar un proveedor.
      state.invu = { configured: false, last_synced_at: null, synced_count: 0, local_count: 0 };
    }
    applyInvuMode();
  }

  /** Muestra el botón que corresponde y explica de dónde salen los proveedores. */
  function applyInvuMode() {
    const { configured, last_synced_at, synced_count, inactive_count, local_count } = state.invu;

    $('btnNewSupplier').hidden = configured;
    $('btnSyncInvu').hidden = !configured;

    const note = $('invuNote');
    if (!configured) {
      note.hidden = true;
      return;
    }

    // La fecha va seguida de "·" y no de un punto: `formatDateTime` ya devuelve "10:31 a. m.",
    // que termina en punto, y encadenarle otro dejaba "a. m..".
    const cuando = last_synced_at
      ? `Última sincronización el ${utils.formatDateTime(last_synced_at)}`
      : 'Todavía no se sincronizó ninguna vez';

    const detalle = [`${synced_count} de esta lista ${synced_count === 1 ? 'viene' : 'vienen'} de Invu`];
    if (inactive_count) {
      detalle.push(`${inactive_count} más ${inactive_count === 1 ? 'está inactivo' : 'están inactivos'} allá y no se ${inactive_count === 1 ? 'muestra' : 'muestran'}`);
    }
    if (local_count) {
      detalle.push(`${pluralize(local_count, 'proveedor', 'proveedores')} se cargó a mano y no está en Invu`);
    }

    note.hidden = false;
    note.querySelector('span').textContent =
      `Los proveedores se dan de alta en Invu y se sincronizan solos una vez al día. ` +
      `${cuando} · ${detalle.join('; ')}.`;
    utils.renderIcons();
  }

  $('btnSyncInvu')?.addEventListener('click', async () => {
    const btn = $('btnSyncInvu');
    btn.disabled = true;
    btn.innerHTML = '<i data-lucide="refresh-cw"></i> Sincronizando...';
    utils.renderIcons();
    try {
      const r = await api.post('/inventory/invu/sync-suppliers', {});
      const partes = [];
      if (r.created) partes.push(`${r.created} nuevo${r.created === 1 ? '' : 's'}`);
      if (r.linked) partes.push(`${r.linked} emparejado${r.linked === 1 ? '' : 's'} con los que ya estaban`);
      if (r.updated) partes.push(`${r.updated} actualizado${r.updated === 1 ? '' : 's'}`);
      utils.showToast(
        partes.length
          ? `Invu devolvió ${r.received}: ${partes.join(', ')}.`
          : `Invu devolvió ${r.received} proveedores; no había nada que cambiar.`,
        'success'
      );
      await Promise.all([loadCatalogs(), loadInvuStatus()]);
      renderSupplierList();
    } catch (err) {
      utils.showToast(err.message || 'No se pudo sincronizar con Invu.', 'error');
    } finally {
      btn.disabled = false;
      btn.innerHTML = '<i data-lucide="refresh-cw"></i> Sincronizar con Invu';
      utils.renderIcons();
    }
  });

  async function loadWasteAnalytics() {
    try {
      state.wasteAnalytics = await api.get(`/inventory/waste?limit=${ANALYTICS_SIZE}`);
    } catch (err) {
      state.wasteAnalytics = [];
    }
  }

  async function loadStock() {
    const params = new URLSearchParams();
    if (state.stockBranchFilter) params.set('branch_id', state.stockBranchFilter);
    if (state.stockOnlyMoved) params.set('only_stocked', 'true');
    try {
      state.stock = await api.get(`/inventory/stock?${params.toString()}`);
    } catch (err) {
      state.stock = [];
      utils.showToast(err.message || 'No se pudieron cargar las existencias.', 'error');
    }
    renderStockTable();
    animarEntrada($('stockTable'));
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
  /**
   * Merma de la ventana reciente. Se mira `occurred_at` y no `created_at`: lo que importa es
   * cuándo se perdió, no cuándo alguien se acordó de cargarlo.
   */
  function recentWasteStats() {
    const desde = daysAgoIso(RECENT_DAYS);
    const rows = state.wasteAnalytics.filter((w) => {
      const fecha = utils._parseServerDate(w.occurred_at);
      return fecha && fecha >= desde;
    });
    const conCosto = rows.filter((w) => w.total_cost != null);
    return {
      rows,
      count: rows.length,
      total: conCosto.length ? conCosto.reduce((acc, w) => acc + Number(w.total_cost), 0) : null,
    };
  }

  function renderResumen() {
    const recent = recentShipments();
    const recentWaste = recentWasteStats();
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
      {
        icon: 'trending-down',
        label: `Merma (${RECENT_DAYS} días)`,
        value: recentWaste.total != null ? money(recentWaste.total) : '—',
        sub: recentWaste.count
          ? `${pluralize(recentWaste.count, 'registro', 'registros')}${recentWaste.total == null ? ', sin costo conocido' : ''}`
          : 'Sin mermas registradas',
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
    renderWasteReasonBars(recentWaste.rows);
    // renderResumen solo corre al cargar y después de registrar algo, nunca por un clic suelto:
    // acá animar siempre es correcto.
    animarEntrada($('viewResumen'), $('recentShipments'));
    utils.renderIcons();
  }

  /**
   * Merma por motivo. Es la pregunta que justifica el módulo: no "cuánto se perdió" sino "por
   * qué", que es lo único sobre lo que se puede hacer algo. Se ordena por plata perdida cuando
   * hay costos, y por cantidad de episodios cuando todavía no.
   */
  function renderWasteReasonBars(rows) {
    const panel = $('wasteReasonPanel');
    if (!panel) return;
    if (!rows.length) {
      panel.hidden = true;
      return;
    }
    panel.hidden = false;

    const porMotivo = new Map();
    rows.forEach((w) => {
      const entry = porMotivo.get(w.reason) || { label: w.reason_label, costo: 0, veces: 0, conCosto: false };
      entry.veces += 1;
      if (w.total_cost != null) {
        entry.costo += Number(w.total_cost);
        entry.conCosto = true;
      }
      porMotivo.set(w.reason, entry);
    });

    const hayCostos = Array.from(porMotivo.values()).some((e) => e.conCosto);
    const lista = Array.from(porMotivo.values())
      .sort((a, b) => (hayCostos ? b.costo - a.costo : b.veces - a.veces))
      .slice(0, 8);
    const tope = Math.max(...lista.map((e) => (hayCostos ? e.costo : e.veces)), 1);

    $('wasteReasonNote').textContent = hayCostos ? `Últimos ${RECENT_DAYS} días, por costo` : `Últimos ${RECENT_DAYS} días`;
    $('wasteReasonBars').innerHTML = lista.map((e) => {
      const valor = hayCostos ? e.costo : e.veces;
      return `
        <div class="inv-bar-row inv-bar-row-waste">
          <span class="inv-bar-name">${esc(e.label)}</span>
          <span class="inv-bar-value">${hayCostos ? money(e.costo) : pluralize(e.veces, 'vez', 'veces')}</span>
          <span class="inv-bar-track"><span class="inv-bar-fill inv-bar-fill-waste" style="width:${Math.max(4, (valor / tope) * 100)}%"></span></span>
        </div>`;
    }).join('');
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
        openDetailOnMobile(document.getElementById('shipmentList'));
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
        // `row` ya quedó desprendido del DOM al re-renderizar la lista: closest() sobre él
        // devuelve null. Se usa el contenedor, que sigue vivo dentro del .inv-workspace.
        openDetailOnMobile($('shipmentList'));
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
          <td class="inv-td-name" data-label="Insumo">${esc(l.item_name)}</td>
          <td class="num" data-label="Cantidad">${esc(qty(l.quantity))} ${esc(l.unit)}</td>
          <td class="num" data-label="Costo unit.">${l.unit_cost != null ? money(l.unit_cost) : '—'}</td>
          <td class="num" data-label="Subtotal">${subtotal}</td>
        </tr>`;
    }).join('');

    const footHtml = s.total_cost != null ? `
      <tfoot>
        <tr>
          <td colspan="3" class="inv-td-total-label">Total</td>
          <td class="num" data-label="Total">${money(s.total_cost)}</td>
        </tr>
      </tfoot>` : '';

    detail.innerHTML = `
      ${detailBackHtml()}
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
        openDetailOnMobile($('itemList'));
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
      ${detailBackHtml()}
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
      !q || `${s.name} ${s.phone || ''} ${s.tax_id || ''} ${s.contact_name || ''} ${s.code || ''}`.toLowerCase().includes(q)
    );

    $('suppliersCount').textContent = rows.length ? pluralize(rows.length, 'proveedor', 'proveedores') : 'Proveedores';

    const list = $('supplierList');
    if (!rows.length) {
      list.innerHTML = q
        ? emptyStateHtml('search-x', 'Sin resultados', 'Ningún proveedor coincide con esa búsqueda.')
        : (state.invu.configured
            ? emptyStateHtml('building-2', 'Sin proveedores', 'Se cargan en Invu. Apretá "Sincronizar con Invu" para traerlos.')
            : emptyStateHtml('building-2', 'Sin proveedores', 'Creá el primero o agregalo al vuelo mientras registrás un cargamento.'));
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
            <small>${esc(sup.tax_id || sup.phone || 'Sin teléfono')}</small>
          </span>
          ${sup.invu_id ? '<span class="inv-badge invu" title="Viene de Invu">Invu</span>' : ''}
          <span class="inv-badge ${stats.shipments ? 'ok' : 'muted'}">${pluralize(stats.shipments, 'cargamento', 'cargamentos')}</span>
          <span class="inv-row-amount">${stats.spend > 0 ? money(stats.spend) : '—'}</span>
        </button>`;
    }).join('');

    list.querySelectorAll('.inv-row').forEach((row) => {
      row.addEventListener('click', () => {
        state.selected.supplier = Number(row.dataset.supplierId);
        renderSupplierList();
        openDetailOnMobile($('supplierList'));
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
      ${detailBackHtml()}
      <div class="inv-detail-header">
        <span class="inv-detail-thumb"><i data-lucide="building-2"></i></span>
        ${sup.invu_id ? '<span class="inv-badge invu">Invu</span>' : ''}
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
        ${sup.tax_id ? `<div><span>RUC</span><strong>${esc(sup.tax_id)}</strong></div>` : ''}
        ${sup.contact_name ? `<div><span>Contacto</span><strong>${esc(sup.contact_name)}</strong></div>` : ''}
        ${sup.email ? `<div><span>Correo</span><strong><a href="mailto:${esc(sup.email)}">${esc(sup.email)}</a></strong></div>` : ''}
        ${sup.delivery_day ? `<div><span>Día de entrega</span><strong>${esc(diaDeEntrega(sup.delivery_day))}</strong></div>` : ''}
        <div><span>Último cargamento</span><strong>${stats.last ? esc(utils.formatDate(stats.last.toISOString())) : 'Nunca'}</strong></div>
        <div><span>Sucursales que atiende</span><strong>${stats.branches.size ? esc(Array.from(stats.branches).join(', ')) : '—'}</strong></div>
        ${sup.invu_id
          ? `<div><span>Origen</span><strong>Invu${sup.code ? ` · ${esc(sup.code)}` : ''}${sup.synced_at ? ` · sincronizado ${esc(utils.formatDateTime(sup.synced_at))}` : ''}</strong></div>`
          : (state.invu.configured ? '<div><span>Origen</span><strong>Cargado en el panel, no está en Invu</strong></div>' : '')}
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
    // Con Invu configurado no se ofrece crear: el proveedor se da de alta allá. Si no está en
    // la lista es que falta cargarlo o sincronizar, y decirlo es más útil que abrir un
    // formulario cuyo guardado el servidor va a rechazar.
    if (!exactMatch && !state.invu.configured) {
      html += `<button type="button" class="inv-item-suggestion inv-item-suggestion-create" data-create="1">+ Crear "${esc(trimmed)}"</button>`;
    }
    if (!results.length && state.invu.configured) {
      html = '<div class="inv-item-suggestion-empty">No está en Invu. Cargalo allá y sincronizá desde Proveedores.</div>';
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
  // Vista: Merma
  // ==========================================================================
  function filteredWaste() {
    const q = state.search.waste.trim().toLowerCase();
    if (!q) return state.waste;
    return state.waste.filter((w) => {
      const heno = [
        w.reason_label,
        w.recorded_by_name,
        w.branch_name,
        w.notes || '',
        ...w.items.map((l) => l.item_name),
      ].join(' ').toLowerCase();
      return heno.includes(q);
    });
  }

  const WASTE_ICONS = {
    vencido: 'calendar-x',
    danado: 'package-x',
    error_preparacion: 'chef-hat',
    derrame: 'droplets',
    devolucion: 'undo-2',
    consumo_interno: 'utensils',
    faltante: 'search-x',
    otro: 'circle-help',
  };

  const wasteIcon = (reason) => WASTE_ICONS[reason] || 'trending-down';

  /** Cuántas unidades salieron en un registro, resumido para la fila de la lista. */
  function wasteQuantityLabel(w) {
    if (w.items.length === 1) {
      const line = w.items[0];
      return `${qty(line.quantity)} ${line.unit}`;
    }
    return pluralize(w.items.length, 'insumo', 'insumos');
  }

  function renderWasteList() {
    const rows = filteredWaste();
    const list = $('wasteList');

    $('wasteCount').textContent = rows.length ? pluralize(rows.length, 'merma', 'mermas') : 'Mermas';
    $('wasteScopeLabel').textContent = state.isGlobalScope
      ? (state.wasteBranchFilter
          ? (state.branches.find((b) => String(b.id) === state.wasteBranchFilter)?.name || '')
          : 'Todas las sucursales')
      : '';

    if (!rows.length) {
      list.innerHTML = (state.search.waste || state.wasteReasonFilter)
        ? emptyStateHtml('search-x', 'Sin resultados', 'Probá con otro motivo, insumo o persona.')
        : emptyStateHtml('trending-down', 'Todavía no hay mermas', 'Registrá lo que se perdió y acá queda el historial, con su motivo y su costo.');
      $('btnLoadMoreWaste').hidden = !state.wasteHasMore;
      renderWasteDetail();
      utils.renderIcons();
      return;
    }

    if (!rows.some((w) => w.id === state.selected.waste)) {
      state.selected.waste = rows[0].id;
    }

    list.innerHTML = rows.map((w) => {
      const active = w.id === state.selected.waste ? ' active' : '';
      const sub = [utils.formatDateTime(w.occurred_at), state.isGlobalScope ? w.branch_name : null]
        .filter(Boolean).join(' · ');
      return `
        <button type="button" class="inv-row${active}" data-waste-id="${w.id}">
          <span class="inv-row-thumb inv-row-thumb-waste"><i data-lucide="${wasteIcon(w.reason)}"></i></span>
          <span class="inv-row-info">
            <strong>${esc(w.reason_label)}</strong>
            <small>${esc(sub)}</small>
          </span>
          <span class="inv-badge muted">${esc(wasteQuantityLabel(w))}</span>
          <span class="inv-row-amount inv-row-amount-waste">${w.total_cost != null ? `-${money(w.total_cost)}` : '—'}</span>
        </button>`;
    }).join('');

    list.querySelectorAll('.inv-row').forEach((row) => {
      row.addEventListener('click', () => {
        state.selected.waste = Number(row.dataset.wasteId);
        renderWasteList();
        openDetailOnMobile($('wasteList'));
      });
    });

    $('btnLoadMoreWaste').hidden = !state.wasteHasMore;
    renderWasteDetail();
    utils.renderIcons();
  }

  function renderWasteDetail() {
    const detail = $('wasteDetail');
    const w = state.waste.find((x) => x.id === state.selected.waste);
    if (!w) {
      detail.innerHTML = emptyStateHtml('mouse-pointer-click', 'Elegí una merma', 'Su detalle — insumos, cantidades y pérdida — aparece acá.');
      utils.renderIcons();
      return;
    }

    const rowsHtml = w.items.map((l) => {
      const subtotal = l.unit_cost != null ? money(Number(l.quantity) * Number(l.unit_cost)) : '—';
      return `
        <tr>
          <td class="inv-td-name" data-label="Insumo">${esc(l.item_name)}</td>
          <td class="num" data-label="Cantidad">${esc(qty(l.quantity))} ${esc(l.unit)}</td>
          <td class="num" data-label="Costo unit.">${l.unit_cost != null ? money(l.unit_cost) : '—'}</td>
          <td class="num" data-label="Pérdida">${subtotal}</td>
        </tr>`;
    }).join('');

    const footHtml = w.total_cost != null ? `
      <tfoot>
        <tr>
          <td colspan="3" class="inv-td-total-label">Pérdida total</td>
          <td class="num" data-label="Pérdida total">${money(w.total_cost)}</td>
        </tr>
      </tfoot>` : '';

    detail.innerHTML = `
      ${detailBackHtml()}
      <div class="inv-detail-header">
        <span class="inv-detail-thumb inv-detail-thumb-waste"><i data-lucide="${wasteIcon(w.reason)}"></i></span>
        <span class="inv-badge warn">Merma #${w.id}</span>
      </div>
      <h3>${esc(w.reason_label)}</h3>
      <p class="inv-detail-sub">${esc(utils.formatDateTime(w.occurred_at))} · ${esc(w.branch_name)}</p>
      ${w.notes ? `<p class="inv-detail-note">${esc(w.notes)}</p>` : ''}
      <div class="inv-metrics">
        <div><span>Ítems</span><strong>${w.items.length} <small>${w.items.length === 1 ? 'línea' : 'líneas'}</small></strong></div>
        <div><span>Pérdida</span><strong>${w.total_cost != null ? money(w.total_cost) : '—'}</strong></div>
      </div>
      <div class="inv-detail-section-header"><span>Insumos perdidos</span></div>
      <table class="inv-detail-table">
        <thead>
          <tr><th>Insumo</th><th class="num">Cantidad</th><th class="num">Costo unit.</th><th class="num">Pérdida</th></tr>
        </thead>
        <tbody>${rowsHtml}</tbody>
        ${footHtml}
      </table>
      <div class="inv-detail-section-header"><span>Detalles</span></div>
      <div class="inv-detail-rows">
        <div><span>Sucursal</span><strong>${esc(w.branch_name)}</strong></div>
        <div><span>Registrado por</span><strong>${esc(w.recorded_by_name)}</strong></div>
        <div><span>Ocurrió el</span><strong>${esc(utils.formatDateTime(w.occurred_at))}</strong></div>
        <div><span>Cargado al sistema</span><strong>${esc(utils.formatDateTime(w.created_at))}</strong></div>
      </div>`;
    utils.renderIcons();
  }

  $('wasteSearch')?.addEventListener('input', (e) => {
    state.search.waste = e.target.value;
    renderWasteList();
  });

  $('wasteReasonFilter')?.addEventListener('change', (e) => {
    state.wasteReasonFilter = e.target.value;
    state.selected.waste = null;
    loadWaste({ reset: true });
  });

  $('wasteBranchFilter')?.addEventListener('change', (e) => {
    state.wasteBranchFilter = e.target.value;
    state.selected.waste = null;
    loadWaste({ reset: true });
  });

  $('btnLoadMoreWaste')?.addEventListener('click', async () => {
    const btn = $('btnLoadMoreWaste');
    btn.disabled = true;
    btn.textContent = 'Cargando...';
    await loadWaste();
    btn.disabled = false;
    btn.textContent = 'Cargar más mermas';
  });

  // ==========================================================================
  // Vista: Existencias
  // ==========================================================================
  function renderStockTable() {
    const q = state.search.stock.trim().toLowerCase();
    const rows = q
      ? state.stock.filter((r) => `${r.item_name} ${r.category || ''} ${r.unit}`.toLowerCase().includes(q))
      : state.stock;

    $('stockCount').textContent = rows.length ? pluralize(rows.length, 'insumo', 'insumos') : 'Insumos';
    $('stockScopeLabel').textContent = state.isGlobalScope
      ? (state.stockBranchFilter
          ? (state.branches.find((b) => String(b.id) === state.stockBranchFilter)?.name || '')
          : 'Sumando todas las sucursales')
      : (state.user.branch ? state.user.branch.name : '');

    const negativos = rows.filter((r) => Number(r.on_hand) < 0);
    const note = $('stockNote');
    if (negativos.length) {
      note.hidden = false;
      note.querySelector('span').textContent =
        `${pluralize(negativos.length, 'insumo aparece', 'insumos aparecen')} en negativo. ` +
        'Pasa cuando se mermó algo que entró antes de que el sistema llevara la cuenta: ' +
        'se arregla contando lo que hay en el estante.';
    } else {
      note.hidden = true;
    }

    const table = $('stockTable');
    if (!rows.length) {
      table.innerHTML = q
        ? emptyStateHtml('search-x', 'Sin resultados', 'Probá con otro insumo o categoría.')
        : emptyStateHtml('boxes', 'Todavía no hay movimientos', 'En cuanto registres un cargamento o un conteo, las existencias aparecen acá.');
      utils.renderIcons();
      return;
    }

    table.innerHTML = `
      <table class="inv-detail-table inv-stock-grid">
        <thead>
          <tr>
            <th>Insumo</th>
            <th class="num">Entró</th>
            <th class="num">Merma</th>
            <th class="num">Conteo</th>
            <th class="num">Queda</th>
            <th class="num">Perdido</th>
          </tr>
        </thead>
        <tbody>
          ${rows.map((r) => {
            const queda = Number(r.on_hand);
            const clase = queda < 0 ? ' inv-stock-negative' : (queda === 0 ? ' inv-stock-zero' : '');
            return `
              <tr>
                <td class="inv-td-name" data-label="Insumo">
                  ${esc(r.item_name)}
                  <small>${esc(r.category || 'Sin categoría')} · se cuenta en ${esc(r.unit)}</small>
                </td>
                <td class="num" data-label="Entró">${esc(qty(r.entered))}</td>
                <td class="num" data-label="Merma">${Number(r.wasted) ? esc(qty(r.wasted)) : '—'}</td>
                <td class="num" data-label="Conteo" title="${r.last_counted_at ? `Último conteo: ${esc(utils.formatDateTime(r.last_counted_at))}` : 'Nunca se contó'}">${Number(r.adjusted) ? esc(signedQty(r.adjusted)) : '—'}</td>
                <td class="num inv-stock-onhand" data-label="Queda"><span class="inv-stock-pill${clase}">${esc(qty(r.on_hand))} <small>${esc(r.unit)}</small></span></td>
                <td class="num" data-label="Perdido">${r.wasted_cost != null ? money(r.wasted_cost) : '—'}</td>
              </tr>`;
          }).join('')}
        </tbody>
      </table>`;
    utils.renderIcons();
  }

  $('stockSearch')?.addEventListener('input', (e) => {
    state.search.stock = e.target.value;
    renderStockTable();
  });

  $('stockBranchFilter')?.addEventListener('change', (e) => {
    state.stockBranchFilter = e.target.value;
    loadStock();
  });

  $('stockOnlyMoved')?.addEventListener('change', (e) => {
    state.stockOnlyMoved = e.target.checked;
    loadStock();
  });

  // ==========================================================================
  // Modal: registrar merma
  // ==========================================================================
  const wasteLinesContainer = $('wasteLines');
  const wasteLineTemplate = $('wasteLineTemplate');

  function wasteModalBranchId() {
    return state.fixedBranchId || ($('wasteBranchSelect').value ? Number($('wasteBranchSelect').value) : null);
  }

  /**
   * Trae las existencias de la sucursal elegida una sola vez al abrir el modal (y al cambiar de
   * sucursal), en vez de consultar por cada insumo que se elige: son pocas filas y así la cifra
   * aparece al instante al lado de la línea.
   */
  async function loadWasteStock() {
    state.wasteStock = new Map();
    const branchId = wasteModalBranchId();
    if (!branchId) return;
    try {
      const rows = await api.get(`/inventory/stock?branch_id=${branchId}&only_stocked=true`);
      rows.forEach((r) => state.wasteStock.set(r.inventory_item_id, r));
    } catch (err) {
      // Sin existencias no se bloquea nada: la línea simplemente no muestra el "te quedan".
    }
    wasteLinesContainer.querySelectorAll('.inv-line-row').forEach(refreshWasteLineStock);
    updateWasteTotal();
  }

  function refreshWasteLineStock(row) {
    const itemInput = row.querySelector('.inv-item-input');
    const qtyInput = row.querySelector('.inv-line-qty');
    const cell = row.querySelector('.inv-line-stock');
    const itemId = Number(itemInput.dataset.itemId || 0);

    cell.classList.remove('is-short');
    if (!itemId) { cell.textContent = '—'; return; }

    const fila = state.wasteStock.get(itemId);
    if (!fila) {
      cell.textContent = 'Sin registro';
      return;
    }
    const disponible = Number(fila.on_hand);
    cell.textContent = `${qty(disponible)} ${fila.unit}`;
    const pedido = Number(qtyInput.value || 0);
    if (pedido > disponible) cell.classList.add('is-short');
  }

  /** Refresca la existencia de una fila y, con ella, la pérdida estimada del formulario. */
  function refreshWasteLine(row) {
    refreshWasteLineStock(row);
    updateWasteTotal();
  }

  /**
   * Estimación de la pérdida con el último costo conocido de cada insumo en esa sucursal.
   *
   * Es una vista previa, no la cifra final: el servidor vuelve a mirar el último cargamento al
   * grabar, y entre que se abrió el formulario y se guardó puede haber entrado uno nuevo. Un
   * insumo sin costo cargado nunca suma, y se dice cuántos quedaron afuera para que el total no
   * parezca completo cuando no lo está.
   */
  function updateWasteTotal() {
    let total = 0;
    let conCosto = 0;
    let sinCosto = 0;
    wasteLinesContainer.querySelectorAll('.inv-line-row').forEach((row) => {
      const itemId = Number(row.querySelector('.inv-item-input').dataset.itemId || 0);
      const cantidad = Number(row.querySelector('.inv-line-qty').value || 0);
      if (!itemId || cantidad <= 0) return;
      const fila = state.wasteStock.get(itemId);
      if (fila && fila.last_unit_cost != null) {
        total += cantidad * Number(fila.last_unit_cost);
        conCosto += 1;
      } else {
        sinCosto += 1;
      }
    });

    $('wasteTotal').textContent = conCosto ? money(total) : '—';
    const nota = document.querySelector('#modalWaste .inv-total-box small');
    if (nota) {
      nota.textContent = sinCosto
        ? `${pluralize(sinCosto, 'insumo', 'insumos')} sin costo conocido, no suma${sinCosto === 1 ? '' : 'n'}`
        : 'Al costo del último cargamento';
    }
  }

  function createWasteLineRow() {
    const frag = wasteLineTemplate.content.cloneNode(true);
    const row = frag.querySelector('.inv-line-row');
    const itemInput = row.querySelector('.inv-item-input');
    const suggestBox = row.querySelector('.inv-item-suggestions');
    const unitLabel = row.querySelector('.inv-line-unit');
    const removeBtn = row.querySelector('.inv-line-remove');
    const qtyInput = row.querySelector('.inv-line-qty');

    itemInput.dataset.itemId = '';
    itemInput._reqId = 0;

    const hideSuggestions = () => { suggestBox.hidden = true; suggestBox.innerHTML = ''; };

    function selectItem(item) {
      itemInput.value = item.name;
      itemInput.dataset.itemId = String(item.id);
      unitLabel.textContent = item.unit ? `Se cuenta en ${item.unit}` : '';
      hideSuggestions();
      refreshWasteLine(row);
      qtyInput.focus();
    }

    function renderSuggestions(results) {
      // Sin "+ Crear": una merma es de algo que ya existía. Si el insumo no está en el catálogo,
      // tampoco entró nunca, y registrar su pérdida sería inventar un movimiento.
      if (!results.length) {
        suggestBox.innerHTML = '<div class="inv-item-suggestion-empty">Ese insumo no está en el catálogo.</div>';
        suggestBox.hidden = false;
        return;
      }
      suggestBox.innerHTML = results.map((r, i) =>
        `<button type="button" class="inv-item-suggestion" data-idx="${i}">${esc(r.name)} <small>(${esc(r.unit)})</small></button>`
      ).join('');
      suggestBox.hidden = false;
      suggestBox.querySelectorAll('.inv-item-suggestion').forEach((btn) => {
        btn.addEventListener('click', () => selectItem(results[Number(btn.dataset.idx)]));
      });
    }

    async function fetchSuggestions(query) {
      if (!query || query.trim().length < 2) { hideSuggestions(); return; }
      const reqId = ++itemInput._reqId;
      try {
        const results = await api.get(`/inventory/items?q=${encodeURIComponent(query.trim())}`);
        if (reqId !== itemInput._reqId) return;
        renderSuggestions(results);
      } catch (err) {
        if (reqId === itemInput._reqId) hideSuggestions();
      }
    }

    itemInput.addEventListener('input', () => {
      itemInput.dataset.itemId = '';
      unitLabel.textContent = '';
      refreshWasteLine(row);
      clearTimeout(itemInput._debounce);
      const query = itemInput.value;
      itemInput._debounce = setTimeout(() => fetchSuggestions(query), 300);
    });

    qtyInput.addEventListener('input', () => refreshWasteLine(row));

    removeBtn.addEventListener('click', () => {
      if (wasteLinesContainer.children.length > 1) {
        row.remove();
      } else {
        itemInput.value = '';
        itemInput.dataset.itemId = '';
        unitLabel.textContent = '';
        qtyInput.value = '';
      }
      refreshWasteLine(row);
    });

    wasteLinesContainer.appendChild(row);
    utils.renderIcons();
  }

  $('btnAddWasteLine')?.addEventListener('click', () => {
    createWasteLineRow();
    const inputs = wasteLinesContainer.querySelectorAll('.inv-item-input');
    inputs[inputs.length - 1]?.focus();
  });

  $('wasteBranchSelect')?.addEventListener('change', loadWasteStock);

  function openWasteModal() {
    $('wasteError').style.display = 'none';
    $('wasteNotes').value = '';
    $('wasteOccurredAt').value = toLocalInputValue(new Date());
    if (state.wasteReasons.length) $('wasteReasonSelect').value = state.wasteReasons[0].code;
    wasteLinesContainer.innerHTML = '';
    createWasteLineRow();
    $('wasteTotal').textContent = '—';
    openModal('modalWaste');
    loadWasteStock();
  }

  $('btnSubmitWaste')?.addEventListener('click', async () => {
    $('wasteError').style.display = 'none';

    const branchId = wasteModalBranchId();
    if (!branchId) { showModalError('wasteError', 'Elegí una sucursal.'); return; }

    const reason = $('wasteReasonSelect').value;
    if (!reason) { showModalError('wasteError', 'Elegí un motivo.'); return; }

    const rows = Array.from(wasteLinesContainer.querySelectorAll('.inv-line-row'));
    const items = [];
    for (const row of rows) {
      const itemInput = row.querySelector('.inv-item-input');
      const qtyInput = row.querySelector('.inv-line-qty');
      const itemId = itemInput.dataset.itemId;
      const qtyValue = qtyInput.value;
      if (!itemId && !qtyValue) continue;
      if (!itemId) { showModalError('wasteError', 'Elegí un insumo de la lista en cada fila con cantidad.'); itemInput.focus(); return; }
      if (!qtyValue || Number(qtyValue) <= 0) { showModalError('wasteError', `Falta la cantidad de "${itemInput.value}".`); qtyInput.focus(); return; }
      items.push({ inventory_item_id: Number(itemId), quantity: qtyValue });
    }
    if (!items.length) { showModalError('wasteError', 'Agregá al menos un insumo.'); return; }

    const occurredValue = $('wasteOccurredAt').value;
    let occurredAt = null;
    if (occurredValue) {
      const parsed = new Date(occurredValue);
      if (Number.isNaN(parsed.getTime())) { showModalError('wasteError', 'La fecha no es válida.'); return; }
      occurredAt = parsed.toISOString();
    }

    const btn = $('btnSubmitWaste');
    btn.disabled = true;
    btn.textContent = 'Registrando...';
    try {
      const creada = await api.post('/inventory/waste', {
        branch_id: branchId,
        reason,
        occurred_at: occurredAt,
        notes: $('wasteNotes').value.trim() || null,
        items,
      });
      closeModal('modalWaste');

      // El servidor avisa qué quedó en negativo. No es un error: es que falta cargar el
      // inventario de arranque, y conviene decirlo con esas palabras.
      if (creada.negative_items && creada.negative_items.length) {
        utils.showToast(
          `Merma registrada. ${creada.negative_items.join(', ')} ${creada.negative_items.length === 1 ? 'queda' : 'quedan'} en negativo: hacé un conteo para cargar lo que hay.`,
          'warning'
        );
      } else {
        utils.showToast('Merma registrada.', 'success');
      }

      state.selected.waste = null;
      await Promise.all([loadWaste({ reset: true }), loadWasteAnalytics(), loadStock()]);
      renderResumen();
    } catch (err) {
      showModalError('wasteError', err.message || 'No se pudo registrar la merma.');
    } finally {
      btn.disabled = false;
      btn.textContent = 'Registrar merma';
    }
  });

  // ==========================================================================
  // Vista: Conteo
  // ==========================================================================
  const countTitle = (c) => (c.is_first_count ? 'Conteo de arranque' : 'Conteo físico');

  function filteredCounts() {
    const q = state.search.count.trim().toLowerCase();
    if (!q) return state.counts;
    return state.counts.filter((c) => {
      const heno = [
        countTitle(c),
        c.counted_by_name,
        c.branch_name,
        c.notes || '',
        ...c.items.map((l) => l.item_name),
      ].join(' ').toLowerCase();
      return heno.includes(q);
    });
  }

  /**
   * Cómo se muestra la diferencia valuada de un conteo. En el de arranque va neutra: ahí la
   * diferencia no es un faltante ni un sobrante, es lo que ya había, y pintarla de rojo o de
   * verde contaría una historia que no pasó.
   */
  function countAmountHtml(c) {
    if (c.difference_cost == null) return '<span class="inv-row-amount">—</span>';
    const valor = Number(c.difference_cost);
    if (c.is_first_count) return `<span class="inv-row-amount inv-row-amount-count">${money(Math.abs(valor))}</span>`;
    const clase = valor < 0 ? ' inv-row-amount-waste' : '';
    return `<span class="inv-row-amount${clase}">${valor < 0 ? '-' : '+'}${money(Math.abs(valor))}</span>`;
  }

  function renderCountList() {
    const rows = filteredCounts();
    const list = $('countList');

    $('countCount').textContent = rows.length ? pluralize(rows.length, 'conteo', 'conteos') : 'Conteos';
    $('countScopeLabel').textContent = state.isGlobalScope
      ? (state.countBranchFilter
          ? (state.branches.find((b) => String(b.id) === state.countBranchFilter)?.name || '')
          : 'Todas las sucursales')
      : '';

    if (!rows.length) {
      list.innerHTML = state.search.count
        ? emptyStateHtml('search-x', 'Sin resultados', 'Probá con otro insumo o persona.')
        : emptyStateHtml('clipboard-check', 'Todavía no hay conteos', 'Contá lo que hay en el estante: el primero de cada sucursal pasa a ser su inventario de arranque.');
      $('btnLoadMoreCounts').hidden = !state.countsHasMore;
      renderCountDetail();
      utils.renderIcons();
      return;
    }

    if (!rows.some((c) => c.id === state.selected.count)) {
      state.selected.count = rows[0].id;
    }

    list.innerHTML = rows.map((c) => {
      const active = c.id === state.selected.count ? ' active' : '';
      const sub = [utils.formatDateTime(c.counted_at), state.isGlobalScope ? c.branch_name : null]
        .filter(Boolean).join(' · ');
      const badge = c.is_first_count
        ? `<span class="inv-badge muted">${pluralize(c.items.length, 'insumo', 'insumos')}</span>`
        : (c.mismatched_count
            ? `<span class="inv-badge warn">${pluralize(c.mismatched_count, 'diferencia', 'diferencias')}</span>`
            : '<span class="inv-badge ok">Todo cuadra</span>');
      return `
        <button type="button" class="inv-row${active}" data-count-id="${c.id}">
          <span class="inv-row-thumb inv-row-thumb-count"><i data-lucide="${c.is_first_count ? 'flag' : 'clipboard-check'}"></i></span>
          <span class="inv-row-info">
            <strong>${esc(countTitle(c))}</strong>
            <small>${esc(sub)}</small>
          </span>
          ${badge}
          ${countAmountHtml(c)}
        </button>`;
    }).join('');

    list.querySelectorAll('.inv-row').forEach((row) => {
      row.addEventListener('click', () => {
        state.selected.count = Number(row.dataset.countId);
        renderCountList();
        openDetailOnMobile($('countList'));
      });
    });

    $('btnLoadMoreCounts').hidden = !state.countsHasMore;
    renderCountDetail();
    utils.renderIcons();
  }

  function renderCountDetail() {
    const detail = $('countDetail');
    const c = state.counts.find((x) => x.id === state.selected.count);
    if (!c) {
      detail.innerHTML = emptyStateHtml('mouse-pointer-click', 'Elegí un conteo', 'Lo que decía el sistema, lo que se contó y la diferencia aparecen acá.');
      utils.renderIcons();
      return;
    }

    // Primero lo que no cuadró, de la diferencia más grande a la más chica: es lo que alguien
    // tiene que ir a mirar. Lo que cuadró queda abajo, en orden alfabético.
    const lineas = c.items.slice().sort((a, b) => {
      const da = Math.abs(Number(a.difference));
      const db = Math.abs(Number(b.difference));
      if (da !== db) return db - da;
      return a.item_name.localeCompare(b.item_name, 'es');
    });

    const rowsHtml = lineas.map((l) => {
      const dif = Number(l.difference);
      const clase = c.is_first_count || dif === 0 ? '' : (dif < 0 ? ' inv-count-diff-short' : ' inv-count-diff-over');
      const valor = (l.unit_cost != null && dif !== 0) ? money(Math.abs(dif * Number(l.unit_cost))) : '—';
      return `
        <tr>
          <td class="inv-td-name" data-label="Insumo">${esc(l.item_name)}</td>
          <td class="num" data-label="Sistema">${esc(qty(l.expected_quantity))}</td>
          <td class="num" data-label="Contado">${esc(qty(l.counted_quantity))} ${esc(l.unit)}</td>
          <td class="num${clase}" data-label="Diferencia">${dif === 0 ? '—' : esc(signedQty(dif))}</td>
          <td class="num" data-label="Valor">${valor}</td>
        </tr>`;
    }).join('');

    const nota = c.is_first_count
      ? '<p class="inv-detail-note">Primer conteo de la sucursal: es su inventario de arranque. Las diferencias son lo que ya había antes de que el sistema llevara la cuenta, no faltantes.</p>'
      : '';

    detail.innerHTML = `
      ${detailBackHtml()}
      <div class="inv-detail-header">
        <span class="inv-detail-thumb inv-detail-thumb-count"><i data-lucide="${c.is_first_count ? 'flag' : 'clipboard-check'}"></i></span>
        <span class="inv-badge ${c.is_first_count ? 'muted' : (c.mismatched_count ? 'warn' : 'ok')}">Conteo #${c.id}</span>
      </div>
      <h3>${esc(countTitle(c))}</h3>
      <p class="inv-detail-sub">${esc(utils.formatDateTime(c.counted_at))} · ${esc(c.branch_name)}</p>
      ${nota}
      ${c.notes ? `<p class="inv-detail-note">${esc(c.notes)}</p>` : ''}
      <div class="inv-metrics">
        <div><span>Contados</span><strong>${c.items.length} <small>${c.items.length === 1 ? 'insumo' : 'insumos'}</small></strong></div>
        <div><span>${c.is_first_count ? 'Valor cargado' : 'Diferencia'}</span><strong>${countAmountHtml(c)}</strong></div>
      </div>
      <div class="inv-detail-section-header"><span>${c.is_first_count ? 'Lo que había' : 'Contado contra sistema'}</span></div>
      <table class="inv-detail-table inv-count-table">
        <thead>
          <tr><th>Insumo</th><th class="num">Sistema</th><th class="num">Contado</th><th class="num">Diferencia</th><th class="num">Valor</th></tr>
        </thead>
        <tbody>${rowsHtml}</tbody>
      </table>
      <div class="inv-detail-section-header"><span>Detalles</span></div>
      <div class="inv-detail-rows">
        <div><span>Sucursal</span><strong>${esc(c.branch_name)}</strong></div>
        <div><span>Contó</span><strong>${esc(c.counted_by_name)}</strong></div>
        <div><span>Cuándo</span><strong>${esc(utils.formatDateTime(c.counted_at))}</strong></div>
        <div><span>Con diferencia</span><strong>${pluralize(c.mismatched_count, 'insumo', 'insumos')}</strong></div>
      </div>`;
    utils.renderIcons();
  }

  $('countSearch')?.addEventListener('input', (e) => {
    state.search.count = e.target.value;
    renderCountList();
  });

  $('countBranchFilter')?.addEventListener('change', (e) => {
    state.countBranchFilter = e.target.value;
    state.selected.count = null;
    loadCounts({ reset: true });
  });

  $('btnLoadMoreCounts')?.addEventListener('click', async () => {
    const btn = $('btnLoadMoreCounts');
    btn.disabled = true;
    btn.textContent = 'Cargando...';
    await loadCounts();
    btn.disabled = false;
    btn.textContent = 'Cargar más conteos';
  });

  // ==========================================================================
  // Modal: nuevo conteo
  // El catálogo entero ya está puesto y solo se escribe lo contado: un conteo es recorrer el
  // estante, no armar una lista. Lo escrito vive en state.countEntries (y no en los inputs) para
  // que buscar o filtrar, que vuelve a dibujar las filas, no borre nada de lo que ya se anotó.
  // ==========================================================================
  function countModalBranchId() {
    return state.fixedBranchId || ($('countBranchSelect').value ? Number($('countBranchSelect').value) : null);
  }

  async function loadCountStock() {
    state.countStock = [];
    state.countEntries = new Map();
    state.countIsFirst = false;
    $('countIntro').hidden = true;
    const branchId = countModalBranchId();
    if (!branchId) { renderCountLines(); return; }

    $('countLines').innerHTML = skeletonListHtml(4);
    try {
      // Sin only_stocked: en el conteo de arranque justamente importa lo que el sistema nunca vio.
      const [stock, previos] = await Promise.all([
        api.get(`/inventory/stock?branch_id=${branchId}`),
        api.get(`/inventory/counts?branch_id=${branchId}&limit=1`),
      ]);
      state.countStock = stock;
      state.countIsFirst = previos.length === 0;
    } catch (err) {
      showModalError('countError', err.message || 'No se pudo traer el catálogo de esta sucursal.');
    }
    $('countIntro').hidden = !state.countIsFirst;
    renderCountLines();
    updateCountTotal();
  }

  function visibleCountRows() {
    const q = state.search.countItem.trim().toLowerCase();
    return state.countStock.filter((r) => {
      // Lo que ya se anotó nunca se esconde: desaparecer de la vista algo que se contó hace
      // pensar que se perdió.
      if (state.countEntries.has(r.inventory_item_id)) return !q || r.item_name.toLowerCase().includes(q);
      if (state.countOnlyStocked && !Number(r.entered) && !Number(r.wasted) && !Number(r.adjusted)) return false;
      return !q || `${r.item_name} ${r.category || ''}`.toLowerCase().includes(q);
    });
  }

  function countDiffHtml(row) {
    const valor = state.countEntries.get(row.inventory_item_id);
    if (valor == null) return '—';
    const dif = Number(valor) - Number(row.on_hand);
    if (Math.abs(dif) < 0.0005) return '<span class="inv-count-diff-ok">Cuadra</span>';
    if (state.countIsFirst) return esc(signedQty(dif));
    return `<span class="${dif < 0 ? 'inv-count-diff-short' : 'inv-count-diff-over'}">${esc(signedQty(dif))}</span>`;
  }

  function renderCountLines() {
    const box = $('countLines');
    const rows = visibleCountRows();

    if (!state.countStock.length) {
      box.innerHTML = emptyStateHtml('layout-list', 'No hay insumos en el catálogo', 'Creá los insumos en la vista Insumos y volvé a contar.');
      utils.renderIcons();
      return;
    }
    if (!rows.length) {
      box.innerHTML = emptyStateHtml('search-x', 'Sin resultados', 'Probá con otro nombre, o destildá "Solo los que tienen movimiento".');
      utils.renderIcons();
      return;
    }

    box.innerHTML = rows.map((r) => {
      const valor = state.countEntries.get(r.inventory_item_id);
      const sistema = Number(r.on_hand);
      return `
        <div class="inv-count-row${valor != null ? ' is-counted' : ''}" data-item-id="${r.inventory_item_id}">
          <div class="inv-count-name">
            <strong>${esc(r.item_name)}</strong>
            <small>${esc(r.category || 'Sin categoría')} · en ${esc(r.unit)}</small>
          </div>
          <div class="inv-count-cell">
            <span class="inv-line-label">Sistema</span>
            <span class="inv-count-system${sistema < 0 ? ' is-negative' : ''}">${esc(qty(sistema))}</span>
          </div>
          <div class="inv-count-cell">
            <span class="inv-line-label">Contado</span>
            <input type="number" class="modal-input inv-count-input" min="0" step="0.001" inputmode="decimal"
                   placeholder="—" value="${valor != null ? esc(valor) : ''}" aria-label="Cantidad contada de ${esc(r.item_name)}">
          </div>
          <div class="inv-count-cell">
            <span class="inv-line-label">Diferencia</span>
            <span class="inv-count-diff">${countDiffHtml(r)}</span>
          </div>
        </div>`;
    }).join('');

    box.querySelectorAll('.inv-count-row').forEach((el) => {
      const itemId = Number(el.dataset.itemId);
      const row = state.countStock.find((r) => r.inventory_item_id === itemId);
      const input = el.querySelector('.inv-count-input');
      // Solo se refresca la fila y el pie: volver a dibujar la lista con cada tecla le quitaría
      // el foco al campo que se está escribiendo.
      input.addEventListener('input', () => {
        const v = input.value.trim();
        if (v === '') state.countEntries.delete(itemId);
        else state.countEntries.set(itemId, v);
        el.classList.toggle('is-counted', v !== '');
        el.querySelector('.inv-count-diff').innerHTML = countDiffHtml(row);
        updateCountTotal();
      });
      // Enter pasa al siguiente: contar un estante es anotar un número tras otro.
      input.addEventListener('keydown', (e) => {
        if (e.key !== 'Enter') return;
        e.preventDefault();
        const inputs = Array.from(box.querySelectorAll('.inv-count-input'));
        inputs[inputs.indexOf(input) + 1]?.focus();
      });
    });
    utils.renderIcons();
  }

  /** Pie del modal: cuántos se contaron, cuántos no cuadran y cuánto vale la diferencia. */
  function updateCountTotal() {
    let contados = 0;
    let distintos = 0;
    let valor = 0;
    let conCosto = 0;
    state.countEntries.forEach((v, itemId) => {
      const row = state.countStock.find((r) => r.inventory_item_id === itemId);
      if (!row) return;
      contados += 1;
      const dif = Number(v) - Number(row.on_hand);
      if (Math.abs(dif) < 0.0005) return;
      distintos += 1;
      if (row.last_unit_cost != null) {
        valor += dif * Number(row.last_unit_cost);
        conCosto += 1;
      }
    });

    $('countProgress').textContent = contados
      ? `${pluralize(contados, 'contado', 'contados')} · ${distintos ? pluralize(distintos, 'no cuadra', 'no cuadran') : 'todo cuadra'}`
      : 'Nada contado todavía';

    const total = $('countTotal');
    total.classList.remove('is-short', 'is-over');
    if (!conCosto) {
      total.textContent = '—';
    } else if (state.countIsFirst) {
      total.textContent = money(Math.abs(valor));
    } else {
      total.textContent = `${valor < 0 ? '-' : '+'}${money(Math.abs(valor))}`;
      total.classList.add(valor < 0 ? 'is-short' : 'is-over');
    }
    const nota = document.querySelector('#modalCount .inv-total-box small');
    if (nota) {
      nota.textContent = state.countIsFirst
        ? 'Valor de lo que había, al costo del último cargamento'
        : 'Diferencia al costo del último cargamento';
    }
  }

  $('countItemSearch')?.addEventListener('input', (e) => {
    state.search.countItem = e.target.value;
    renderCountLines();
  });

  $('countOnlyStocked')?.addEventListener('change', (e) => {
    state.countOnlyStocked = e.target.checked;
    renderCountLines();
  });

  $('countBranchSelect')?.addEventListener('change', () => {
    // Cambiar de sucursal a mitad del conteo descarta lo anotado: los números eran de otro
    // estante, y mandarlos a esta sucursal sería peor que perderlos.
    loadCountStock();
  });

  function openCountModal() {
    $('countError').style.display = 'none';
    $('countNotes').value = '';
    $('countItemSearch').value = '';
    state.search.countItem = '';
    $('countOnlyStocked').checked = state.countOnlyStocked;
    $('countTotal').textContent = '—';
    $('countProgress').textContent = 'Nada contado todavía';
    openModal('modalCount');
    loadCountStock();
  }

  $('btnSubmitCount')?.addEventListener('click', async () => {
    $('countError').style.display = 'none';

    const branchId = countModalBranchId();
    if (!branchId) { showModalError('countError', 'Elegí una sucursal.'); return; }

    const items = [];
    for (const [itemId, v] of state.countEntries) {
      const cantidad = Number(v);
      if (Number.isNaN(cantidad) || cantidad < 0) {
        const nombre = state.countStock.find((r) => r.inventory_item_id === itemId)?.item_name || 'un insumo';
        showModalError('countError', `La cantidad de "${nombre}" no es válida.`);
        return;
      }
      items.push({ inventory_item_id: itemId, counted_quantity: v });
    }
    if (!items.length) { showModalError('countError', 'Anotá la cantidad de al menos un insumo.'); return; }

    const btn = $('btnSubmitCount');
    btn.disabled = true;
    btn.textContent = 'Guardando...';
    try {
      const creado = await api.post('/inventory/counts', {
        branch_id: branchId,
        notes: $('countNotes').value.trim() || null,
        items,
      });
      closeModal('modalCount');

      utils.showToast(
        creado.is_first_count
          ? `Inventario de arranque cargado: ${pluralize(creado.items.length, 'insumo', 'insumos')}.`
          : (creado.mismatched_count
              ? `Conteo guardado. ${pluralize(creado.mismatched_count, 'insumo no cuadró', 'insumos no cuadraron')} y ya quedó corregido.`
              : 'Conteo guardado. Todo cuadra con el sistema.'),
        creado.is_first_count || !creado.mismatched_count ? 'success' : 'warning'
      );

      state.selected.count = creado.id;
      await Promise.all([loadCounts({ reset: true }), loadStock()]);
      setView('conteo');
    } catch (err) {
      showModalError('countError', err.message || 'No se pudo guardar el conteo.');
    } finally {
      btn.disabled = false;
      btn.textContent = 'Guardar conteo';
    }
  });

  // ==========================================================================
  // Contexto de sucursal
  // ==========================================================================
  async function resolveBranchContext(user) {
    const badge = $('branchFixedBadge');
    const select = $('branchSelect');
    const filter = $('shipmentBranchFilter');

    const wasteBadge = $('wasteBranchBadge');
    const wasteSelect = $('wasteBranchSelect');
    const wasteFilter = $('wasteBranchFilter');
    const stockFilter = $('stockBranchFilter');

    const countBadge = $('countBranchBadge');
    const countSelect = $('countBranchSelect');
    const countFilter = $('countBranchFilter');

    if (user.branch_id) {
      state.fixedBranchId = user.branch_id;
      state.isGlobalScope = false;
      const branchName = user.branch ? user.branch.name : `Sucursal #${user.branch_id}`;
      badge.hidden = false;
      badge.textContent = branchName;
      select.hidden = true;
      filter.hidden = true;
      wasteBadge.hidden = false;
      wasteBadge.textContent = branchName;
      wasteSelect.hidden = true;
      wasteFilter.hidden = true;
      stockFilter.hidden = true;
      countBadge.hidden = false;
      countBadge.textContent = branchName;
      countSelect.hidden = true;
      countFilter.hidden = true;
      $('invScopeValue').textContent = branchName;
      $('invHeaderScope').textContent = `Entradas, merma y existencias de ${branchName}`;
      return;
    }

    state.isGlobalScope = true;
    badge.hidden = true;
    select.hidden = false;
    wasteBadge.hidden = true;
    wasteSelect.hidden = false;
    countBadge.hidden = true;
    countSelect.hidden = false;
    $('invScopeValue').textContent = 'Todas las sucursales';
    $('invHeaderScope').textContent = 'Entradas, merma y existencias de todas las sucursales';

    try {
      state.branches = await api.get('/branches/');
      const options = state.branches.map((b) => `<option value="${b.id}">${esc(b.name)}</option>`).join('');
      select.innerHTML = options;
      wasteSelect.innerHTML = options;
      filter.innerHTML = `<option value="">Todas las sucursales</option>${options}`;
      filter.hidden = false;
      wasteFilter.innerHTML = `<option value="">Todas las sucursales</option>${options}`;
      wasteFilter.hidden = false;
      stockFilter.innerHTML = `<option value="">Todas las sucursales</option>${options}`;
      stockFilter.hidden = false;
      countSelect.innerHTML = options;
      countFilter.innerHTML = `<option value="">Todas las sucursales</option>${options}`;
      countFilter.hidden = false;
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
  $('wasteList').innerHTML = skeletonListHtml(3);
  $('countList').innerHTML = skeletonListHtml(3);

  await resolveBranchContext(existingUser);
  // Los motivos van primero: el filtro de la vista Merma y el selector del modal se llenan con
  // ellos, y loadWaste puede pedir con un motivo ya elegido.
  await loadWasteReasons();
  await Promise.all([
    loadShipments({ reset: true }),
    loadAnalytics(),
    loadCatalogs(),
    loadWaste({ reset: true }),
    loadWasteAnalytics(),
    loadStock(),
    loadCounts({ reset: true }),
    loadInvuStatus(),
  ]);

  renderResumen();
  renderItemList();
  renderSupplierList();
  setView('resumen');
  utils.renderIcons();
});
