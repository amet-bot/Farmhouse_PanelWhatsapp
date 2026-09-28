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
    itemKind: '',   // filtro de Insumos: '' | 'materia_prima' | 'casa'
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
  // Costo de referencia de Invu: por gramo o mililitro son fracciones de centavo ($0.0065) y con
  // dos decimales se verían como $0.01.
  const unitCostFormatter = new Intl.NumberFormat('es-PA', { minimumFractionDigits: 2, maximumFractionDigits: 4 });
  const unitCost = (n) => `$${unitCostFormatter.format(Number(n || 0))}`;
  // Costo de una merma para mostrar: el de cargamento o, si no hay, el estimado con Invu (≈).
  const wasteCostValue = (w) => (w.display_cost != null ? Number(w.display_cost) : (w.total_cost != null ? Number(w.total_cost) : null));
  const wasteCostTxt = (w) => { const v = wasteCostValue(w); return v == null ? '—' : `${w.cost_estimated ? '≈ ' : ''}${money(v)}`; };

  // Fotos de evidencia de la merma (ver "Evidencia de la merma" más abajo). Arriba y no allá: el detalle de una
  // merma se puede pintar antes de que el módulo llegue a esa parte.
  const WASTE_PHOTOS_MAX = 6;           // mismo tope que el servidor
  const WASTE_SELF_DELETE_MS = 24 * 60 * 60 * 1000;   // quien la cargó puede borrarla (igual que el servidor)
  const PHOTO_MAX_SIDE = 1600;          // px del lado largo: se ve bien el detalle y pesa ~300 KB
  let pendingWastePhotos = [];          // [{ blob, url }] elegidas en el modal; se suben al guardar
  const wastePhotoUrls = new Map();     // id de foto → URL ya descargada (no se vuelve a pedir)

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

  // La unidad corta que va al lado de cada número ("692 g", "1.65 kg", "3 u."). Las de Invu
  // vienen escritas enteras ("gramos", "kilogramo"); las cargadas a mano, como sea.
  const UNIT_SHORT = {
    gramo: 'g', gramos: 'g', gr: 'g', g: 'g',
    kilogramo: 'kg', kilogramos: 'kg', kilo: 'kg', kilos: 'kg', kg: 'kg',
    mililitro: 'ml', mililitros: 'ml', ml: 'ml',
    litro: 'L', litros: 'L', l: 'L',
    libra: 'lb', libras: 'lb', lb: 'lb',
    onza: 'oz', onzas: 'oz', oz: 'oz',
    unidad: 'u.', unidades: 'u.', und: 'u.', u: 'u.',
  };
  const unitShort = (u) => UNIT_SHORT[String(u || '').trim().toLowerCase()] || String(u || '');
  /** Para buscar sin que importen tildes ni mayúsculas: "Piña" → "pina". */
  const sinTildes = (s) => String(s || '').normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase();
  // Kilos por unidad corta, solo de las que son peso (la merma se pesa en balanza).
  const KG_FACTOR = { g: 0.001, kg: 1, lb: 0.45359237, oz: 0.028349523 };
  const ML_FACTOR = { ml: 1, L: 1000 };
  /**
   * Familia de la unidad de un insumo, con `base` = gramos (o ml) que hay en 1 de esa unidad.
   * Lo que no es peso ni volumen ("unidad", "caja"...) se cuenta por pieza. Igual que el servidor
   * (_familia_de_unidad).
   */
  function unitFamily(unit) {
    const corta = unitShort(unit);
    if (KG_FACTOR[corta]) return { fam: 'peso', base: KG_FACTOR[corta] * 1000, short: corta };
    if (ML_FACTOR[corta]) return { fam: 'volumen', base: ML_FACTOR[corta], short: corta };
    return { fam: 'unidad', base: 1, short: corta || 'u.' };
  }

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
  // Tema y sesión (utilidad compartida, ver js/shared/shell.js)
  // ==========================================================================
  FarmhouseShell.initTheme();
  FarmhouseShell.initLogout({ redirectTo: '/' });

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
    // Solo el de arriba (el último en el DOM se pinta encima). Antes cerraba todos: con "Nuevo
    // insumo" abierto sobre "Registrar cargamento", Escape también tiraba el cargamento a medio
    // llenar.
    const open = document.querySelectorAll('.modal-backdrop.active');
    if (open.length) open[open.length - 1].classList.remove('active');
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
  // Número de la petición más reciente por lista. Cambiar el filtro de sucursal dos veces
  // seguidas (o tocar "Cargar más" durante un reinicio) dejaba que la respuesta vieja llegara
  // después y se concatenara: filas de dos sucursales mezcladas y el offset corrido.
  const loadSeq = { shipments: 0, waste: 0, counts: 0, stock: 0, wasteStock: 0, countStock: 0 };

  async function loadShipments({ reset = false } = {}) {
    const seq = ++loadSeq.shipments;
    if (reset) {
      state.shipments = [];
      state.shipmentsOffset = 0;
      $('shipmentList').innerHTML = skeletonListHtml();
    }
    const params = new URLSearchParams({ limit: String(PAGE_SIZE), offset: String(state.shipmentsOffset) });
    if (state.branchFilter) params.set('branch_id', state.branchFilter);
    try {
      const page = await api.get(`/inventory/shipments?${params.toString()}`);
      if (seq !== loadSeq.shipments) return;
      state.shipments = state.shipments.concat(page);
      state.shipmentsOffset += page.length;
      state.shipmentsHasMore = page.length === PAGE_SIZE;
    } catch (err) {
      if (seq !== loadSeq.shipments) return;
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
      api.get('/inventory/items?limit=500').catch(() => []),
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
    $('wasteReasonSelect').innerHTML = `<option value=""></option>${options}`;
    renderWasteReasonChips();
  }

  // Los motivos del formulario van como botones grandes con ícono y en palabras de cocina. El
  // código (y la etiqueta del filtro y los reportes) sigue siendo el del servidor; esto solo
  // cambia cómo se le pregunta a quien está botando algo.
  const REASON_UI = {
    vencido: { icon: 'calendar-x', label: 'Se venció' },
    danado: { icon: 'package-x', label: 'Se dañó o golpeó' },
    derrame: { icon: 'glass-water', label: 'Se cayó o se rompió' },
    error_preparacion: { icon: 'chef-hat', label: 'Salió mal al prepararlo' },
    recorte: { icon: 'scissors', label: 'Recorte al limpiar' },
    devolucion: { icon: 'undo-2', label: 'Lo devolvió un cliente' },
    consumo_interno: { icon: 'utensils', label: 'Lo comió el personal' },
    faltante: { icon: 'search-x', label: 'Falta o se perdió' },
    otro: { icon: 'more-horizontal', label: 'Otro motivo' },
  };
  // Orden de los botones: lo que más pasa primero.
  const REASON_ORDER = ['vencido', 'danado', 'derrame', 'error_preparacion', 'recorte', 'devolucion', 'consumo_interno', 'faltante', 'otro'];

  function renderWasteReasonChips() {
    const box = $('wasteReasonChips');
    if (!box) return;
    const actual = $('wasteReasonSelect').value;
    const motivos = [...state.wasteReasons].sort((a, b) => {
      const ia = REASON_ORDER.indexOf(a.code);
      const ib = REASON_ORDER.indexOf(b.code);
      return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib);
    });
    box.innerHTML = motivos.map((r) => {
      const ui = REASON_UI[r.code] || { icon: 'circle-help', label: r.label };
      const on = r.code === actual;
      return `<button type="button" class="inv-reason-chip${on ? ' is-active' : ''}" data-reason="${esc(r.code)}" role="radio" aria-checked="${on}">
          <i data-lucide="${ui.icon}"></i><span>${esc(ui.label)}</span>
        </button>`;
    }).join('');
    utils.renderIcons();
  }

  $('wasteReasonChips')?.addEventListener('click', (e) => {
    const chip = e.target.closest('.inv-reason-chip');
    if (!chip) return;
    const select = $('wasteReasonSelect');
    select.value = chip.dataset.reason;
    select.dispatchEvent(new Event('change'));
    $('wasteReasonChips').querySelectorAll('.inv-reason-chip').forEach((c) => {
      const on = c === chip;
      c.classList.toggle('is-active', on);
      c.setAttribute('aria-checked', on ? 'true' : 'false');
    });
  });

  async function loadWaste({ reset = false } = {}) {
    const seq = ++loadSeq.waste;
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
      if (seq !== loadSeq.waste) return;
      state.waste = state.waste.concat(page);
      state.wasteOffset += page.length;
      state.wasteHasMore = page.length === PAGE_SIZE;
    } catch (err) {
      if (seq !== loadSeq.waste) return;
      utils.showToast(err.message || 'No se pudo cargar la merma.', 'error');
      state.wasteHasMore = false;
    }
    renderWasteList();
    animarEntrada($('wasteList'));
  }

  async function loadCounts({ reset = false } = {}) {
    const seq = ++loadSeq.counts;
    if (reset) {
      state.counts = [];
      state.countsOffset = 0;
      $('countList').innerHTML = skeletonListHtml();
    }
    const params = new URLSearchParams({ limit: String(PAGE_SIZE), offset: String(state.countsOffset) });
    if (state.countBranchFilter) params.set('branch_id', state.countBranchFilter);
    try {
      const page = await api.get(`/inventory/counts?${params.toString()}`);
      if (seq !== loadSeq.counts) return;
      state.counts = state.counts.concat(page);
      state.countsOffset += page.length;
      state.countsHasMore = page.length === PAGE_SIZE;
    } catch (err) {
      if (seq !== loadSeq.counts) return;
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
    applyInvuItemsMode();

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

  /**
   * Insumos: con Invu conectado se ofrece "Sincronizar con Invu" y se explica el origen, pero
   * "Nuevo insumo" sigue ahí (a diferencia de Proveedores): hace falta al recibir un cargamento.
   */
  function applyInvuItemsMode() {
    const { configured, items_last_synced_at, items_synced_count, items_local_count } = state.invu;
    $('btnSyncInvuItems').hidden = !configured;
    const note = $('invuItemsNote');
    if (!configured) {
      note.hidden = true;
      return;
    }
    const cuando = items_last_synced_at
      ? `Última sincronización el ${utils.formatDateTime(items_last_synced_at)}`
      : 'Todavía no se sincronizó ninguna vez';
    const detalle = [`${items_synced_count || 0} ${items_synced_count === 1 ? 'viene' : 'vienen'} de Invu`];
    if (items_local_count) detalle.push(`${pluralize(items_local_count, 'se cargó', 'se cargaron')} a mano`);
    note.hidden = false;
    note.querySelector('span').textContent =
      `Los insumos se traen de Invu (Ingredientes) una vez al día, y se pueden seguir creando acá. ` +
      `${cuando} · ${detalle.join('; ')}.`;
    utils.renderIcons();
  }

  $('btnSyncInvuItems')?.addEventListener('click', async () => {
    const btn = $('btnSyncInvuItems');
    btn.disabled = true;
    btn.innerHTML = '<i data-lucide="refresh-cw"></i> <span>Sincronizando...</span>';
    utils.renderIcons();
    try {
      const r = await api.post('/inventory/invu/sync-items', {});
      const partes = [];
      if (r.created) partes.push(`${r.created} nuevo${r.created === 1 ? '' : 's'}`);
      if (r.linked) partes.push(`${r.linked} emparejado${r.linked === 1 ? '' : 's'} con los que ya estaban`);
      if (r.updated) partes.push(`${r.updated} actualizado${r.updated === 1 ? '' : 's'}`);
      if (r.deactivated) partes.push(`${r.deactivated} archivado${r.deactivated === 1 ? '' : 's'} en Invu`);
      utils.showToast(
        partes.length
          ? `Invu devolvió ${r.received} insumos: ${partes.join(', ')}.`
          : `Invu devolvió ${r.received} insumos; no había nada que cambiar.`,
        'success'
      );
      await Promise.all([loadCatalogs(), loadInvuStatus()]);
      renderItemList();
    } catch (err) {
      utils.showToast(err.message || 'No se pudo sincronizar con Invu.', 'error');
    } finally {
      btn.disabled = false;
      btn.innerHTML = '<i data-lucide="refresh-cw"></i> <span>Sincronizar con Invu</span>';
      utils.renderIcons();
    }
  });

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
    const seq = ++loadSeq.stock;
    const params = new URLSearchParams();
    if (state.stockBranchFilter) params.set('branch_id', state.stockBranchFilter);
    if (state.stockOnlyMoved) params.set('only_stocked', 'true');
    try {
      const rows = await api.get(`/inventory/stock?${params.toString()}`);
      if (seq !== loadSeq.stock) return;
      state.stock = rows;
    } catch (err) {
      if (seq !== loadSeq.stock) return;
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
    const conCosto = rows.filter((w) => wasteCostValue(w) != null);
    return {
      rows,
      count: rows.length,
      total: conCosto.length ? conCosto.reduce((acc, w) => acc + wasteCostValue(w), 0) : null,
    };
  }

  // ==========================================================================
  // Tablero del Resumen: ventas, compras, merma, faltantes y costo de lo vendido
  // ==========================================================================
  const dashboard = { days: 7, branch: '', data: null, seq: 0 };

  const pctTxt = (v) => (v == null ? '—' : `${Number(v).toLocaleString('es-PA', { maximumFractionDigits: 1 })}%`);

  /** "+12% vs. semana anterior" (sube bien o mal según la cifra: más venta es bueno, más merma no). */
  function dashDelta(ahora, antes, subirEsBueno) {
    const a = Number(ahora || 0);
    const b = Number(antes || 0);
    if (!b) return '';
    const pct = Math.round(((a - b) / b) * 100);
    if (Math.abs(pct) < 1) return '<span class="inv-dash-delta">igual que el período anterior</span>';
    const bueno = subirEsBueno ? pct > 0 : pct < 0;
    const texto = Math.abs(pct) > 300 ? 'mucho' : `${Math.abs(pct)}%`;
    return `<span class="inv-dash-delta ${bueno ? 'is-good' : 'is-bad'}">${pct > 0 ? '▲' : '▼'} ${texto} ${pct > 0 ? 'más' : 'menos'}</span>`;
  }

  function dashboardInsights(d) {
    const t = d.totals;
    const frases = [];
    const filas = d.branches;
    if (t.sales_net == null) frases.push('Todavía no hay ventas de Invu sincronizadas en este período.');
    if (filas.length > 1) {
      const conVenta = filas.filter((b) => b.waste_pct_sales != null);
      const peor = conVenta.sort((a, b) => Number(b.waste_pct_sales) - Number(a.waste_pct_sales))[0];
      if (peor && Number(peor.waste_pct_sales) > 1) {
        frases.push(`<strong>${esc(peor.branch_name)}</strong> es la que más pierde en merma: ${pctTxt(peor.waste_pct_sales)} de lo que vende.`);
      }
    }
    if (t.waste_pct_sales != null && Number(t.waste_pct_sales) > 3) {
      frases.push(`La merma es el ${pctTxt(t.waste_pct_sales)} de la venta: arriba de 3% suele valer una revisión.`);
    }
    if (Number(t.count_missing) > 0) {
      frases.push(`En los conteos faltaron <strong>${esc(money(t.count_missing))}</strong> que nadie registró (ya descontado lo vendido).`);
    } else if (!t.counts) {
      frases.push('No hubo conteos en este período: sin conteo no se sabe si falta mercadería. Uno por semana alcanza.');
    }
    if (t.recipe_coverage_pct != null && Number(t.recipe_coverage_pct) < 60) {
      frases.push(`Solo el ${pctTxt(t.recipe_coverage_pct)} de los platos vendidos tiene receta en Invu: el costo de lo vendido y los conteos son parciales hasta que se carguen más recetas.`);
    }
    if (t.purchase_lines_without_cost) {
      frases.push(`${pluralize(t.purchase_lines_without_cost, 'línea de cargamento quedó', 'líneas de cargamento quedaron')} sin costo: las compras salen más bajas de lo real.`);
    }
    return frases;
  }

  function renderDashboard() {
    const box = $('dashboardBox');
    const d = dashboard.data;
    if (!box || !d) return;
    const t = d.totals;
    const p = d.prev_totals;
    const periodo = dashboard.days === 7 ? 'la semana anterior' : 'el mes anterior';
    const tiles = [
      { label: 'Ventas', value: t.sales_net != null ? money(t.sales_net) : '—', sub: 'Caja de Invu', delta: dashDelta(t.sales_net, p.sales_net, true) },
      { label: 'Compras', value: money(t.purchases), sub: t.purchases_pct_sales != null ? `${pctTxt(t.purchases_pct_sales)} de la venta` : 'Cargamentos con costo', delta: dashDelta(t.purchases, p.purchases, false) },
      { label: 'Merma', value: `${t.waste_estimated ? '≈ ' : ''}${money(t.waste)}`, sub: t.waste_pct_sales != null ? `${pctTxt(t.waste_pct_sales)} de la venta` : 'Lo que se botó', delta: dashDelta(t.waste, p.waste, false), bad: true },
      { label: 'Faltó en conteos', value: t.counts ? money(t.count_missing) : '—', sub: t.counts ? pluralize(t.counts, 'conteo', 'conteos') : 'Sin conteos en el período', delta: t.counts ? dashDelta(t.count_missing, p.count_missing, false) : '', bad: true },
      {
        label: 'Costo de lo vendido',
        value: money(t.theoretical_cost),
        // Con pocas recetas cargadas la cifra sale chica por falta de datos, no porque la comida
        // cueste poco: se dice que es parcial en vez de mostrar un "0.7% de la venta" engañoso.
        sub: t.recipe_coverage_pct != null && Number(t.recipe_coverage_pct) < 60
          ? `Parcial: solo ${pctTxt(t.recipe_coverage_pct)} de lo vendido tiene receta en Invu`
          : (t.food_cost_pct != null ? `${pctTxt(t.food_cost_pct)} de la venta · recetas de Invu` : 'Según recetas de Invu'),
        delta: '',
      },
    ];

    const tabla = d.branches.length > 1 ? `
      <div class="inv-dash-table-wrap">
        <table class="inv-dash-table">
          <thead><tr><th>Sucursal</th><th class="num">Ventas</th><th class="num">Compras</th><th class="num">Merma</th><th class="num">% merma</th><th class="num">Faltó</th><th class="num">Costo vendido</th></tr></thead>
          <tbody>${d.branches.map((b) => `
            <tr>
              <td data-label="Sucursal"><strong>${esc(b.branch_name)}</strong></td>
              <td class="num" data-label="Ventas">${b.sales_net != null ? esc(money(b.sales_net)) : '—'}</td>
              <td class="num" data-label="Compras">${esc(money(b.purchases))}</td>
              <td class="num" data-label="Merma">${esc(money(b.waste))}</td>
              <td class="num" data-label="% merma">${esc(pctTxt(b.waste_pct_sales))}</td>
              <td class="num" data-label="Faltó">${b.counts ? esc(money(b.count_missing)) : '—'}</td>
              <td class="num" data-label="Costo vendido">${esc(money(b.theoretical_cost))}${b.food_cost_pct != null ? ` <small>(${esc(pctTxt(b.food_cost_pct))})</small>` : ''}</td>
            </tr>`).join('')}
          </tbody>
        </table>
      </div>` : '';

    const topHtml = (titulo, lista, vacio) => `
      <div class="inv-dash-top">
        <h3>${esc(titulo)}</h3>
        ${lista.length ? `<ol>${lista.map((i) => `<li><span>${esc(i.name)} <small>${esc(qty(i.quantity))} ${esc(unitShort(i.unit))}</small></span><strong>${i.estimated ? '≈ ' : ''}${esc(money(i.cost))}</strong></li>`).join('')}</ol>` : `<p class="inv-ca-foot">${esc(vacio)}</p>`}
      </div>`;

    const hallazgos = dashboardInsights(d);
    box.innerHTML = `
      <div class="inv-dash-tiles">${tiles.map((k) => `
        <div class="inv-dash-tile${k.bad ? ' is-loss' : ''}">
          <span class="inv-dash-label">${esc(k.label)}</span>
          <strong>${esc(k.value)}</strong>
          <small>${esc(k.sub)}</small>
          ${k.delta ? `<small>${k.delta} que ${esc(periodo)}</small>` : ''}
        </div>`).join('')}
      </div>
      ${hallazgos.length ? `<ul class="inv-ca-findings inv-dash-findings">${hallazgos.map((h) => `<li>${h}</li>`).join('')}</ul>` : ''}
      ${tabla}
      <div class="inv-dash-tops">
        ${topHtml('Lo que más se bota', d.top_waste, 'Sin merma en el período.')}
        ${topHtml('Lo que más falta en los conteos', d.top_missing, d.totals.counts ? 'Nada faltó en los conteos.' : 'Sin conteos en el período.')}
      </div>`;
    utils.renderIcons();
  }

  async function loadDashboard() {
    const box = $('dashboardBox');
    if (!box) return;
    const seq = ++dashboard.seq;
    box.innerHTML = '<div class="inv-ca-loading">Calculando el tablero…</div>';
    const params = new URLSearchParams({ days: String(dashboard.days) });
    if (dashboard.branch) params.set('branch_id', dashboard.branch);
    try {
      const d = await api.get(`/inventory/dashboard?${params}`);
      if (seq !== dashboard.seq) return;
      dashboard.data = d;
      renderDashboard();
    } catch (err) {
      if (seq !== dashboard.seq) return;
      box.innerHTML = '<p class="inv-ca-foot">No se pudo calcular el tablero. Probá de nuevo en un rato.</p>';
    }
  }

  document.querySelectorAll('#dashboardPeriod button').forEach((b) => b.addEventListener('click', () => {
    document.querySelectorAll('#dashboardPeriod button').forEach((x) => x.classList.toggle('active', x === b));
    dashboard.days = Number(b.dataset.days);
    loadDashboard();
  }));
  $('dashboardBranch')?.addEventListener('change', (e) => {
    dashboard.branch = e.target.value;
    loadDashboard();
  });

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

    loadDashboard();
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
      if (wasteCostValue(w) != null) {
        entry.costo += wasteCostValue(w);
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

  // ---- Contexto de un cargamento ----
  // Al guardar (y en el detalle): el precio contra la compra anterior, otra sucursal e Invu; la
  // existencia y para cuántos días alcanza; el gasto. Sale de GET /shipments/{id}/insights.
  const shipmentInsightsCache = new Map();

  const fechaServidor = (s) => (s ? utils._parseServerDate(s)?.toLocaleDateString('es-PA', { day: 'numeric', month: 'short' }) : '');

  /** $ por kg o por litro de un costo por unidad del insumo; null si el insumo va por pieza. */
  function costoPorMil(costo, unit) {
    const fam = unitFamily(unit);
    if (fam.fam === 'unidad' || costo == null) return null;
    return (Number(costo) / fam.base) * 1000;
  }

  function shipmentInsightItemHtml(it) {
    const u = unitShort(it.unit);
    const frases = [];
    const costo = it.unit_cost != null ? Number(it.unit_cost) : null;

    if (costo == null) {
      frases.push('<span class="inv-line-warn">Sin costo: cargalo en el próximo cargamento para que la merma y el conteo usen el precio real.</span>');
    } else if (it.change_pct != null) {
      const pct = Number(it.change_pct);
      const cuando = `la compra del ${esc(fechaServidor(it.prev_received_at))}${it.prev_supplier ? ` (${esc(it.prev_supplier)})` : ''}`;
      if (Math.abs(pct) < 3) {
        frases.push(`Mismo precio que ${cuando}.`);
      } else {
        frases.push(`${pct > 0 ? 'Subió' : 'Bajó'} <strong>${Math.abs(pct).toLocaleString('es-PA', { maximumFractionDigits: 1 })}%</strong> desde ${cuando}: de ${esc(unitCost(it.prev_unit_cost))} a ${esc(unitCost(costo))} por ${esc(u)}.`);
      }
    } else {
      frases.push('Primera compra con precio en esta sucursal: desde la próxima se compara.');
    }

    if (costo != null && it.best_other_cost != null) {
      const otro = Number(it.best_other_cost);
      if (otro < costo * 0.97) {
        frases.push(`<strong>${esc(it.best_other_branch)}</strong> lo compró más barato: ${esc(unitCost(otro))} por ${esc(u)}${it.best_other_supplier ? ` a ${esc(it.best_other_supplier)}` : ''} (${esc(fechaServidor(it.best_other_received_at))}).`);
      } else {
        frases.push('Es el mejor precio entre las sucursales.');
      }
    }

    // Un costo cargado "por paquete" en un insumo que va en gramos da miles de dólares el kilo.
    const porMil = costoPorMil(costo, it.unit);
    if (porMil != null && porMil > 100) {
      frases.push(`<span class="inv-line-warn">Ojo: ${esc(money(porMil))} el ${unitFamily(it.unit).fam === 'volumen' ? 'litro' : 'kilo'}. ¿Se cargó el precio del paquete? El costo va por ${esc(u)}.</span>`);
    } else if (costo != null && it.reference_cost != null && hasPerm('inventory.adjust')) {
      const ref = Number(it.reference_cost);
      if (ref > 0 && Math.abs((costo - ref) / ref) > 0.15) {
        frases.push(`En Invu figura a ${esc(unitCost(ref))}: conviene actualizarlo, las recetas de Invu calculan con ese costo.`);
      }
    }

    const stock = Number(it.stock_now);
    if (stock < 0) {
      frases.push('La existencia da negativa: falta el conteo de arranque de la sucursal.');
    } else {
      const dias = it.days_left != null ? Number(it.days_left) : null;
      frases.push(`Quedan <strong>${esc(qty(stock))} ${esc(u)}</strong> en existencia${dias != null ? `: alcanza para <strong>~${dias.toLocaleString('es-PA', { maximumFractionDigits: 0 })} días</strong> al ritmo de venta` : ''}.`);
    }

    const subtotal = costo != null ? money(Number(it.quantity) * costo) : null;
    return `
      <div class="inv-ca-line">
        <div class="inv-ca-line-head">
          <strong>${esc(it.name)} <small class="inv-si-qty">${esc(qty(it.quantity))} ${esc(u)}</small></strong>
          ${subtotal ? `<span class="inv-ca-chip inv-ca-chip-ok">${esc(subtotal)}</span>` : ''}
        </div>
        <ul class="inv-wi-list">${frases.map((f) => `<li>${f}</li>`).join('')}</ul>
      </div>`;
  }

  function shipmentInsightsHtml(ins) {
    const semana = `En ${esc(ins.branch_name)} van <strong>${esc(money(ins.branch_week_spend))}</strong> en compras en los últimos 7 días${trendTxt(ins.branch_week_spend, ins.branch_prev_week_spend)}.`;
    const prov = ins.supplier_name && ins.supplier_month_spend != null
      ? ` A ${esc(ins.supplier_name)}, ${esc(money(ins.supplier_month_spend))} en los últimos 30 días.`
      : '';
    const sinCosto = ins.items_without_cost
      ? `<p class="inv-wi-warn">${pluralize(ins.items_without_cost, 'insumo quedó', 'insumos quedaron')} sin costo: sin el precio, la merma y el conteo de ${ins.items_without_cost === 1 ? 'ese insumo' : 'esos insumos'} se valúan con el de Invu.</p>`
      : '';
    return `
      ${sinCosto}
      ${ins.items.map(shipmentInsightItemHtml).join('')}
      <p class="inv-wi-branch">${semana}${prov}</p>`;
  }

  async function shipmentInsightsFor(id) {
    if (shipmentInsightsCache.has(id)) return shipmentInsightsCache.get(id);
    const ins = await api.get(`/inventory/shipments/${id}/insights`);
    shipmentInsightsCache.set(id, ins);
    return ins;
  }

  function showShipmentResult(cargamento) {
    shipmentInsightsCache.clear();   // un cargamento nuevo cambia el contexto de los demás
    if (cargamento.insights) shipmentInsightsCache.set(cargamento.id, cargamento.insights);
    $('shipmentResultTitle').textContent = `Cargamento #${cargamento.id} registrado`;
    $('shipmentResultSubtitle').textContent = [
      cargamento.branch_name,
      cargamento.supplier_name || 'Sin proveedor',
      cargamento.total_cost != null ? money(cargamento.total_cost) : null,
    ].filter(Boolean).join(' · ');
    $('shipmentResultBody').innerHTML = cargamento.insights ? shipmentInsightsHtml(cargamento.insights) : '';
    openModal('modalShipmentResult');
  }

  /** Debajo del costo de cada línea: en qué unidad va y el aviso si parece el precio de un paquete. */
  function updateShipmentCostHint(row) {
    const unitLabel = row.querySelector('.inv-line-unit');
    const unit = row.querySelector('.inv-item-input').dataset.unit;
    if (!unit) return;
    const u = unitShort(unit);
    const costo = row.querySelector('.inv-line-cost').value;
    const porMil = costoPorMil(costo !== '' ? Number(costo) : null, unit);
    unitLabel.innerHTML = porMil != null && porMil > 100
      ? `<span class="inv-line-warn">Ojo: da ${esc(money(porMil))} el ${unitFamily(unit).fam === 'volumen' ? 'litro' : 'kilo'}. El costo va por ${esc(u)}, no por paquete.</span>`
      : `Se cuenta en ${esc(unit)} · el costo va por ${esc(u)}`;
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
          <td class="num" data-label="Costo unit.">${l.unit_cost != null ? unitCost(l.unit_cost) : '—'}</td>
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
      <div class="inv-detail-section-header"><span>En contexto</span></div>
      <div id="shipmentInsightsBox"><div class="inv-ca-loading">Calculando…</div></div>
      <div class="inv-detail-section-header"><span>Detalles</span></div>
      <div class="inv-detail-rows">
        <div><span>Sucursal</span><strong>${esc(s.branch_name)}</strong></div>
        <div><span>Registrado por</span><strong>${esc(s.received_by_name)}</strong></div>
        <div><span>Recibido el</span><strong>${esc(utils.formatDateTime(s.received_at))}</strong></div>
        <div><span>Cargado al sistema</span><strong>${esc(utils.formatDateTime(s.created_at))}</strong></div>
      </div>`;
    if (canDeleteShipment(s)) {
      detail.insertAdjacentHTML('beforeend', `
        <div class="inv-detail-danger">
          <button type="button" class="inv-danger-outline" id="btnDeleteShipment">
            <i data-lucide="trash-2"></i><span>Eliminar cargamento</span>
          </button>
          <small>${hasPerm('inventory.adjust') ? 'Si se cargó por error.' : 'Si te equivocaste: podés borrarlo hasta 24 horas después de cargarlo.'}</small>
        </div>`);
      $('btnDeleteShipment').addEventListener('click', () => openShipmentDelete(s));
    }
    utils.renderIcons();
    shipmentInsightsFor(s.id)
      .then((ins) => {
        const box = $('shipmentInsightsBox');
        if (!box || state.selected.shipment !== s.id) return;
        box.innerHTML = shipmentInsightsHtml(ins);
        utils.renderIcons();
      })
      .catch(() => {
        const box = $('shipmentInsightsBox');
        if (box) box.innerHTML = '<p class="inv-ca-foot">No se pudo calcular el contexto.</p>';
      });
  }

  // ---- Eliminar cargamento (cargado por error) ----
  // Mismas reglas que el servidor, solo para no mostrar un botón que va a dar 403. Si después se
  // contaron esos insumos el servidor igual lo rechaza (409) y el motivo se muestra en el modal.
  function canDeleteShipment(s) {
    if (hasPerm('inventory.adjust')) return true;
    if (!state.user || s.received_by_user_id !== state.user.id) return false;
    return Date.now() - (utils._parseServerDate(s.created_at) || new Date(0)).getTime() <= WASTE_SELF_DELETE_MS;
  }

  let shipmentToDelete = null;

  function openShipmentDelete(s) {
    shipmentToDelete = s;
    $('shipmentDeleteError').style.display = 'none';
    $('shipmentDeleteReason').value = '';
    $('shipmentDeleteTitle').textContent = `Eliminar cargamento #${s.id}`;
    const insumos = s.items.map((l) => `${qty(l.quantity)} ${unitShort(l.unit)} de ${l.item_name}`).join(', ');
    $('shipmentDeleteSummary').textContent =
      `${s.supplier_name || 'Sin proveedor'} · ${insumos}${s.total_cost != null ? ` · ${money(s.total_cost)}` : ''}`;
    openModal('modalShipmentDelete');
    $('shipmentDeleteReason').focus();
  }

  $('btnConfirmShipmentDelete')?.addEventListener('click', async () => {
    if (!shipmentToDelete) return;
    const btn = $('btnConfirmShipmentDelete');
    btn.disabled = true;
    btn.textContent = 'Eliminando...';
    try {
      const motivo = $('shipmentDeleteReason').value.trim();
      await api.delete(`/inventory/shipments/${shipmentToDelete.id}${motivo ? `?motivo=${encodeURIComponent(motivo)}` : ''}`);
      closeModal('modalShipmentDelete');
      shipmentInsightsCache.clear();
      utils.showToast(`Cargamento #${shipmentToDelete.id} eliminado.`, 'success');
      shipmentToDelete = null;
      state.selected.shipment = null;
      closeAllMobileDetails();
      await Promise.all([loadShipments({ reset: true }), loadAnalytics(), loadStock()]);
      renderResumen();
      renderItemList();
      renderSupplierList();
    } catch (err) {
      showModalError('shipmentDeleteError', err.message || 'No se pudo eliminar el cargamento.');
    } finally {
      btn.disabled = false;
      btn.textContent = 'Eliminar cargamento';
    }
  });

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
  // Tipo de insumo (viene de Invu): una preparación hecha en la casa o materia prima comprada.
  const KIND_LABELS = { casa: 'De la casa', materia_prima: 'Materia prima' };
  const kindBadge = (i) => (i.kind === 'casa'
    ? '<span class="inv-badge kind-house">De la casa</span>'
    : i.kind === 'materia_prima' ? '<span class="inv-badge kind-raw">Materia prima</span>' : '');

  function renderItemList() {
    const q = state.search.item.trim().toLowerCase();
    const rows = state.items.filter((i) =>
      (!state.itemKind || i.kind === state.itemKind) &&
      (!q || `${i.name} ${i.code || ''} ${i.unit} ${i.category || ''} ${KIND_LABELS[i.kind] || ''}`.toLowerCase().includes(q))
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
      // Con el tipo a la vista, "Sin registros" se omite: con 160 insumos recién traídos de Invu
      // la lista entera decía lo mismo. Queda el de "Recibido", que sí distingue.
      const badge = stats.shipments
        ? `<span class="inv-badge ok">Recibido</span>`
        : (i.kind ? '' : `<span class="inv-badge warn">Sin registros</span>`);
      return `
        <button type="button" class="inv-row${active}" data-item-id="${i.id}">
          <span class="inv-row-thumb"><i data-lucide="${i.kind === 'casa' ? 'chef-hat' : 'package'}"></i></span>
          <span class="inv-row-info">
            <strong>${esc(i.name)}</strong>
            <small>${i.code ? `${esc(i.code)} · ` : ''}${esc(i.unit)}${i.category ? ` · ${esc(i.category)}` : ''}</small>
          </span>
          ${kindBadge(i)}
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
        <span class="inv-detail-thumb"><i data-lucide="${item.kind === 'casa' ? 'chef-hat' : 'package'}"></i></span>
        <span class="inv-chip-row">
          ${kindBadge(item)}
          ${item.invu_id ? '<span class="inv-badge invu" title="Viene de Invu">Invu</span>' : ''}
          ${stats.shipments ? '<span class="inv-badge ok">Recibido</span>' : '<span class="inv-badge warn">Sin registros</span>'}
        </span>
      </div>
      <h3>${esc(item.name)}</h3>
      <p class="inv-detail-sub">Se cuenta en ${esc(item.unit)}${item.category ? ` · ${esc(item.category)}` : ''}</p>
      <div class="inv-metrics">
        <div><span>Recibido</span><strong>${esc(qty(stats.quantity))} <small>${esc(item.unit)}</small></strong></div>
        <div><span>Gasto acumulado</span><strong>${stats.spend > 0 ? money(stats.spend) : '—'}</strong></div>
        <div><span>Veces recibido</span><strong>${stats.shipments}</strong></div>
        <div><span>Costo promedio</span><strong>${avgCost != null ? unitCost(avgCost) : '—'} <small>por ${esc(item.unit)}</small></strong></div>
      </div>
      ${suppliers.length ? `
        <div class="inv-detail-section-header"><span>Quién lo trae</span></div>
        <div class="inv-chip-row">${suppliers.map((n) => `<span class="inv-chip">${esc(n)}</span>`).join('')}</div>` : ''}
      <div class="inv-detail-section-header"><span>Detalles</span></div>
      <div class="inv-detail-rows">
        ${item.code ? `<div><span>Código en Invu</span><strong>${esc(item.code)}</strong></div>` : ''}
        <div><span>Tipo</span><strong>${esc(KIND_LABELS[item.kind] || 'Sin clasificar')}</strong></div>
        <div><span>Unidad</span><strong>${esc(item.unit)}</strong></div>
        <div><span>Categoría</span><strong>${esc(item.category || 'Sin categoría')}</strong></div>
        ${item.reference_cost != null ? `<div><span>Costo de referencia (Invu)</span><strong>${esc(unitCost(item.reference_cost))} <small>por ${esc(item.unit)}</small></strong></div>` : ''}
        <div><span>Última recepción</span><strong>${stats.last ? esc(utils.formatDate(stats.last.toISOString())) : 'Nunca'}</strong></div>
        <div><span>Sucursales que lo reciben</span><strong>${branches.length ? esc(branches.join(', ')) : '—'}</strong></div>
        <div><span>Estado</span><strong>${item.active ? 'Activo' : 'Inactivo'}</strong></div>
      </div>
      ${pieceSizeHtml(item)}`;
    utils.renderIcons();
    wirePieceSizeEditor(item);
  }

  // Cuánto es una pieza entera (para las mermas de "pieza entera"). Lo ve todo el mundo; lo
  // cambia supervisor o admin. Si nadie lo cargó, se aprende de la primera merma que lo diga.
  function pieceSizeHtml(item) {
    const fam = unitFamily(item.unit);
    const u = fam.fam === 'volumen' ? 'ml' : 'g';
    const valor = item.piece_size != null ? `${qty(item.piece_size)} ${u}` : 'Sin cargar';
    const ayuda = fam.fam === 'unidad'
      ? 'Lo que pesa una. Sirve para pasar a gramos lo que se bota y para mermas de una parte.'
      : `Con esto, "se botó 1 pieza entera" se convierte solo a ${esc(unitShort(item.unit))}.`;
    const editable = hasPerm('inventory.adjust');
    return `
      <div class="inv-detail-section-header"><span>Una pieza entera</span></div>
      <div class="inv-piece-size">
        <div class="inv-piece-size-value"><strong>${esc(valor)}</strong><small>${ayuda}</small></div>
        ${editable ? `
          <div class="inv-piece-size-edit">
            <input type="number" class="modal-input" id="itemPieceSizeInput" min="0" step="0.1" inputmode="decimal"
                   placeholder="${fam.fam === 'volumen' ? 'Ej. 330' : 'Ej. 80'}" value="${item.piece_size != null ? esc(String(Number(item.piece_size))) : ''}"
                   aria-label="${fam.fam === 'volumen' ? 'Mililitros' : 'Gramos'} de una pieza">
            <span>${u}</span>
            <button type="button" class="btn-secondary" id="btnSavePieceSize">Guardar</button>
          </div>` : ''}
      </div>`;
  }

  function wirePieceSizeEditor(item) {
    $('btnSavePieceSize')?.addEventListener('click', async () => {
      const raw = $('itemPieceSizeInput').value.trim();
      if (raw && !(Number(raw) > 0)) { utils.showToast('Tiene que ser mayor que cero.', 'error'); return; }
      const btn = $('btnSavePieceSize');
      btn.disabled = true;
      try {
        const actualizado = await api.request(`/inventory/items/${item.id}/piece-size`, {
          method: 'PATCH',
          body: JSON.stringify({ piece_size: raw || null }),
        });
        item.piece_size = actualizado.piece_size;
        utils.showToast(raw ? 'Guardado.' : 'Borrado.', 'success');
        renderItemDetail();
      } catch (err) {
        utils.showToast(err.message || 'No se pudo guardar.', 'error');
        btn.disabled = false;
      }
    });
  }

  $('itemSearch')?.addEventListener('input', (e) => {
    state.search.item = e.target.value;
    renderItemList();
  });

  $('itemKindFilter')?.addEventListener('change', (e) => {
    state.itemKind = e.target.value;
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
    shipmentCostConfirmed = '';
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
      itemInput.dataset.unit = item.unit || '';
      unitLabel.textContent = item.unit ? `Se cuenta en ${item.unit}` : '';
      updateShipmentCostHint(row);
      // El costo de Invu como sugerencia, no como valor: si no se escribe nada, el cargamento
      // queda sin costo (como siempre) en vez de guardar uno que nadie confirmó.
      if (!('defaultPlaceholder' in costInput.dataset)) costInput.dataset.defaultPlaceholder = costInput.placeholder;
      costInput.placeholder = item.reference_cost != null
        ? `Ref. ${unitCost(item.reference_cost)}`
        : costInput.dataset.defaultPlaceholder;
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
    costInput.addEventListener('input', () => { updateShipmentTotal(); updateShipmentCostHint(row); });

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
  let shipmentCostConfirmed = '';   // costos raros ya confirmados con un segundo toque
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

    const sospechosos = rows.map((row) => {
      const unit = row.querySelector('.inv-item-input').dataset.unit;
      const costo = row.querySelector('.inv-line-cost').value;
      const porMil = costoPorMil(costo !== '' ? Number(costo) : null, unit);
      return porMil != null && porMil > 100
        ? `${row.querySelector('.inv-item-input').value} (${money(porMil)} el ${unitFamily(unit).fam === 'volumen' ? 'litro' : 'kilo'})`
        : null;
    }).filter(Boolean);
    const firma = sospechosos.join('|');
    if (sospechosos.length && shipmentCostConfirmed !== firma) {
      shipmentCostConfirmed = firma;
      showModalError('shipmentError', `Revisá el costo de ${sospechosos.join(', ')}: parece el precio del paquete y va por unidad del insumo (g, ml). Si está bien, tocá "Registrar cargamento" otra vez.`);
      return;
    }

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
      const creado = await api.post('/inventory/shipments', {
        branch_id: branchId,
        received_at: receivedAt,
        supplier_id: state.selectedSupplierId ? Number(state.selectedSupplierId) : null,
        notes: $('notesInput').value.trim() || null,
        items,
      });
      closeModal('modalShipment');
      showShipmentResult(creado);
      state.selected.shipment = null;
      // loadStock también: Existencias quedaba mostrando lo de antes del cargamento hasta
      // recargar la página (merma y conteo ya la refrescaban).
      await Promise.all([loadShipments({ reset: true }), loadAnalytics(), loadStock()]);
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
      if (line.mode === 'entera' && line.pieces != null) {
        const n = Number(line.pieces);
        return `${qty(n)} ${n === 1 ? 'entera' : 'enteras'}`;
      }
      return `${qty(line.quantity)} ${unitShort(line.unit)}`;
    }
    return pluralize(w.items.length, 'insumo', 'insumos');
  }

  /** "2 piezas enteras · 160 g", "40 g · una parte", o la cantidad a secas (mermas anteriores). */
  function wasteLineQtyTxt(l) {
    const enUnidad = `${qty(l.quantity)} ${unitShort(l.unit)}`;
    const fam = unitFamily(l.unit);
    const mu = fam.fam === 'volumen' ? 'ml' : 'g';
    if (l.mode === 'entera' && l.pieces != null) {
      const n = Number(l.pieces);
      const piezas = `${qty(n)} ${n === 1 ? 'pieza entera' : 'piezas enteras'}`;
      if (l.measured_amount != null) {
        return `${piezas} · ${fam.fam === 'unidad' ? `${qty(l.measured_amount)} ${mu}` : enUnidad} pesado`;
      }
      // Sin pesar: el peso es el promedio de la pieza (≈). Por unidad la cantidad es exacta.
      return fam.fam === 'unidad' ? piezas : `${piezas} · ≈ ${enUnidad} (peso promedio)`;
    }
    if (l.mode === 'parte') {
      if (fam.fam === 'unidad' && (l.measured_amount != null || l.piece_size)) {
        // 0.333 × 90 g da 29.97: se redondea a gramo entero (a décima si es menos de 10 g).
        const g = l.measured_amount != null ? Number(l.measured_amount) : Number(l.quantity) * Number(l.piece_size);
        return `${Number(g.toFixed(g >= 10 ? 0 : 1))} g · una parte (${enUnidad})`;
      }
      return `${enUnidad} · una parte`;
    }
    return enUnidad;
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
          ${(w.photos || []).length ? `<span class="inv-photo-flag" title="${pluralize(w.photos.length, 'foto', 'fotos')}" aria-label="Con foto"><i data-lucide="camera"></i></span>` : ''}
          <span class="inv-badge muted">${esc(wasteQuantityLabel(w))}</span>
          <span class="inv-row-amount inv-row-amount-waste">${wasteCostValue(w) != null ? `${w.cost_estimated ? '≈ ' : ''}-${money(wasteCostValue(w))}` : '—'}</span>
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

  // ---- Contexto de una merma ----
  // Al guardar (y en el detalle): cuánto va de ese insumo en la semana y el mes, si el motivo se
  // repite, en qué puesto está y qué parte de lo usado se botó. Sale de GET /waste/{id}/insights.
  const wasteInsightsCache = new Map();

  // Qué hacer, según el motivo. Corto: es para leerlo parado en la cocina.
  const REASON_TIPS = {
    vencido: 'Revisá la rotación (lo primero que entra, primero sale) y si conviene pedir menos.',
    danado: 'Revisá cómo llega y cómo se guarda: los golpes suelen ser de la recepción o del almacenamiento.',
    derrame: 'Mirá dónde y cómo se manipula: recipientes, estantes, traslados.',
    error_preparacion: 'Si se repite, conviene repasar la receta o el procedimiento con el equipo.',
    recorte: 'Es merma de proceso: lo importante es cuánto se aprovecha de lo que se limpia.',
    devolucion: 'Anotá qué reclamó el cliente: así se puede corregir.',
    consumo_interno: 'Si es comida del personal, conviene tener una regla clara de qué y cuánto.',
    faltante: 'Hacé un conteo de ese insumo para ver si falta más.',
  };

  const ordinal = (n) => `${n}.ª`;   // «la 3.ª vez»
  const puesto = (n) => (n === 1 ? 'el insumo que más plata se pierde' : `el ${n}.º insumo que más plata se pierde`);

  function trendTxt(ahora, antes) {
    const a = Number(ahora);
    const b = Number(antes);
    if (!b) return '';
    const pct = Math.round(((a - b) / b) * 100);
    if (Math.abs(pct) < 5) return ', parecido a la semana anterior';
    if (pct > 300) return `, <strong>mucho más</strong> que la semana anterior (${esc(money(b))})`;
    return pct > 0 ? `, <strong>${pct}% más</strong> que la semana anterior (${esc(money(b))})` : `, ${Math.abs(pct)}% menos que la semana anterior`;
  }

  function wasteInsightItemHtml(it, ins) {
    const aprox = it.cost_estimated ? '≈ ' : '';
    const u = unitShort(it.unit);
    const motivo = (REASON_UI[ins.reason] || { label: ins.reason_label }).label;
    const frases = [];
    frases.push(`Esta semana van <strong>${esc(qty(it.week_quantity))} ${esc(u)}</strong> botados (${aprox}${esc(money(it.week_cost))}) en ${pluralize(it.week_records, 'merma', 'mermas')}${trendTxt(it.week_cost, it.prev_week_cost)}.`);
    if (it.same_reason_month >= 2) {
      frases.push(`Es la <strong>${ordinal(it.same_reason_month)} vez</strong> este mes por «${esc(motivo)}».`);
    }
    if (it.rank_month && it.items_ranked > 1 && it.rank_month <= 3) {
      frases.push(`Es ${puesto(it.rank_month)} este mes en ${esc(ins.branch_name)} (${aprox}${esc(money(it.month_cost))}).`);
    }
    if (it.waste_pct_month != null) {
      const pct = Number(it.waste_pct_month);
      frases.push(`De todo lo que se usó este mes, se botó el <strong>${pct.toLocaleString('es-PA', { maximumFractionDigits: 1 })}%</strong>${pct > 5 ? ': arriba de 5% vale revisarlo' : ''}.`);
    }
    return `
      <div class="inv-ca-line">
        <div class="inv-ca-line-head">
          <strong>${esc(it.name)}</strong>
          ${it.this_cost != null ? `<span class="inv-ca-chip inv-ca-chip-short">${aprox}${esc(money(it.this_cost))}</span>` : ''}
        </div>
        <ul class="inv-wi-list">${frases.map((f) => `<li>${f}</li>`).join('')}</ul>
      </div>`;
  }

  function wasteInsightsHtml(ins) {
    const tip = REASON_TIPS[ins.reason];
    return `
      ${ins.items.map((it) => wasteInsightItemHtml(it, ins)).join('')}
      <p class="inv-wi-branch">En ${esc(ins.branch_name)} van <strong>${esc(money(ins.branch_week_cost))}</strong> de merma en los últimos 7 días (${pluralize(ins.branch_week_records, 'merma', 'mermas')})${trendTxt(ins.branch_week_cost, ins.branch_prev_week_cost)}.</p>
      ${tip ? `<p class="inv-wi-tip"><i data-lucide="lightbulb"></i><span>${esc(tip)}</span></p>` : ''}`;
  }

  async function wasteInsightsFor(id) {
    if (wasteInsightsCache.has(id)) return wasteInsightsCache.get(id);
    const ins = await api.get(`/inventory/waste/${id}/insights`);
    wasteInsightsCache.set(id, ins);
    return ins;
  }

  /** Lo que se ve al guardar una merma, en vez de solo "Merma registrada". */
  function showWasteResult(merma, { fotosFallidas = 0 } = {}) {
    wasteInsightsCache.clear();
    if (merma.insights) wasteInsightsCache.set(merma.id, merma.insights);
    const avisos = [];
    if (fotosFallidas) {
      avisos.push(`${pluralize(fotosFallidas, 'foto no se subió', 'fotos no se subieron')}: se ${fotosFallidas === 1 ? 'puede' : 'pueden'} agregar desde el detalle de la merma.`);
    }
    if (merma.negative_items && merma.negative_items.length) {
      avisos.push(`${esc(merma.negative_items.join(', '))} ${merma.negative_items.length === 1 ? 'queda' : 'quedan'} en negativo: falta el conteo de arranque de la sucursal.`);
    }
    $('wasteResultTitle').textContent = `Merma #${merma.id} registrada`;
    $('wasteResultSubtitle').textContent = `${merma.branch_name} · se pierde ${wasteCostTxt(merma)}`;
    $('wasteResultBody').innerHTML = `
      ${avisos.map((a) => `<p class="inv-wi-warn">${a}</p>`).join('')}
      ${merma.insights ? wasteInsightsHtml(merma.insights) : ''}`;
    openModal('modalWasteResult');
  }

  function renderWasteDetail() {
    const detail = $('wasteDetail');
    const w = state.waste.find((x) => x.id === state.selected.waste);
    if (!w) {
      detail.innerHTML = emptyStateHtml('mouse-pointer-click', 'Elegí una merma', 'Su detalle — insumos, cantidades y pérdida — aparece acá.');
      utils.renderIcons();
      return;
    }

    const fotos = w.photos || [];
    const rowsHtml = w.items.map((l) => {
      // Sin cargamento con costo, el de Invu marcado con ≈ (igual que en la lista y el análisis).
      const costo = l.unit_cost != null ? Number(l.unit_cost) : (l.reference_cost != null ? Number(l.reference_cost) : null);
      const aprox = l.unit_cost == null && l.reference_cost != null ? '≈ ' : '';
      const subtotal = costo != null ? `${aprox}${money(Number(l.quantity) * costo)}` : '—';
      return `
        <tr>
          <td class="inv-td-name" data-label="Insumo">${esc(l.item_name)}</td>
          <td class="num" data-label="Cantidad">${esc(wasteLineQtyTxt(l))}</td>
          <td class="num" data-label="Costo unit.">${costo != null ? `${aprox}${unitCost(costo)}` : '—'}</td>
          <td class="num" data-label="Pérdida">${subtotal}</td>
        </tr>`;
    }).join('');

    const footHtml = wasteCostValue(w) != null ? `
      <tfoot>
        <tr>
          <td colspan="3" class="inv-td-total-label">Pérdida total</td>
          <td class="num" data-label="Pérdida total">${esc(wasteCostTxt(w))}</td>
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
        <div><span>Pérdida</span><strong>${esc(wasteCostTxt(w))}${w.cost_estimated ? ' <small>costo de Invu</small>' : ''}</strong></div>
        <div><span>Peso</span><strong>${w.weight_value != null ? `${w.weight_estimated ? '≈ ' : ''}${esc(qty(w.weight_value))} <small>${esc(w.weight_unit || 'kg')}${w.weight_estimated ? ' · estimado' : ''}</small>` : '—'}</strong></div>
        <div><span>Evidencia</span><strong>${fotos.length ? `${fotos.length} <small>${fotos.length === 1 ? 'foto' : 'fotos'}</small>` : '—'}</strong></div>
        ${w.is_process && w.processed_value != null ? `
          <div><span>Se limpió</span><strong>${esc(qty(w.processed_value))} <small>${esc(w.processed_unit || 'kg')}</small></strong></div>
          <div><span>Rendimiento</span><strong>${w.yield_pct != null ? `${Number(w.yield_pct).toLocaleString('es-PA', { maximumFractionDigits: 1 })}% <small>aprovechado</small>` : '—'}</strong></div>` : ''}
      </div>
      ${w.is_process ? '<p class="inv-detail-note">Merma de proceso: lo que se saca al limpiar o preparar. En el análisis va aparte de la merma evitable.</p>' : ''}
      <div class="inv-detail-section-header"><span>Insumos perdidos</span></div>
      <table class="inv-detail-table">
        <thead>
          <tr><th>Insumo</th><th class="num">Cantidad</th><th class="num">Costo unit.</th><th class="num">Pérdida</th></tr>
        </thead>
        <tbody>${rowsHtml}</tbody>
        ${footHtml}
      </table>
      <div class="inv-detail-section-header"><span>En contexto</span></div>
      <div class="inv-waste-insights" id="wasteInsightsBox"><div class="inv-ca-loading">Calculando…</div></div>
      <div class="inv-detail-section-header"><span>Evidencia</span></div>
      <div class="inv-photo-grid" id="wasteDetailPhotos">
        ${fotos.map((p) => `<button type="button" class="inv-photo-thumb is-loading" data-photo-id="${p.id}" aria-label="Ver foto (subida por ${esc(p.uploaded_by_name || 'alguien')})"></button>`).join('')}
        ${fotos.length < WASTE_PHOTOS_MAX ? `
          <div class="inv-photo-add-group">
            <button type="button" class="inv-photo-add" data-photo-camera="detail">
              <i data-lucide="camera"></i><span>Tomar foto</span>
            </button>
            <button type="button" class="inv-photo-add inv-photo-add-secondary" id="btnAddWastePhoto">
              <i data-lucide="image"></i><span>Galería</span>
            </button>
          </div>` : ''}
      </div>
      ${fotos.length ? '' : '<p class="inv-photo-empty">Esta merma no tiene foto de evidencia.</p>'}
      <div class="inv-detail-section-header"><span>Detalles</span></div>
      <div class="inv-detail-rows">
        <div><span>Sucursal</span><strong>${esc(w.branch_name)}</strong></div>
        <div><span>Registrado por</span><strong>${esc(w.recorded_by_name)}</strong></div>
        <div><span>Ocurrió el</span><strong>${esc(utils.formatDateTime(w.occurred_at))}</strong></div>
        <div><span>Cargado al sistema</span><strong>${esc(utils.formatDateTime(w.created_at))}</strong></div>
      </div>
      ${canDeleteWaste(w) ? `
        <div class="inv-detail-danger">
          <button type="button" class="inv-danger-outline" id="btnDeleteWaste">
            <i data-lucide="trash-2"></i><span>Eliminar merma</span>
          </button>
          <small>${hasPerm('inventory.adjust') ? 'Si se cargó por error.' : 'Si te equivocaste: podés borrarla hasta 24 horas después de cargarla.'}</small>
        </div>` : ''}`;
    utils.renderIcons();
    wirePhotoGallery(w);
    $('btnDeleteWaste')?.addEventListener('click', () => openWasteDelete(w));
    wasteInsightsFor(w.id)
      .then((ins) => {
        const box = $('wasteInsightsBox');
        if (!box || state.selected.waste !== w.id) return;
        box.innerHTML = wasteInsightsHtml(ins);
        utils.renderIcons();
      })
      .catch(() => {
        const box = $('wasteInsightsBox');
        if (box) box.innerHTML = '<p class="inv-ca-foot">No se pudo calcular el contexto.</p>';
      });
  }

  // ---- Eliminar merma (cargada por error) ----
  // Mismas reglas que el servidor, solo para no mostrar un botón que va a dar 403: supervisor y
  // admin cualquiera que vean; quien la cargó, la suya durante las primeras 24 horas.
  function hasPerm(code) {
    return (state.user?.permissions || []).includes(code);
  }

  function canDeleteWaste(w) {
    if (hasPerm('inventory.adjust')) return true;
    if (!state.user || w.recorded_by_user_id !== state.user.id) return false;
    return Date.now() - (utils._parseServerDate(w.created_at) || new Date(0)).getTime() <= WASTE_SELF_DELETE_MS;
  }

  let wasteToDelete = null;

  function openWasteDelete(w) {
    wasteToDelete = w;
    $('wasteDeleteError').style.display = 'none';
    $('wasteDeleteReason').value = '';
    $('wasteDeleteTitle').textContent = `Eliminar merma #${w.id}`;
    const insumos = w.items.map((l) => `${wasteLineQtyTxt(l)} de ${l.item_name}`).join(', ');
    $('wasteDeleteSummary').textContent =
      `${w.reason_label} · ${insumos}${wasteCostValue(w) != null ? ` · ${wasteCostTxt(w)}` : ''}`;
    openModal('modalWasteDelete');
    $('wasteDeleteReason').focus();
  }

  $('btnConfirmWasteDelete')?.addEventListener('click', async () => {
    if (!wasteToDelete) return;
    const btn = $('btnConfirmWasteDelete');
    btn.disabled = true;
    btn.textContent = 'Eliminando...';
    try {
      const motivo = $('wasteDeleteReason').value.trim();
      await api.delete(`/inventory/waste/${wasteToDelete.id}${motivo ? `?motivo=${encodeURIComponent(motivo)}` : ''}`);
      closeModal('modalWasteDelete');
      wasteInsightsCache.clear();
      utils.showToast(`Merma #${wasteToDelete.id} eliminada.`, 'success');
      wasteToDelete = null;
      state.selected.waste = null;
      closeAllMobileDetails();
      await Promise.all([loadWaste({ reset: true }), loadWasteAnalytics(), loadStock()]);
      renderResumen();
      if (wasteAnalysis.tab === 'analisis') loadWasteAnalysis();
    } catch (err) {
      showModalError('wasteDeleteError', err.message || 'No se pudo eliminar la merma.');
    } finally {
      btn.disabled = false;
      btn.textContent = 'Eliminar merma';
    }
  });

  $('wasteSearch')?.addEventListener('input', (e) => {
    state.search.waste = e.target.value;
    renderWasteList();
  });

  $('wasteReasonFilter')?.addEventListener('change', (e) => {
    state.wasteReasonFilter = e.target.value;
    state.selected.waste = null;
    loadWaste({ reset: true });
  });

  // ==========================================================================
  // Merma → Análisis (los números salen de GET /inventory/waste/analytics)
  // ==========================================================================
  const wasteAnalysis = { tab: 'registros', period: '30', branch: '', metric: 'cost', data: null, recipes: null, seq: 0 };

  /** Hoy en Panamá (YYYY-MM-DD): los días de la merma y de las ventas se cuentan en esa hora. */
  function hoyPanama() {
    return new Date().toLocaleDateString('en-CA', { timeZone: 'America/Panama' });
  }
  function isoMasDias(iso, dias) {
    const d = new Date(`${iso}T12:00:00Z`);
    d.setUTCDate(d.getUTCDate() + dias);
    return d.toISOString().slice(0, 10);
  }
  function periodoMerma(p) {
    const hoy = hoyPanama();
    if (p === '7') return { from: isoMasDias(hoy, -6), to: hoy };
    if (p === 'mes') return { from: `${hoy.slice(0, 7)}-01`, to: hoy };
    if (p === 'mes-anterior') {
      const finAnterior = isoMasDias(`${hoy.slice(0, 7)}-01`, -1);
      return { from: `${finAnterior.slice(0, 7)}-01`, to: finAnterior };
    }
    return { from: isoMasDias(hoy, -29), to: hoy };
  }
  const fechaCorta = (iso) => new Date(`${iso}T12:00:00Z`).toLocaleDateString('es-PA', { day: 'numeric', month: 'short', timeZone: 'UTC' });
  const kgTxt = (n) => `${qty(n)} kg`;

  function setWasteTab(tab) {
    wasteAnalysis.tab = tab;
    document.querySelectorAll('#wasteTabs [data-waste-tab]').forEach((b) => {
      const on = b.dataset.wasteTab === tab;
      b.classList.toggle('active', on);
      b.setAttribute('aria-selected', on ? 'true' : 'false');
    });
    $('wasteAnalysis').hidden = tab !== 'analisis';
    $('wasteRecordsPane').hidden = tab !== 'registros';
    if (tab === 'analisis') loadWasteAnalysis();
  }
  document.querySelectorAll('#wasteTabs [data-waste-tab]').forEach((b) => b.addEventListener('click', () => setWasteTab(b.dataset.wasteTab)));

  function segmentado(id, attr, onPick) {
    document.querySelectorAll(`#${id} [data-${attr}]`).forEach((b) => b.addEventListener('click', () => {
      document.querySelectorAll(`#${id} [data-${attr}]`).forEach((x) => x.classList.toggle('active', x === b));
      onPick(b.dataset[attr]);
    }));
  }
  segmentado('wastePeriod', 'period', (p) => { wasteAnalysis.period = p; loadWasteAnalysis(); });
  segmentado('wasteMetric', 'metric', (m) => { wasteAnalysis.metric = m; renderWasteAnalysis(); });
  $('wasteAnalysisBranch')?.addEventListener('change', (e) => { wasteAnalysis.branch = e.target.value; loadWasteAnalysis(); });

  async function loadWasteAnalysis() {
    const sel = $('wasteAnalysisBranch');
    if (state.isGlobalScope && sel && !sel.options.length) {
      sel.innerHTML = '<option value="">Todas las sucursales</option>' +
        state.branches.map((b) => `<option value="${b.id}">${esc(b.name)}</option>`).join('');
    }
    if (sel) sel.hidden = !state.isGlobalScope;

    const { from, to } = periodoMerma(wasteAnalysis.period);
    const params = new URLSearchParams({ date_from: from, date_to: to });
    if (wasteAnalysis.branch) params.set('branch_id', wasteAnalysis.branch);
    const seq = ++wasteAnalysis.seq;
    $('wasteKpis').innerHTML = '<div class="inv-kpi inv-kpi-skeleton"></div>'.repeat(4);
    // El cruce con recetas va aparte: si falla (o todavía no hay recetas) no tapa el resto.
    api.get(`/inventory/waste/recipe-usage?${params}`)
      .then((r) => { if (seq === wasteAnalysis.seq) { wasteAnalysis.recipes = r; renderWasteRecipes(); } })
      .catch(() => { if (seq === wasteAnalysis.seq) { wasteAnalysis.recipes = null; renderWasteRecipes(); } });
    try {
      const data = await api.get(`/inventory/waste/analytics?${params}`);
      if (seq !== wasteAnalysis.seq) return;   // llegó otra más nueva (se cambió el período)
      wasteAnalysis.data = data;
      renderWasteAnalysis();
    } catch (err) {
      if (seq !== wasteAnalysis.seq) return;
      $('wasteKpis').innerHTML = '';
      utils.showToast(err.message || 'No se pudo calcular la merma.', 'error');
    }
  }

  /**
   * Tramos del gráfico de tendencia: por día si el período es corto (7 días), por semana si es
   * largo. Treinta barras finitas con días vacíos entre medio no se leían de un vistazo; cuatro
   * o cinco semanas con su monto escrito encima, sí.
   */
  function tramosMerma(dias) {
    if (dias.length <= 14) {
      return dias.map((d) => ({ ...d, cost: Number(d.cost), kg: Number(d.kg), label: fechaCorta(d.date), largo: fechaCorta(d.date) }));
    }
    // Semanas contadas desde el final: las recientes (las que importan) quedan completas y el
    // tramo parcial, si lo hay, es el más viejo. Un tramo de 2 días no puede competir como "la
    // semana más alta" contra semanas de 7, así que se marca.
    const tramos = [];
    for (let fin = dias.length; fin > 0; fin -= 7) {
      const grupo = dias.slice(Math.max(0, fin - 7), fin);
      const ini = grupo[0].date;
      const ult = grupo[grupo.length - 1].date;
      const mismoMes = ini.slice(0, 7) === ult.slice(0, 7);
      const parcial = grupo.length < 7;
      tramos.unshift({
        label: (mismoMes ? `${Number(ini.slice(8))}–${fechaCorta(ult)}` : `${fechaCorta(ini)}–${fechaCorta(ult)}`) + (parcial ? '*' : ''),
        largo: `del ${fechaCorta(ini)} al ${fechaCorta(ult)}` + (parcial ? ` (solo ${grupo.length} días)` : ''),
        parcial,
        cost: grupo.reduce((s, d) => s + Number(d.cost), 0),
        kg: grupo.reduce((s, d) => s + Number(d.kg), 0),
        records: grupo.reduce((s, d) => s + d.records, 0),
      });
    }
    return tramos;
  }

  /** Paso "redondo" del eje (4 líneas): 14.56 → 4 (0, 4, 8, 12, 16); 25.27 → 8 (hasta 32). */
  function pasoRedondo(max) {
    if (max <= 0) return 1;
    const crudo = max / 4;
    const mag = 10 ** Math.floor(Math.log10(crudo));
    for (const p of [1, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10]) {
      if (p * mag >= crudo) return p * mag;
    }
    return 10 * mag;
  }

  function renderWasteTrend() {
    const a = wasteAnalysis.data;
    const box = $('wasteDayChart');
    if (!a || !box) return;
    const porKg = wasteAnalysis.metric === 'kg';
    const valorDe = (x) => (porKg ? x.kg : x.cost);
    const fmtEje = (n) => (porKg ? `${qty(n)} kg` : `$${Number(n).toLocaleString('es-PA', { maximumFractionDigits: n < 10 ? 2 : 0 })}`);
    const fmtValor = (n) => (porKg ? kgTxt(n) : money(n));

    const tramos = tramosMerma(a.by_day);
    const semanal = a.by_day.length > 14;
    const unidadTramo = semanal ? 'semana' : 'día';
    $('wasteDayTitle').textContent = `Pérdida por ${unidadTramo}`;
    $('wasteDayNote').textContent = porKg ? 'En kilos' : 'En dólares';

    if (!a.totals.records) {
      $('wasteDayHeadline').textContent = '';
      box.innerHTML = emptyStateHtml('bar-chart-3', 'Sin mermas en el período', 'Cuando se registren, acá se ve cuánto se perdió en cada tramo.');
      utils.renderIcons();
      return;
    }

    const total = tramos.reduce((s, x) => s + valorDe(x), 0);
    // Promedio y "el más alto" sobre tramos completos cuando los hay (un tramo parcial no compite).
    const completos = tramos.filter((x) => !x.parcial);
    const base = completos.length ? completos : tramos;
    const promedio = base.reduce((s, x) => s + valorDe(x), 0) / base.length;
    const peor = base.reduce((m, x) => (valorDe(x) > valorDe(m) ? x : m), base[0]);
    $('wasteDayHeadline').innerHTML = total > 0
      ? `En estos ${a.by_day.length} días se ${porKg ? 'descartaron' : 'perdieron'} <strong>${esc(fmtValor(total))}</strong>, ` +
        `unos <strong>${esc(fmtValor(promedio))}</strong> por ${unidadTramo}. ` +
        `El${semanal ? ' tramo' : ''} más alto fue ${semanal ? esc(peor.largo) : `el ${esc(peor.largo)}`} (<strong>${esc(fmtValor(valorDe(peor)))}</strong>).`
      : (porKg ? 'Ninguna merma del período tiene kilos conocidos.' : 'Ninguna merma del período tiene costo conocido todavía.');

    // ---- SVG: eje con montos, barras con su valor encima, promedio punteado ----
    const W = Math.max(300, box.clientWidth || 700);
    const H = 250;
    // A la derecha queda lugar para la etiqueta del promedio, afuera de las barras (adentro se
    // encimaba con el monto de la última).
    const M = { top: 26, right: W < 520 ? 58 : 96, bottom: 34, left: 52 };
    const iw = W - M.left - M.right;
    const ih = H - M.top - M.bottom;
    const paso4 = pasoRedondo(Math.max(...tramos.map(valorDe)));
    const max = paso4 * 4;
    const y = (v) => M.top + ih - (v / max) * ih;
    const paso = iw / tramos.length;
    const barW = Math.min(64, paso * 0.62);
    const angosto = paso < 44;   // en el celular con 7 días: los montos se escriben más chicos

    let svg = '';
    for (let i = 0; i <= 4; i++) {
      const v = (max / 4) * i;
      svg += `<line class="inv-trend-grid" x1="${M.left}" x2="${W - M.right}" y1="${y(v)}" y2="${y(v)}"/>`;
      svg += `<text class="inv-trend-axis" x="${M.left - 8}" y="${y(v) + 4}" text-anchor="end">${esc(fmtEje(v))}</text>`;
    }
    tramos.forEach((x, i) => {
      const v = valorDe(x);
      const cx = M.left + paso * i + paso / 2;
      const alto = Math.max(0, M.top + ih - y(v));
      const esPeor = x === peor && v > 0;
      const detalle = `${x.largo}: ${money(x.cost)} · ${kgTxt(x.kg)} · ${pluralize(x.records, 'merma', 'mermas')}`;
      svg += `<g class="inv-trend-bar${esPeor ? ' is-peak' : ''}" tabindex="0" aria-label="${esc(detalle)}"><title>${esc(detalle)}</title>`;
      if (v > 0) {
        svg += `<rect x="${cx - barW / 2}" y="${y(v)}" width="${barW}" height="${Math.max(2, alto)}" rx="5"/>`;
        svg += `<text class="inv-trend-value${angosto ? ' is-small' : ''}" x="${cx}" y="${y(v) - 7}" text-anchor="middle">${esc(fmtValor(v))}</text>`;
      } else {
        svg += `<rect class="is-empty" x="${cx - barW / 2}" y="${M.top + ih - 2}" width="${barW}" height="2" rx="1"/>`;
      }
      svg += `<text class="inv-trend-label" x="${cx}" y="${H - 12}" text-anchor="middle">${esc(x.label)}</text></g>`;
    });
    if (promedio > 0) {
      svg += `<line class="inv-trend-avg" x1="${M.left}" x2="${W - M.right + 4}" y1="${y(promedio)}" y2="${y(promedio)}"/>`;
      svg += `<text class="inv-trend-avg-label" x="${W - M.right + 8}" y="${y(promedio) - 3}">Promedio</text>`;
      svg += `<text class="inv-trend-avg-label is-value" x="${W - M.right + 8}" y="${y(promedio) + 11}">${esc(fmtValor(promedio))}</text>`;
    }
    box.innerHTML = `<svg viewBox="0 0 ${W} ${H}" width="100%" height="${H}" role="img" aria-label="Pérdida por ${unidadTramo}">${svg}</svg>` +
      (tramos.some((x) => x.parcial) ? '<p class="inv-trend-foot">* Semana incompleta: se muestra, pero no entra en el promedio ni compite como la más alta.</p>' : '');
  }

  // El gráfico se dibuja al ancho real de su caja: al cambiar el tamaño de la ventana se redibuja.
  let trendResizeTimer = null;
  window.addEventListener('resize', () => {
    clearTimeout(trendResizeTimer);
    trendResizeTimer = setTimeout(() => { if (wasteAnalysis.tab === 'analisis') renderWasteTrend(); }, 150);
  });

  /** Merma contra el uso real en platos (recetas de Invu) y los platos más afectados. */
  function renderWasteRecipes() {
    const r = wasteAnalysis.recipes;
    const body = $('wasteRecipeBody');
    if (!body) return;
    const puedeTraer = (state.user?.permissions || []).includes('integrations.manage');
    const botonTraer = puedeTraer
      ? `<button type="button" class="inv-secondary-btn" id="btnSyncRecipes"${r?.recipes_running ? ' disabled' : ''}>
           <i data-lucide="refresh-cw"></i> <span>${r?.recipes_running ? 'Trayendo recetas…' : 'Actualizar recetas de Invu'}</span>
         </button>`
      : '';

    if (!r) {
      body.innerHTML = emptyStateHtml('chef-hat', 'No se pudo cruzar con las recetas', 'Probá de nuevo en un momento.');
      $('wasteDishPanel').hidden = true;
      utils.renderIcons();
      return;
    }

    const cobertura = Number(r.sold_units) > 0 ? Math.round((Number(r.sold_units_with_recipe) / Number(r.sold_units)) * 100) : null;
    $('wasteRecipeNote').textContent = r.recipes_synced_at
      ? `Recetas del ${utils.formatDate(r.recipes_synced_at)}${cobertura != null ? ` · ${cobertura}% de lo vendido tiene receta` : ''}`
      : 'Recetas de Invu';

    if (!r.recipes_count) {
      body.innerHTML = `
        ${emptyStateHtml('chef-hat', r.recipes_running ? 'Trayendo las recetas de Invu…' : 'Todavía no se trajeron las recetas de Invu',
          r.recipes_running ? 'Son cientos de platos y modificadores: tarda unos 12 minutos por sucursal. Volvé a abrir esta vista más tarde.'
                            : 'Se traen solas una vez por semana. Con ellas se calcula cuánto de cada insumo fue a los platos vendidos.')}
        ${botonTraer ? `<div class="inv-recipe-actions">${botonTraer}</div>` : ''}`;
      $('wasteDishPanel').hidden = true;
      wireSyncRecipes();
      utils.renderIcons();
      return;
    }

    const nivel = (pct) => (pct == null ? '' : pct >= 8 ? 'bad' : pct >= 3 ? 'warn' : 'ok');
    const filas = r.items;
    body.innerHTML = filas.length ? `
      <table class="inv-detail-table inv-recipe-table">
        <thead>
          <tr>
            <th>Insumo</th>
            <th class="num">Fue a platos<small>según recetas</small></th>
            <th class="num">Se botó<small>merma</small></th>
            <th class="num">% merma<small>de lo que pasó por cocina</small></th>
            <th>Dónde más se usa</th>
          </tr>
        </thead>
        <tbody>
          ${filas.map((f) => {
            const pct = f.waste_pct != null ? Number(f.waste_pct) : null;
            return `
              <tr>
                <td class="inv-td-name" data-label="Insumo">${esc(f.name)}${f.kind === 'casa' ? ' <span class="inv-badge kind-house">Casa</span>' : ''}
                  <small>${Number(f.wasted_cost) > 0 ? `${money(f.wasted_cost)} perdidos${f.estimated ? ' ≈' : ''}` : 'sin costo conocido'}</small></td>
                <td class="num" data-label="Fue a platos">${f.used != null ? `${esc(qty(f.used))} <small>${esc(unitShort(f.unit))}</small>` : '<span class="inv-stock-none">sin receta</span>'}</td>
                <td class="num" data-label="Se botó">${esc(qty(f.wasted))} <small>${esc(unitShort(f.unit))}</small></td>
                <td class="num" data-label="% merma">${pct != null ? `<span class="inv-pct-pill ${nivel(pct)}">${pct.toLocaleString('es-PA', { maximumFractionDigits: 1 })}%</span>` : '—'}</td>
                <td data-label="Dónde más se usa">${f.dishes.length
                  ? f.dishes.map((d) => `<span class="inv-chip" title="${esc(d.type === 'modificador' ? 'Opción elegida en el plato' : 'Plato')}">${esc(d.name)} <small>${Number(d.share_pct).toLocaleString('es-PA', { maximumFractionDigits: 0 })}%</small></span>`).join(' ')
                  : '<span class="inv-stock-none">ninguna receta lo usa</span>'}</td>
              </tr>`;
          }).join('')}
        </tbody>
      </table>
      <p class="inv-recipe-foot">Los insumos marcados "Casa" (salsas, arroces…) se cuentan como preparación: lo que llevan adentro todavía no se desglosa.${r.lines_without_conversion ? ` ${pluralize(r.lines_without_conversion, 'línea de receta', 'líneas de receta')} en una unidad que no se puede pasar a la del insumo no ${r.lines_without_conversion === 1 ? 'suma' : 'suman'}.` : ''}</p>
      ${botonTraer ? `<div class="inv-recipe-actions">${botonTraer}</div>` : ''}`
      : `${emptyStateHtml('bar-chart-3', 'Sin mermas en el período', 'Cuando se registren, acá se ve qué parte de cada insumo se botó frente a lo que se usó.')}
         ${botonTraer ? `<div class="inv-recipe-actions">${botonTraer}</div>` : ''}`;

    // Platos más afectados
    const platos = r.dishes.filter((d) => Number(d.allocated_cost) > 0);
    $('wasteDishPanel').hidden = !platos.length;
    if (platos.length) {
      $('wasteDishBars').innerHTML = barsHtml(platos, {
        valor: (d) => Number(d.allocated_cost),
        etiqueta: (d) => `${esc(d.name)}${d.type === 'modificador' ? ' <span class="inv-badge muted">opción</span>' : ''}`,
        extra: (d) => `${money(d.allocated_cost)} <small>${esc(d.ingredients.join(', '))}</small>`,
      });
    }
    wireSyncRecipes();
    utils.renderIcons();
  }

  function wireSyncRecipes() {
    $('btnSyncRecipes')?.addEventListener('click', async () => {
      try {
        const res = await api.post('/inventory/invu/sync-recipes', {});
        utils.showToast(res.started
          ? 'Se están trayendo las recetas de Invu. Tarda unos 12 minutos por sucursal; podés seguir usando el sistema.'
          : 'Ya se estaban trayendo las recetas.', 'success');
        if (wasteAnalysis.recipes) wasteAnalysis.recipes.recipes_running = true;
        renderWasteRecipes();
      } catch (err) {
        utils.showToast(err.message || 'No se pudieron pedir las recetas.', 'error');
      }
    });
  }

  function barsHtml(filas, { valor, etiqueta, extra }) {
    const tope = Math.max(...filas.map(valor), 0);
    return filas.map((f) => `
      <div class="inv-bar-row inv-bar-row-waste">
        <span class="inv-bar-name">${etiqueta(f)}</span>
        <span class="inv-bar-value">${extra(f)}</span>
        <span class="inv-bar-track"><span class="inv-bar-fill inv-bar-fill-waste" style="width:${tope > 0 && valor(f) > 0 ? Math.max(3, (valor(f) / tope) * 100) : 0}%"></span></span>
      </div>`).join('');
  }

  function renderWasteAnalysis() {
    const a = wasteAnalysis.data;
    if (!a) return;
    const t = a.totals;
    const porKg = wasteAnalysis.metric === 'kg';
    const val = (x) => Number(porKg ? (x.kg || 0) : x.cost);
    const fmt = (x) => (porKg ? (x.kg != null ? kgTxt(x.kg) : '—') : money(x.cost));
    const sucursal = a.branch_id ? (state.branches.find((b) => b.id === a.branch_id)?.name || '') : 'todas las sucursales';

    $('wasteAnalysisRange').textContent = `Del ${fechaCorta(a.date_from)} al ${fechaCorta(a.date_to)} · ${sucursal}`;

    const subCosto = [];
    if (Number(t.cost_estimated) > 0) subCosto.push(`${money(t.cost_estimated)} estimado con costo de Invu`);
    if (t.lines_without_cost) subCosto.push(`${pluralize(t.lines_without_cost, 'línea', 'líneas')} sin costo`);
    const kpis = [
      { icon: 'dollar-sign', label: 'Pérdida', value: money(t.cost_total), sub: subCosto.join(' · ') || 'Al costo de los cargamentos' },
      { icon: 'scale', label: 'Kilos descartados', value: kgTxt(t.kg_total),
        sub: [
          Number(t.kg_estimated) > 0 ? `≈ ${kgTxt(t.kg_estimated)} estimado por peso promedio de pieza` : null,
          t.lines_without_kg ? `${pluralize(t.lines_without_kg, 'línea', 'líneas')} por unidad sin peso` : null,
        ].filter(Boolean).join(' · ') || 'Todo pesado en balanza' },
      { icon: 'trending-down', label: 'Mermas', value: String(t.records),
        sub: t.records ? `${t.records_with_photo} con foto · ${t.records_with_weight} con peso` : 'Ninguna en el período' },
      { icon: 'percent', label: 'Merma sobre ventas', value: t.waste_pct_of_sales != null ? `${Number(t.waste_pct_of_sales).toLocaleString('es-PA', { maximumFractionDigits: 2 })}%` : '—',
        sub: t.sales_net != null ? `de ${money(t.sales_net)} vendidos (Invu)` : 'Sin ventas de Invu en el período' },
    ];
    $('wasteKpis').innerHTML = kpis.map((k) => `
      <div class="inv-kpi">
        <span class="inv-kpi-label"><i data-lucide="${k.icon}"></i> ${esc(k.label)}</span>
        <span class="inv-kpi-value">${esc(k.value)}</span>
        <span class="inv-kpi-sub">${esc(k.sub)}</span>
      </div>`).join('');

    renderWasteTrend();

    // ---- Insumos ----
    // Los que no suman en la medida elegida (sin costo, o sin kilos) van al final: en "$" un
    // insumo sin costo no es "el que menos se pierde", es uno que todavía no se sabe cuánto vale.
    const items = [...a.by_item].sort((x, y) => val(y) - val(x)).slice(0, 10);
    $('wasteItemsNote').textContent = porKg ? 'Por kilos' : 'Por pérdida';
    $('wasteItemsBars').innerHTML = items.length ? barsHtml(items, {
      valor: val,
      etiqueta: (i) => `${esc(i.name)} ${i.kind === 'casa' ? '<span class="inv-badge kind-house">Casa</span>' : ''}`,
      extra: (i) => `${fmt(i)}${!porKg && i.estimated ? ' <small title="Parte del costo es el de referencia de Invu">≈</small>' : ''}
        <small>${esc(qty(i.quantity))} ${esc(i.unit)}</small>`,
    }) : emptyStateHtml('package', 'Nada para mostrar', porKg ? 'Ningún insumo con kilos en el período.' : 'Ninguna merma en el período.');

    // ---- Motivo ----
    $('wasteReasonAnalysisBars').innerHTML = a.by_reason.length ? barsHtml(a.by_reason, {
      valor: val,
      etiqueta: (g) => esc(g.label),
      extra: (g) => `${fmt(g)} <small>${pluralize(g.records, 'vez', 'veces')}</small>`,
    }) : emptyStateHtml('help-circle', 'Sin motivos', 'Ninguna merma en el período.');

    // ---- Sucursal (solo quien ve más de una) ----
    const verSucursales = state.isGlobalScope && !a.branch_id;
    $('wasteBranchPanel').hidden = !verSucursales;
    if (verSucursales) {
      $('wasteBranchBars').innerHTML = a.by_branch.length ? barsHtml(a.by_branch, {
        valor: val,
        etiqueta: (g) => esc(g.label),
        extra: (g) => `${fmt(g)} <small>${g.waste_pct_of_sales != null ? `${Number(g.waste_pct_of_sales).toLocaleString('es-PA', { maximumFractionDigits: 2 })}% de su venta` : 'sin venta de Invu'}</small>`,
      }) : emptyStateHtml('building-2', 'Sin mermas', 'Ninguna sucursal registró merma en el período.');
    }

    // ---- Tipo ----
    $('wasteKindBars').innerHTML = a.by_kind.length ? barsHtml(a.by_kind, {
      valor: val,
      etiqueta: (g) => esc(g.label),
      extra: (g) => fmt(g),
    }) : emptyStateHtml('layers', 'Sin datos', 'Ninguna merma en el período.');

    // ---- De proceso o evitable ----
    $('wasteNatureBars').innerHTML = (a.by_nature || []).length ? barsHtml(a.by_nature, {
      valor: val,
      etiqueta: (g) => esc(g.label),
      extra: (g) => `${fmt(g)} <small>${pluralize(g.records, 'merma', 'mermas')}</small>`,
    }) : emptyStateHtml('scissors', 'Sin datos', 'Ninguna merma en el período.');

    // ---- Rendimiento al limpiar (el más bajo primero: es el que hay que mirar) ----
    const rinde = a.yields || [];
    $('wasteYieldPanel').hidden = !rinde.length;
    if (rinde.length) {
      $('wasteYieldBars').innerHTML = rinde.map((y) => {
        const pct = Number(y.yield_pct);
        return `
          <div class="inv-bar-row">
            <span class="inv-bar-name">${esc(y.name)}</span>
            <span class="inv-bar-value">${pct.toLocaleString('es-PA', { maximumFractionDigits: 1 })}% <small>de ${esc(kgTxt(y.processed_kg))} quedaron ${esc(kgTxt(y.trimmed_kg))} de recorte</small></span>
            <span class="inv-bar-track"><span class="inv-bar-fill" style="width:${Math.max(0, Math.min(100, pct))}%"></span></span>
          </div>`;
      }).join('');
    }

    utils.renderIcons();
  }

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
      const sinEntradas = negativos.filter((r) => !Number(r.entered)).length;
      note.hidden = false;
      note.querySelector('span').textContent =
        `${pluralize(negativos.length, 'insumo da', 'insumos dan')} negativo. No es que falte mercadería: ` +
        (sinEntradas
          ? `se registró merma${sinEntradas === negativos.length ? '' : ' en varios'}, pero nunca lo que entró (no hay cargamentos), así que el sistema resta de cero. `
          : 'salió más de lo que el sistema tiene registrado como entrada. ') +
        'Se arregla registrando los cargamentos que llegan, o con un conteo de lo que hay hoy: el conteo fija el punto de partida.';
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

    // La columna de traslados solo aparece cuando hubo alguno: la mayoría de las sucursales
    // todavía no los usa y una columna llena de "—" solo le quita lugar al resto en celular.
    const hayTraslados = rows.some((r) => Number(r.transferred));
    // Cada número con su unidad al lado: un "692" solo no dice si son gramos, kilos o unidades.
    const cant = (n, unit, conSigno = false) =>
      `${esc(conSigno ? signedQty(n) : qty(n))} <small>${esc(unitShort(unit))}</small>`;
    table.innerHTML = `
      <table class="inv-detail-table inv-stock-grid">
        <thead>
          <tr>
            <th>Insumo</th>
            <th class="num" title="Lo que llegó por cargamentos">Entró<small>cargamentos</small></th>
            <th class="num" title="Lo que se registró como merma">Salió<small>por merma</small></th>
            <th class="num" title="Lo que corrigió el último conteo: + sobraba, − faltaba">Ajuste<small>por conteo</small></th>
            ${hayTraslados ? '<th class="num" title="Recibido de otras sucursales menos lo enviado">Traslados<small>entre sucursales</small></th>' : ''}
            <th class="num" title="Entró − salió ± ajuste ± traslados">Queda<small>hoy</small></th>
            <th class="num" title="Lo que costó lo que salió por merma">Pérdida<small>en $</small></th>
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
                <td class="num" data-label="Entró (cargamentos)">${Number(r.entered) ? cant(r.entered, r.unit) : '<span class="inv-stock-none">nada</span>'}</td>
                <td class="num" data-label="Salió por merma">${Number(r.wasted) ? cant(r.wasted, r.unit) : '—'}</td>
                <td class="num" data-label="Ajuste por conteo" title="${r.last_counted_at ? `Último conteo: ${esc(utils.formatDateTime(r.last_counted_at))}` : 'Nunca se contó'}">${Number(r.adjusted) ? cant(r.adjusted, r.unit, true) : '—'}</td>
                ${hayTraslados ? `<td class="num" data-label="Traslados">${Number(r.transferred) ? cant(r.transferred, r.unit, true) : '—'}</td>` : ''}
                <td class="num inv-stock-onhand" data-label="Queda hoy"><span class="inv-stock-pill${clase}">${cant(r.on_hand, r.unit)}</span></td>
                <td class="num" data-label="Pérdida en $">${r.wasted_cost != null
                  ? `${money(r.wasted_cost)}${r.wasted_cost_estimated ? ' <small title="Valuado con el costo de referencia de Invu: todavía no hay cargamento con costo">≈</small>' : ''}`
                  : '—'}</td>
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
    const seq = ++loadSeq.wasteStock;
    state.wasteStock = new Map();
    const branchId = wasteModalBranchId();
    if (!branchId) return;
    try {
      const rows = await api.get(`/inventory/stock?branch_id=${branchId}&only_stocked=true`);
      // Cambiar de sucursal en el modal: la respuesta de la anterior ya no escribe su stock
      // en el mapa de la nueva.
      if (seq !== loadSeq.wasteStock) return;
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
    const pedido = wasteLineCalc(row).qty || 0;
    if (pedido > disponible) cell.classList.add('is-short');
  }

  /** Refresca la existencia de una fila y, con ella, la pérdida estimada y el peso del formulario. */
  function refreshWasteLine(row) {
    refreshWasteLineStock(row);
    updateWasteLineCalc(row);
    updateWasteTotal();
    syncWasteWeight();
  }

  // ---- Pieza entera o parte ----
  // Cada línea dice qué se botó. `quantity` (lo que resta del stock y se multiplica por el costo)
  // sale de ahí, siempre en la unidad del insumo:
  //   entera · insumo en g/kg/ml → piezas × lo que pesa (o trae) una pieza
  //   entera · insumo por unidad → piezas
  //   parte  · insumo en g/kg/ml → lo que marca la balanza
  //   parte  · insumo por unidad → gramos del pedazo ÷ gramos de una pieza entera
  // El servidor hace la misma cuenta (_cantidad_de_linea); esto es para mostrarla antes de guardar.

  /**
   * { itemId, mode, fam, qty, grams, measured, estimated, weighable, error } de una fila.
   * `estimated`: "pieza entera" sin pesar, así que el peso sale del promedio de la pieza.
   */
  function wasteLineCalc(row) {
    const itemInput = row.querySelector('.inv-item-input');
    const itemId = Number(itemInput.dataset.itemId || 0);
    const mode = row.dataset.mode || '';
    const fam = unitFamily(itemInput.dataset.unit);
    const v = Number(row.querySelector('.inv-line-qty').value || 0);
    const tam = Number(row.querySelector('.inv-line-piece-input').value || 0);
    const pesado = mode === 'entera' ? Number(row.querySelector('.inv-line-measured-input').value || 0) : 0;
    const r = { itemId, mode, fam, qty: null, grams: null, measured: pesado > 0 ? pesado : null,
                estimated: false, weighable: false, error: null };
    if (!itemId) return r;
    if (!mode) { r.error = 'tocá si se botó entero o un pedazo.'; return r; }
    // ¿Se puede saber el peso de esta línea? (para completar solo el bloque "Peso")
    r.weighable = fam.fam === 'peso' || (fam.fam === 'unidad' && (mode === 'parte' || tam > 0 || pesado > 0));
    if (!(v > 0)) {
      r.error = mode === 'entera' ? 'falta cuántos se botaron.' : 'falta cuánto pesó.';
      return r;
    }
    const medida = fam.fam === 'volumen' ? 'cuánto trae' : 'cuánto pesa';
    if (mode === 'entera') {
      if (fam.fam === 'unidad') {
        r.qty = v;
        if (pesado > 0) r.grams = pesado;
        else if (tam > 0) { r.grams = v * tam; r.estimated = true; }
      } else if (pesado > 0) {
        // Se pesó: manda la balanza, no el promedio.
        r.qty = pesado / fam.base;
        if (fam.fam === 'peso') r.grams = pesado;
      } else {
        if (!(tam > 0)) { r.error = `falta ${medida} uno, más o menos (o tocá "Lo pesé en la balanza").`; return r; }
        r.qty = (v * tam) / fam.base;
        r.estimated = true;
        if (fam.fam === 'peso') r.grams = v * tam;
      }
    } else if (fam.fam === 'unidad') {
      if (!(tam > 0)) { r.error = 'falta cuánto pesa uno entero, más o menos.'; return r; }
      r.qty = v / tam;
      r.grams = v;
    } else {
      r.qty = v;
      if (fam.fam === 'peso') r.grams = v * fam.base;
    }
    r.qty = Number(r.qty.toFixed(3));
    return r;
  }

  /** Lo que se manda al servidor por esta fila (él recalcula la cantidad con las mismas reglas). */
  function wasteLinePayload(row, c) {
    const v = row.querySelector('.inv-line-qty').value;
    const pieceInput = row.querySelector('.inv-line-piece-input');
    const linea = { inventory_item_id: c.itemId, mode: c.mode };
    if (c.mode === 'entera') {
      linea.pieces = v;
      if (c.measured) linea.measured_amount = String(c.measured);
    } else if (c.fam.fam === 'unidad') linea.part_amount = v;
    else linea.quantity = v;
    if (!pieceInput.readOnly && Number(pieceInput.value) > 0) linea.piece_size = pieceInput.value;
    return linea;
  }

  /** El costo por unidad del insumo que va a usar la estimación: el del último cargamento o el de Invu. */
  function wasteLineUnitCost(row) {
    const input = row.querySelector('.inv-item-input');
    const fila = state.wasteStock.get(Number(input.dataset.itemId || 0));
    if (fila && fila.last_unit_cost != null) return { cost: Number(fila.last_unit_cost), estimated: false };
    if (input.dataset.refCost) return { cost: Number(input.dataset.refCost), estimated: true };
    return null;
  }

  /** Debajo de cada insumo, en una línea: cuánto es y cuánto se pierde. */
  function updateWasteLineCalc(row) {
    const box = row.querySelector('.inv-line-calc');
    if (!box) return;
    const c = wasteLineCalc(row);
    const partes = [];
    const v = Number(row.querySelector('.inv-line-qty').value || 0);
    const tam = Number(row.querySelector('.inv-line-piece-input').value || 0);
    const u = c.fam.short;
    const mu = c.fam.fam === 'volumen' ? 'ml' : 'g';
    const costo = wasteLineUnitCost(row);
    if (c.qty != null) {
      if (c.mode === 'entera' && c.measured) {
        partes.push(`Pesado: <strong>${esc(qty(c.measured))} ${mu}</strong>`);
      } else if (c.mode === 'entera' && c.fam.fam !== 'unidad') {
        partes.push(`${esc(qty(v))} × ${esc(qty(tam))} ${mu} = <strong>≈ ${esc(qty(c.qty))} ${esc(u)}</strong> <span class="inv-line-est">(aprox.)</span>`);
      } else if (c.mode === 'parte' && c.fam.fam === 'unidad') {
        partes.push(`Es <strong>${esc(qty(c.qty))}</strong> de uno entero`);
      }
      if (costo) {
        const aprox = costo.estimated || c.estimated ? '≈ ' : '';
        partes.push(`Se pierde <strong>${aprox}${esc(money(c.qty * costo.cost))}</strong>`);
      }
    }
    // Un costo de Invu cargado "por pieza" en un insumo que se mide en gramos da miles de dólares
    // el kilo (pasó con un pan: $1.625 el gramo). Quien carga la merma no puede arreglarlo, así
    // que el aviso lo ven solo supervisor y admin, que sí pueden pedir que se corrija en Invu.
    if (costo && c.fam.fam !== 'unidad' && hasPerm('inventory.adjust')) {
      const porMil = (costo.cost / c.fam.base) * 1000;   // $ por kg o por litro
      if (porMil > 100) {
        partes.push(`<span class="inv-line-warn">Revisar en Invu: el costo de este insumo parece mal cargado (da ${esc(money(porMil))} el ${c.fam.fam === 'volumen' ? 'litro' : 'kilo'}).</span>`);
      }
    }
    box.innerHTML = partes.join(' · ');
    box.hidden = !partes.length;
  }

  /** Ajusta la tarjeta del insumo a lo elegido: qué se pregunta y qué se esconde. */
  function applyWasteLineMode(row) {
    const itemInput = row.querySelector('.inv-item-input');
    const extra = row.querySelector('.inv-line-extra');
    const qtyInput = row.querySelector('.inv-line-qty');
    const qtyLabel = row.querySelector('.inv-line-qty-label');
    const qtyUnit = row.querySelector('.inv-line-amount .inv-qty-unit');
    const pieceBox = row.querySelector('.inv-line-piece');
    const pieceLabel = row.querySelector('.inv-line-piece-label');
    const pieceInput = row.querySelector('.inv-line-piece-input');
    const measuredBox = row.querySelector('.inv-line-measured');
    const measuredInput = row.querySelector('.inv-line-measured-input');
    const toggle = row.querySelector('.inv-line-weighed-toggle');
    const tip = row.querySelector('.inv-line-tip');
    const tieneItem = Boolean(itemInput.dataset.itemId);
    const mode = row.dataset.mode || '';
    const fam = unitFamily(itemInput.dataset.unit);
    const mu = fam.fam === 'volumen' ? 'ml' : 'g';

    row.querySelector('.inv-line-mode').hidden = !tieneItem;
    extra.hidden = !tieneItem || !mode;
    row.querySelectorAll('.inv-mode-btn').forEach((b) => {
      const on = b.dataset.mode === mode;
      b.classList.toggle('is-active', on);
      b.setAttribute('aria-checked', on ? 'true' : 'false');
    });
    qtyInput.disabled = !tieneItem || !mode;
    if (!tieneItem || !mode) return;

    const entera = mode === 'entera';
    row.querySelectorAll('.inv-qty-step').forEach((b) => { b.hidden = !entera; });
    if (entera) {
      qtyLabel.textContent = '¿Cuántos se botaron?';
      qtyInput.placeholder = '1';
      qtyInput.step = '1';
      qtyUnit.textContent = '';
    } else if (fam.fam === 'volumen') {
      qtyLabel.textContent = '¿Cuánto era?';
      qtyInput.placeholder = fam.short === 'ml' ? 'Ej. 250' : 'Ej. 0.25';
      qtyInput.step = '0.001';
      qtyUnit.textContent = fam.short;
    } else {
      qtyLabel.textContent = '¿Cuánto pesó en la balanza?';
      const enGramos = fam.fam === 'unidad' || fam.short === 'g';
      qtyInput.placeholder = enGramos ? 'Ej. 240' : 'Ej. 0.24';
      qtyInput.step = enGramos ? '0.1' : '0.001';
      qtyUnit.textContent = fam.fam === 'unidad' ? 'g' : fam.short;
    }

    // "Lo pesé en la balanza": solo en "entero", y se abre a pedido (o si ya tiene un valor).
    const pesado = Number(measuredInput.value || 0) > 0;
    const abierto = row.dataset.weighed === '1' || pesado;
    toggle.hidden = !entera || abierto;
    measuredBox.hidden = !entera || !abierto;
    row.querySelector('.inv-line-measured-label').textContent = fam.fam === 'volumen' ? 'Cantidad medida' : 'Peso en la balanza';
    row.querySelector('.inv-line-measured-unit').textContent = mu;
    // El recordatorio de la tara, solo cuando de verdad se está por pesar.
    tip.hidden = fam.fam === 'volumen' || (entera && !abierto);

    // Cuánto pesa uno: hace falta en "entero" de un insumo en gramos/kilos (si no se pesó) y en
    // "un pedazo" de un insumo que se cuenta por unidad. Si ya está guardado y quien carga no lo
    // puede cambiar, no se pregunta: se usa y la cuenta de abajo lo muestra.
    const guardado = itemInput.dataset.pieceSize;
    if (guardado && !pieceInput.value) pieceInput.value = String(Number(guardado));
    const fijo = Boolean(guardado) && !hasPerm('inventory.adjust');
    pieceInput.readOnly = fijo;
    const necesita = (entera && fam.fam !== 'unidad' && !pesado) || (!entera && fam.fam === 'unidad');
    pieceBox.hidden = !necesita || fijo;
    pieceLabel.textContent = entera
      ? (fam.fam === 'volumen' ? '¿Cuánto trae uno, más o menos?' : '¿Cuánto pesa uno, más o menos?')
      : '¿Y cuánto pesa uno entero, más o menos?';
    row.querySelector('.inv-line-piece-unit').textContent = mu;
  }

  /**
   * El peso se pide UNA vez. Si todos los insumos elegidos se cuentan en peso (g, kg, lb), la
   * cantidad de arriba ya es lo que marcó la balanza: el bloque "Peso" se completa solo con esa
   * suma y queda de solo lectura. Si alguno va por unidad (la piña), ahí sí se escribe el peso.
   */
  let wasteWeightAuto = false;
  let wasteWeightEstimated = false;   // el peso automático usa algún peso promedio de pieza
  function syncWasteWeight() {
    const input = $('wasteWeightValue');
    const hint = $('wasteWeightHint');
    if (!input) return;
    // Cada línea sabe su peso si el insumo va en peso, o si va por unidad y se sabe cuánto pesa
    // una pieza (o lo que se pesó fue el pedazo).
    const filas = Array.from(wasteLinesContainer.querySelectorAll('.inv-line-row'))
      .map(wasteLineCalc)
      .filter((f) => f.itemId);
    const todoEnPeso = filas.length > 0 && filas.every((f) => f.weighable);
    if (todoEnPeso) {
      const kg = filas.reduce((s, f) => s + (f.grams || 0), 0) / 1000;
      const unidad = $('wasteWeightUnit').value;
      input.value = kg > 0 ? String(Number((kg / KG_FACTOR[unidad]).toFixed(3))) : '';
      input.readOnly = true;
      input.classList.add('is-auto');
      wasteWeightEstimated = filas.some((f) => f.estimated);
      hint.textContent = wasteWeightEstimated
        ? '≈ Estimado: sale del peso promedio de la pieza. Si lo pesaste, poné el peso real arriba.'
        : 'Se calcula de lo que escribiste arriba.';
      wasteWeightAuto = true;
    } else {
      if (wasteWeightAuto) input.value = '';   // venía calculado: ahora lo escribe la persona
      wasteWeightEstimated = false;            // escrito a mano = leído en la balanza
      input.readOnly = false;
      input.classList.remove('is-auto');
      hint.textContent = filas.length ? 'Lo que marcó la balanza (el insumo se cuenta por unidad).' : 'Lo que marcó la balanza.';
      wasteWeightAuto = false;
    }
    updateYieldPreview();
  }

  /** Recorte o limpieza: "de 5 kg quedaron 0.270 kg de recorte → se aprovecha el 94.6%". */
  function updateYieldPreview() {
    const box = $('wasteYieldPreview');
    if (!box) return;
    const recorte = Number($('wasteWeightValue').value || 0) * (KG_FACTOR[$('wasteWeightUnit').value] || 0);
    const limpio = Number($('wasteProcessedValue').value || 0) * (KG_FACTOR[$('wasteProcessedUnit').value] || 0);
    if (!(recorte > 0 && limpio > 0)) { box.hidden = true; return; }
    box.hidden = false;
    if (recorte > limpio) {
      box.className = 'inv-yield-preview is-bad';
      box.textContent = 'El recorte pesa más que lo limpiado: revisá los dos números.';
      return;
    }
    const pct = ((limpio - recorte) / limpio) * 100;
    box.className = 'inv-yield-preview';
    box.innerHTML = `De <strong>${esc(kgTxt(limpio))}</strong> quedaron <strong>${esc(kgTxt(recorte))}</strong> de recorte: se aprovecha el <strong>${pct.toLocaleString('es-PA', { maximumFractionDigits: 1 })}%</strong>.`;
  }

  function syncProcessedField() {
    const esRecorte = $('wasteReasonSelect').value === 'recorte';
    $('wasteProcessedField').hidden = !esRecorte;
    updateYieldPreview();
  }
  $('wasteReasonSelect')?.addEventListener('change', syncProcessedField);
  $('wasteWeightUnit')?.addEventListener('change', syncWasteWeight);
  $('wasteWeightValue')?.addEventListener('input', updateYieldPreview);
  $('wasteProcessedValue')?.addEventListener('input', updateYieldPreview);
  $('wasteProcessedUnit')?.addEventListener('change', updateYieldPreview);

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
    let estimados = 0;
    wasteLinesContainer.querySelectorAll('.inv-line-row').forEach((row) => {
      const c = wasteLineCalc(row);
      const cantidad = c.qty || 0;
      if (!c.itemId || cantidad <= 0) return;
      // Sin cargamento con costo: el de referencia de Invu (el mismo que usa el servidor).
      const costo = wasteLineUnitCost(row);
      if (costo) {
        total += cantidad * costo.cost;
        conCosto += 1;
        if (costo.estimated) estimados += 1;
      } else {
        sinCosto += 1;
      }
    });

    $('wasteTotal').textContent = conCosto ? `${estimados ? '≈ ' : ''}${money(total)}` : '—';
    const nota = document.querySelector('#modalWaste .inv-total-box small');
    if (nota) {
      const partes = [];
      if (sinCosto) partes.push(`${pluralize(sinCosto, 'insumo', 'insumos')} sin precio, no se cuenta${sinCosto === 1 ? '' : 'n'}`);
      if (estimados) partes.push('aprox., con el precio de Invu');
      nota.textContent = partes.length ? partes.join(' · ') : 'Con el precio de la última compra';
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

    const pieceInput = row.querySelector('.inv-line-piece-input');
    const measuredInput = row.querySelector('.inv-line-measured-input');

    const hideSuggestions = () => { suggestBox.hidden = true; suggestBox.innerHTML = ''; };
    /** Vuelve la fila a "sin insumo": sin modo, sin cantidad pedida y sin tamaño de pieza. */
    const resetLine = () => {
      itemInput.dataset.itemId = '';
      itemInput.dataset.unit = '';
      itemInput.dataset.refCost = '';
      itemInput.dataset.pieceSize = '';
      unitLabel.textContent = '';
      row.dataset.mode = '';
      row.dataset.weighed = '';
      pieceInput.value = '';
      measuredInput.value = '';
      applyWasteLineMode(row);
    };

    function selectItem(item) {
      itemInput.value = item.name;
      itemInput.dataset.itemId = String(item.id);
      itemInput.dataset.unit = item.unit || '';
      itemInput.dataset.refCost = item.reference_cost != null ? String(item.reference_cost) : '';
      itemInput.dataset.pieceSize = item.piece_size != null ? String(item.piece_size) : '';
      unitLabel.textContent = item.unit ? `Se cuenta en ${item.unit}` : '';
      row.dataset.mode = '';
      row.dataset.weighed = '';
      pieceInput.value = '';
      measuredInput.value = '';
      qtyInput.value = '';
      applyWasteLineMode(row);
      hideSuggestions();
      refreshWasteLine(row);
      row.querySelector('.inv-mode-btn')?.focus();
    }

    row.querySelectorAll('.inv-mode-btn').forEach((btn) => {
      btn.addEventListener('click', () => {
        if (row.dataset.mode !== btn.dataset.mode) {
          // Piezas ≠ gramos: al cambiar de modo la cantidad se vacía. En "entera" arranca en 1
          // (lo más común: se botó un pan, una piña), y se cambia si fueron más.
          qtyInput.value = btn.dataset.mode === 'entera' ? '1' : '';
          measuredInput.value = '';
          row.dataset.weighed = '';
        }
        row.dataset.mode = btn.dataset.mode;
        applyWasteLineMode(row);
        refreshWasteLine(row);
        // En "entero" no se abre el teclado: se usan − y +. En "un pedazo" sí, porque lo que
        // sigue es escribir lo que marcó la balanza.
        if (btn.dataset.mode === 'parte') qtyInput.focus();
      });
    });
    row.querySelectorAll('.inv-qty-step').forEach((b) => {
      b.addEventListener('click', () => {
        const n = Math.max(1, Math.round((Number(qtyInput.value) || 0) + Number(b.dataset.step)));
        qtyInput.value = String(n);
        refreshWasteLine(row);
      });
    });
    row.querySelector('.inv-line-weighed-toggle').addEventListener('click', () => {
      row.dataset.weighed = '1';
      applyWasteLineMode(row);
      measuredInput.focus();
    });
    pieceInput.addEventListener('input', () => refreshWasteLine(row));
    measuredInput.addEventListener('input', () => { applyWasteLineMode(row); refreshWasteLine(row); });

    // La lista sale del catálogo ya cargado en la pantalla (los insumos de Invu y los cargados a
    // mano): aparece entera al tocar el campo y se filtra con cada letra, al instante, sin
    // importar tildes ni mayúsculas ("pina" encuentra "Piña") y también por código ("P203").
    let visibles = [];
    let marcado = -1;

    function renderSuggestions(results) {
      visibles = results;
      marcado = -1;
      // Sin "+ Crear": una merma es de algo que ya existía. Si el insumo no está en el catálogo,
      // tampoco entró nunca, y registrar su pérdida sería inventar un movimiento.
      if (!results.length) {
        suggestBox.innerHTML = '<div class="inv-item-suggestion-empty">Ningún insumo del catálogo coincide.</div>';
        suggestBox.hidden = false;
        return;
      }
      suggestBox.innerHTML = results.map((r, i) => `
        <button type="button" class="inv-item-suggestion" data-idx="${i}" role="option">
          <span class="inv-sugg-name">${esc(r.name)}${r.kind === 'casa' ? ' <span class="inv-badge kind-house">Casa</span>' : ''}</span>
          <small>${r.code ? `${esc(r.code)} · ` : ''}${esc(r.unit)}</small>
        </button>`).join('');
      suggestBox.hidden = false;
      suggestBox.querySelectorAll('.inv-item-suggestion').forEach((btn) => {
        // mousedown y no click: el blur del campo cerraría la lista antes de que llegue el click.
        btn.addEventListener('mousedown', (e) => { e.preventDefault(); selectItem(visibles[Number(btn.dataset.idx)]); });
      });
    }

    function filtrarCatalogo(query) {
      const q = sinTildes(query.trim());
      const activos = state.items.filter((i) => i.active !== false);
      if (!q) return activos;
      const empiezan = [];
      const contienen = [];
      activos.forEach((i) => {
        const nombre = sinTildes(i.name);
        const codigo = sinTildes(i.code || '');
        if (nombre.startsWith(q) || codigo === q) empiezan.push(i);
        else if (nombre.includes(q) || codigo.includes(q)) contienen.push(i);
      });
      return [...empiezan, ...contienen];
    }

    async function mostrarSugerencias() {
      if (state.items.length) {
        renderSuggestions(filtrarCatalogo(itemInput.value));
        return;
      }
      // Catálogo todavía sin cargar: se pregunta al servidor como antes.
      const query = itemInput.value.trim();
      const reqId = ++itemInput._reqId;
      try {
        const results = await api.get(`/inventory/items?q=${encodeURIComponent(query)}&limit=50`);
        if (reqId === itemInput._reqId) renderSuggestions(results);
      } catch (err) {
        if (reqId === itemInput._reqId) hideSuggestions();
      }
    }

    function marcar(idx) {
      const botones = suggestBox.querySelectorAll('.inv-item-suggestion');
      if (!botones.length) return;
      marcado = (idx + botones.length) % botones.length;
      botones.forEach((b, i) => b.classList.toggle('is-active', i === marcado));
      botones[marcado].scrollIntoView({ block: 'nearest' });
    }

    itemInput.addEventListener('focus', () => { if (!itemInput.dataset.itemId) mostrarSugerencias(); });
    itemInput.addEventListener('blur', () => setTimeout(hideSuggestions, 120));
    itemInput.addEventListener('keydown', (e) => {
      if (suggestBox.hidden) return;
      if (e.key === 'ArrowDown') { e.preventDefault(); marcar(marcado + 1); }
      else if (e.key === 'ArrowUp') { e.preventDefault(); marcar(marcado - 1); }
      else if (e.key === 'Enter' && visibles.length) { e.preventDefault(); selectItem(visibles[Math.max(0, marcado)]); }
      else if (e.key === 'Escape') hideSuggestions();
    });

    itemInput.addEventListener('input', () => {
      resetLine();
      refreshWasteLine(row);
      mostrarSugerencias();
    });

    qtyInput.addEventListener('input', () => refreshWasteLine(row));

    removeBtn.addEventListener('click', () => {
      if (wasteLinesContainer.children.length > 1) {
        row.remove();
      } else {
        itemInput.value = '';
        qtyInput.value = '';
        resetLine();
      }
      refreshWasteLine(row);
    });

    applyWasteLineMode(row);
    wasteLinesContainer.appendChild(row);
    utils.renderIcons();
  }

  $('btnAddWasteLine')?.addEventListener('click', () => {
    createWasteLineRow();
    const inputs = wasteLinesContainer.querySelectorAll('.inv-item-input');
    inputs[inputs.length - 1]?.focus();
  });

  $('wasteBranchSelect')?.addEventListener('change', loadWasteStock);

  // ==========================================================================
  // Evidencia de la merma: fotos de lo que se descartó. Se guardan en la base con la merma.
  // ==========================================================================

  /** Achica la foto antes de subirla (una de celular pesa 3-5 MB). Si no se puede, va tal cual. */
  async function compressPhoto(file) {
    let tmpUrl = null;
    try {
      let source;
      if (window.createImageBitmap) {
        source = await createImageBitmap(file, { imageOrientation: 'from-image' });
      } else {
        tmpUrl = URL.createObjectURL(file);
        source = await new Promise((resolve, reject) => {
          const img = new Image();
          img.onload = () => resolve(img);
          img.onerror = reject;
          img.src = tmpUrl;
        });
      }
      const scale = Math.min(1, PHOTO_MAX_SIDE / Math.max(source.width, source.height));
      const canvas = document.createElement('canvas');
      canvas.width = Math.round(source.width * scale);
      canvas.height = Math.round(source.height * scale);
      canvas.getContext('2d').drawImage(source, 0, 0, canvas.width, canvas.height);
      if (source.close) source.close();
      const blob = await new Promise((resolve) => canvas.toBlob(resolve, 'image/jpeg', 0.82));
      if (blob) return blob;
    } catch (e) {
      /* Un formato que este navegador no sabe dibujar (p. ej. HEIC fuera de Safari): se manda
         el original y el servidor decide si es una imagen aceptada. */
    } finally {
      if (tmpUrl) URL.revokeObjectURL(tmpUrl);
    }
    return file;
  }

  function renderPendingPhotos() {
    const grid = $('wastePhotoPreviews');
    if (!grid) return;
    const add = grid.querySelector('.inv-photo-add-group');
    grid.querySelectorAll('.inv-photo-thumb').forEach((n) => n.remove());
    pendingWastePhotos.forEach((p, i) => {
      const el = document.createElement('div');
      el.className = 'inv-photo-thumb';
      el.innerHTML = `<img src="${p.url}" alt="Foto ${i + 1}">
        <button type="button" class="inv-photo-remove" data-idx="${i}" aria-label="Quitar foto ${i + 1}">&times;</button>`;
      grid.insertBefore(el, add);
    });
    add.hidden = pendingWastePhotos.length >= WASTE_PHOTOS_MAX;
  }

  function resetPendingPhotos() {
    pendingWastePhotos.forEach((p) => URL.revokeObjectURL(p.url));
    pendingWastePhotos = [];
    renderPendingPhotos();
  }

  /** Fotos elegidas o sacadas en el formulario: quedan en espera hasta guardar la merma. */
  async function addPendingPhotos(files) {
    for (const f of files) {
      if (pendingWastePhotos.length >= WASTE_PHOTOS_MAX) break;
      const blob = await compressPhoto(f);
      pendingWastePhotos.push({ blob, url: URL.createObjectURL(blob) });
    }
    renderPendingPhotos();
  }

  $('wastePhotoInput')?.addEventListener('change', async (e) => {
    const files = Array.from(e.target.files || []);
    e.target.value = '';   // elegir la misma foto otra vez tiene que volver a disparar el cambio
    await addPendingPhotos(files);
  });

  // ---- Tomar foto con la cámara ----
  // Celular/tablet: el input con `capture` abre la cámara del teléfono (la app nativa, que enfoca
  // y maneja la luz mejor que cualquier cosa hecha acá). Computadora: ahí `capture` se ignora y
  // abriría el explorador de archivos, así que se usa la webcam en vivo. Sin webcam o sin
  // permiso, cae a elegir un archivo.
  let cameraTarget = 'form';   // para quién se abrió la cámara: el formulario o el detalle
  let cameraStream = null;

  async function deliverCameraPhotos(files) {
    if (!files.length) return;
    if (cameraTarget === 'detail') await uploadDetailPhotos(files);
    else await addPendingPhotos(files);
  }

  function openCamera(target) {
    cameraTarget = target;
    const tactil = window.matchMedia('(pointer: coarse)').matches;
    if (tactil || !navigator.mediaDevices?.getUserMedia) {
      $('wasteCameraInput').click();
      return;
    }
    openWebcam();
  }

  async function openWebcam() {
    const msg = $('wasteCameraMsg');
    const shoot = $('btnCameraShoot');
    msg.hidden = true;
    shoot.disabled = true;
    $('wasteCamera').hidden = false;
    try {
      cameraStream = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: 'environment', width: { ideal: 1920 }, height: { ideal: 1080 } },
        audio: false,
      });
      $('wasteCameraVideo').srcObject = cameraStream;
      shoot.disabled = false;
    } catch (err) {
      closeWebcam();
      const sinPermiso = err && (err.name === 'NotAllowedError' || err.name === 'SecurityError');
      utils.showToast(
        sinPermiso
          ? 'No hay permiso para usar la cámara. Podés elegir la foto con «Galería».'
          : 'No se encontró una cámara. Podés elegir la foto con «Galería».',
        'warning'
      );
      // Si todavía vale el clic del usuario, abre el selector directo; si el navegador ya no lo
      // deja (pasó mucho rato en el aviso de permiso), queda el botón «Galería».
      $(cameraTarget === 'detail' ? 'wasteDetailPhotoInput' : 'wastePhotoInput').click();
    }
  }

  function closeWebcam() {
    if (cameraStream) cameraStream.getTracks().forEach((t) => t.stop());
    cameraStream = null;
    $('wasteCameraVideo').srcObject = null;
    $('wasteCamera').hidden = true;
  }

  $('btnCameraCancel')?.addEventListener('click', closeWebcam);
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && !$('wasteCamera').hidden) closeWebcam();
  });

  $('btnCameraShoot')?.addEventListener('click', async () => {
    const video = $('wasteCameraVideo');
    if (!video.videoWidth) return;
    const scale = Math.min(1, PHOTO_MAX_SIDE / Math.max(video.videoWidth, video.videoHeight));
    const canvas = document.createElement('canvas');
    canvas.width = Math.round(video.videoWidth * scale);
    canvas.height = Math.round(video.videoHeight * scale);
    canvas.getContext('2d').drawImage(video, 0, 0, canvas.width, canvas.height);
    const blob = await new Promise((resolve) => canvas.toBlob(resolve, 'image/jpeg', 0.85));
    closeWebcam();
    if (blob) await deliverCameraPhotos([blob]);
  });

  $('wasteCameraInput')?.addEventListener('change', async (e) => {
    const files = Array.from(e.target.files || []);
    e.target.value = '';
    await deliverCameraPhotos(files);
  });

  // Los botones "Tomar foto" del formulario y del detalle (este último se redibuja).
  document.addEventListener('click', (e) => {
    const btn = e.target.closest('[data-photo-camera]');
    if (btn) openCamera(btn.dataset.photoCamera);
  });

  $('wastePhotoPreviews')?.addEventListener('click', (e) => {
    const btn = e.target.closest('.inv-photo-remove');
    if (!btn) return;
    const [quitada] = pendingWastePhotos.splice(Number(btn.dataset.idx), 1);
    if (quitada) URL.revokeObjectURL(quitada.url);
    renderPendingPhotos();
  });

  /** Sube las fotos una por una. Devuelve la merma como quedó y cuántas fallaron. */
  async function uploadWastePhotos(wasteId, blobs) {
    let ultima = null;
    let fallidas = 0;
    for (const blob of blobs) {
      const fd = new FormData();
      fd.append('file', blob, blob.type === 'image/png' ? 'peso.png' : 'peso.jpg');
      try {
        ultima = await api.request(`/inventory/waste/${wasteId}/photos`, { method: 'POST', body: fd });
      } catch (err) {
        fallidas += 1;
      }
    }
    return { ultima, fallidas };
  }

  /** La foto con los mismos encabezados que el resto (un <img src> no manda el del dispositivo). */
  async function wastePhotoUrl(wasteId, photoId) {
    if (wastePhotoUrls.has(photoId)) return wastePhotoUrls.get(photoId);
    const headers = { 'X-Requested-With': 'XMLHttpRequest' };
    const deviceId = api.getDeviceId();
    if (deviceId) headers['X-Device-ID'] = deviceId;
    const res = await fetch(`${api.baseUrl}/inventory/waste/${wasteId}/photos/${photoId}`, { credentials: 'include', headers });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const url = URL.createObjectURL(await res.blob());
    wastePhotoUrls.set(photoId, url);
    return url;
  }

  function openPhotoViewer(url) {
    $('wastePhotoViewerImg').src = url;
    $('wastePhotoViewer').hidden = false;
    $('btnCloseWastePhoto').focus();
  }
  function closePhotoViewer() {
    $('wastePhotoViewer').hidden = true;
    $('wastePhotoViewerImg').removeAttribute('src');
  }
  $('btnCloseWastePhoto')?.addEventListener('click', closePhotoViewer);
  $('wastePhotoViewer')?.addEventListener('click', (e) => { if (e.target === e.currentTarget) closePhotoViewer(); });
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && !$('wastePhotoViewer').hidden) closePhotoViewer();
  });

  /** Galería del detalle: carga las miniaturas y deja agregar más. */
  function wirePhotoGallery(w) {
    const grid = $('wasteDetailPhotos');
    if (!grid) return;
    grid.querySelectorAll('[data-photo-id]').forEach((btn) => {
      const photoId = Number(btn.dataset.photoId);
      wastePhotoUrl(w.id, photoId)
        .then((url) => {
          if (!btn.isConnected) return;
          btn.classList.remove('is-loading');
          btn.innerHTML = `<img src="${url}" alt="Foto de la merma">`;
          btn.addEventListener('click', () => openPhotoViewer(url));
        })
        .catch(() => {
          if (!btn.isConnected) return;
          btn.classList.remove('is-loading');
          btn.classList.add('is-error');
          btn.innerHTML = '<i data-lucide="image-off"></i>';
          utils.renderIcons();
        });
    });
    $('btnAddWastePhoto')?.addEventListener('click', () => $('wasteDetailPhotoInput').click());
  }

  $('wasteDetailPhotoInput')?.addEventListener('change', async (e) => {
    const files = Array.from(e.target.files || []);
    e.target.value = '';
    await uploadDetailPhotos(files);
  });

  /** Fotos agregadas desde el detalle de una merma ya registrada: se suben en el momento. */
  async function uploadDetailPhotos(files) {
    const w = state.waste.find((x) => x.id === state.selected.waste);
    if (!w || !files.length) return;
    const libres = WASTE_PHOTOS_MAX - (w.photos || []).length;
    const blobs = [];
    for (const f of files.slice(0, Math.max(0, libres))) blobs.push(await compressPhoto(f));
    if (!blobs.length) return;
    utils.showToast('Subiendo foto...', 'info');
    const { ultima, fallidas } = await uploadWastePhotos(w.id, blobs);
    if (ultima) Object.assign(w, ultima);
    if (fallidas) utils.showToast(`${pluralize(fallidas, 'foto no se pudo', 'fotos no se pudieron')} subir.`, 'error');
    else utils.showToast(blobs.length === 1 ? 'Foto agregada.' : 'Fotos agregadas.', 'success');
    renderWasteList();
  }

  function openWasteModal() {
    $('wasteError').style.display = 'none';
    $('wasteNotes').value = '';
    $('wasteWeightValue').value = '';
    $('wasteWeightUnit').value = 'kg';
    $('wasteProcessedValue').value = '';
    $('wasteProcessedUnit').value = 'kg';
    resetPendingPhotos();
    $('wasteOccurredAt').value = toLocalInputValue(new Date());
    // Sin motivo marcado de entrada: uno preseleccionado se quedaba puesto sin que nadie lo eligiera.
    $('wasteReasonSelect').value = '';
    renderWasteReasonChips();
    const mas = document.querySelector('#modalWaste .inv-waste-more');
    if (mas) mas.open = false;
    wasteLinesContainer.innerHTML = '';
    createWasteLineRow();
    $('wasteTotal').textContent = '—';
    syncProcessedField();
    syncWasteWeight();
    openModal('modalWaste');
    loadWasteStock();
  }

  $('btnSubmitWaste')?.addEventListener('click', async () => {
    $('wasteError').style.display = 'none';

    const branchId = wasteModalBranchId();
    if (!branchId) { showModalError('wasteError', 'Elegí una sucursal.'); return; }

    // Se revisa en el mismo orden en que está el formulario: 1) qué, 2) por qué.
    const rows = Array.from(wasteLinesContainer.querySelectorAll('.inv-line-row'));
    const items = [];
    for (const row of rows) {
      const itemInput = row.querySelector('.inv-item-input');
      const qtyInput = row.querySelector('.inv-line-qty');
      const itemId = itemInput.dataset.itemId;
      if (!itemId && !itemInput.value.trim() && !qtyInput.value) continue;
      if (!itemId) { showModalError('wasteError', 'Tocá el insumo en la lista que aparece al escribir.'); itemInput.focus(); return; }
      const c = wasteLineCalc(row);
      if (c.error) {
        showModalError('wasteError', `${itemInput.value}: ${c.error}`);
        if (!c.mode) row.querySelector('.inv-mode-btn')?.focus();
        else if (!(Number(qtyInput.value) > 0)) qtyInput.focus();
        else row.querySelector('.inv-line-piece-input')?.focus();
        return;
      }
      items.push(wasteLinePayload(row, c));
    }
    if (!items.length) { showModalError('wasteError', 'Falta el paso 1: escribí qué se botó.'); return; }

    const reason = $('wasteReasonSelect').value;
    if (!reason) {
      showModalError('wasteError', 'Falta el paso 2: tocá por qué se botó.');
      $('wasteReasonChips')?.querySelector('.inv-reason-chip')?.focus();
      return;
    }

    const occurredValue = $('wasteOccurredAt').value;
    let occurredAt = null;
    if (occurredValue) {
      const parsed = new Date(occurredValue);
      if (Number.isNaN(parsed.getTime())) { showModalError('wasteError', 'La fecha no es válida.'); return; }
      occurredAt = parsed.toISOString();
    }

    const weightRaw = $('wasteWeightValue').value.trim();
    if (weightRaw && !(Number(weightRaw) > 0)) {
      showModalError('wasteError', 'El peso tiene que ser mayor que cero.');
      $('wasteWeightValue').focus();
      return;
    }
    const processedRaw = $('wasteProcessedValue').value.trim();
    if (reason === 'recorte' && processedRaw && !(Number(processedRaw) > 0)) {
      showModalError('wasteError', 'Lo que se limpió tiene que ser mayor que cero.');
      $('wasteProcessedValue').focus();
      return;
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
        weight_value: weightRaw || null,
        weight_unit: weightRaw ? $('wasteWeightUnit').value : null,
        weight_estimated: weightRaw ? (wasteWeightAuto && wasteWeightEstimated) : null,
        processed_value: (reason === 'recorte' && processedRaw) ? processedRaw : null,
        processed_unit: (reason === 'recorte' && processedRaw) ? $('wasteProcessedUnit').value : null,
      });

      // Lo aprendido (cuánto pesa una pieza) queda en el catálogo de la pantalla para la próxima.
      (creada.items || []).forEach((l) => {
        const it = state.items.find((i) => i.id === l.inventory_item_id);
        if (it && l.piece_size != null) it.piece_size = l.piece_size;
      });

      // Las fotos van después, una por una, contra la merma ya creada: si una falla, la merma
      // igual quedó guardada y la foto se puede agregar desde su detalle.
      let fotosFallidas = 0;
      if (pendingWastePhotos.length) {
        btn.textContent = 'Subiendo fotos...';
        ({ fallidas: fotosFallidas } = await uploadWastePhotos(creada.id, pendingWastePhotos.map((p) => p.blob)));
      }
      closeModal('modalWaste');
      resetPendingPhotos();
      showWasteResult(creada, { fotosFallidas });

      state.selected.waste = null;
      await Promise.all([loadWaste({ reset: true }), loadWasteAnalytics(), loadStock()]);
      renderResumen();
      if (wasteAnalysis.tab === 'analisis') loadWasteAnalysis();
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
      const badge = `<span class="inv-badge muted">${pluralize(c.items.length, 'insumo', 'insumos')}</span>`;
      return `
        <button type="button" class="inv-row${active}" data-count-id="${c.id}">
          <span class="inv-row-thumb inv-row-thumb-count"><i data-lucide="${c.is_first_count ? 'flag' : 'clipboard-check'}"></i></span>
          <span class="inv-row-info">
            <strong>${esc(countTitle(c))}</strong>
            <small>${esc(sub)}</small>
          </span>
          ${badge}
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

  // ---- Análisis del conteo ----
  // Qué faltó, qué sobró y qué cuadró, descontando lo que se cocinó (ventas de Invu × recetas).
  // Sale de GET /counts/{id}/analysis y se arma en palabras simples.
  const countAnalyses = new Map();   // id de conteo → análisis ya pedido

  const COUNT_STATUS = {
    falta: { label: 'Faltó', cls: 'short', icon: 'trending-down' },
    sin_receta: { label: 'Faltó · sin receta', cls: 'norecipe', icon: 'help-circle' },
    sobra: { label: 'Sobró', cls: 'over', icon: 'trending-up' },
    cuadra: { label: 'Cuadra', cls: 'ok', icon: 'check' },
    arranque: { label: 'Punto de partida', cls: 'base', icon: 'flag' },
  };

  const conUnidad = (n, unit) => `${qty(n)} ${unitShort(unit)}`;

  /** Una línea, en una frase: de dónde sale lo que tenía que haber y qué pasó. */
  function countLineText(l) {
    const u = l.unit;
    if (l.status === 'arranque') {
      return `Primer conteo: ${esc(conUnidad(l.counted, u))}${l.cost != null ? ` ≈ ${esc(money(l.cost))}` : ''} · desde el próximo se compara`;
    }
    const partes = [`El sistema decía ${esc(conUnidad(l.expected_records, u))}`];
    if (l.used_by_sales != null && Number(l.used_by_sales) > 0) {
      partes.push(`se usaron ${esc(conUnidad(l.used_by_sales, u))} en platos vendidos`);
    }
    const tenia = `tenía que haber <strong>${esc(conUnidad(l.expected, u))}</strong>`;
    const contaste = `contaste <strong>${esc(conUnidad(l.counted, u))}</strong>`;
    const dif = Math.abs(Number(l.unexplained || 0));
    const valor = l.cost != null && Number(l.cost) !== 0 ? ` (${l.cost_estimated ? '≈ ' : ''}${esc(money(Math.abs(Number(l.cost))))})` : '';
    let final = '';
    if (l.status === 'cuadra') final = 'cuadra';
    else if (l.status === 'sobra') final = `hay <strong>${esc(conUnidad(dif, u))} de más</strong>${valor}`;
    else final = `faltan <strong>${esc(conUnidad(dif, u))}</strong>${valor}`;
    return `${partes.join(', ')}: ${tenia}; ${contaste} → ${final}`;
  }

  function countLineHtml(l) {
    const st = COUNT_STATUS[l.status] || COUNT_STATUS.cuadra;
    return `
      <div class="inv-ca-line">
        <div class="inv-ca-line-head">
          <strong>${esc(l.name)}</strong>
          <span class="inv-ca-chip inv-ca-chip-${st.cls}"><i data-lucide="${st.icon}"></i>${esc(st.label)}</span>
        </div>
        <p>${countLineText(l)}</p>
      </div>`;
  }

  /** Lo que se muestra al guardar un conteo y en su detalle. */
  function countAnalysisHtml(a) {
    const t = a.totals;
    const aprox = t.cost_estimated ? '≈ ' : '';
    const hallazgos = [];

    if (t.baseline === t.items) {
      hallazgos.push(`Es el <strong>punto de partida</strong>: contaste ${pluralize(t.items, 'insumo', 'insumos')}${Number(t.baseline_value) ? ` por ${aprox}${esc(money(t.baseline_value))}` : ''}. Desde el próximo conteo vas a ver qué falta y qué sobra.`);
    } else {
      const falta = a.lines.find((l) => l.status === 'falta');
      if (falta) {
        hallazgos.push(`Lo que más faltó: <strong>${esc(falta.name)}</strong>, ${esc(conUnidad(Math.abs(Number(falta.unexplained)), falta.unit))}${falta.cost != null ? ` (${falta.cost_estimated ? '≈ ' : ''}${esc(money(Math.abs(Number(falta.cost))))})` : ''}. Puede ser merma que no se anotó, porciones más grandes que la receta o un error al recibir.`);
      }
      if (t.no_recipe) {
        hallazgos.push(`En ${pluralize(t.no_recipe, 'insumo', 'insumos')} faltó más de lo esperado, pero no ${t.no_recipe === 1 ? 'está' : 'están'} en ninguna receta de Invu: puede ser lo que se usó. Si se les carga la receta en Invu, el conteo lo descuenta solo.`);
      }
      if (t.surplus) {
        hallazgos.push(`${t.surplus === 1 ? 'Sobró 1 insumo' : `Sobraron ${t.surplus} insumos`}: casi siempre es una compra que no se registró en Cargamentos.`);
      }
      if (!t.missing && !t.no_recipe && !t.surplus && t.ok) {
        hallazgos.push('<strong>Todo cuadró</strong> con lo que tenía que haber.');
      }
      if (t.baseline) {
        hallazgos.push(`${pluralize(t.baseline, 'insumo se contó', 'insumos se contaron')} por primera vez: ${t.baseline === 1 ? 'queda' : 'quedan'} como punto de partida.`);
      }
    }
    if (!a.recipes_available) {
      hallazgos.push('Esta sucursal todavía no tiene recetas de Invu: el conteo no puede descontar lo que se usó en los platos.');
    }
    const hora = a.sales_synced_at
      ? utils._parseServerDate(a.sales_synced_at)?.toLocaleTimeString('es-PA', { hour: 'numeric', minute: '2-digit', timeZone: 'America/Panama' })
      : null;

    const tiles = t.baseline === t.items ? '' : `
      <div class="inv-ca-tiles">
        <div class="inv-ca-tile inv-ca-tile-short"><span>Faltó</span><strong>${Number(t.missing_cost) ? `${aprox}${esc(money(t.missing_cost))}` : '$0'}</strong><small>${pluralize(t.missing, 'insumo', 'insumos')}</small></div>
        <div class="inv-ca-tile inv-ca-tile-over"><span>Sobró</span><strong>${Number(t.surplus_cost) ? `${aprox}${esc(money(t.surplus_cost))}` : '$0'}</strong><small>${pluralize(t.surplus, 'insumo', 'insumos')}</small></div>
        <div class="inv-ca-tile inv-ca-tile-ok"><span>Cuadró</span><strong>${t.ok}</strong><small>de ${t.items - t.baseline} comparados</small></div>
      </div>`;

    const revisar = a.lines.filter((l) => ['falta', 'sin_receta', 'sobra'].includes(l.status));
    const cuadran = a.lines.filter((l) => l.status === 'cuadra');
    const arranque = a.lines.filter((l) => l.status === 'arranque');
    const grupo = (titulo, lineas, abierto) => (lineas.length ? `
      <details class="inv-ca-group"${abierto ? ' open' : ''}>
        <summary>${esc(titulo)} <small>(${lineas.length})</small></summary>
        ${lineas.map(countLineHtml).join('')}
      </details>` : '');

    return `
      ${tiles}
      <ul class="inv-ca-findings">${hallazgos.map((h) => `<li>${h}</li>`).join('')}</ul>
      ${grupo('Para revisar', revisar, true)}
      ${grupo('Cuadraron', cuadran, !revisar.length && cuadran.length <= 8)}
      ${grupo('Punto de partida', arranque, t.baseline === t.items && arranque.length <= 8)}
      <p class="inv-ca-foot">Cuadra si la diferencia es de hasta ${esc(String(Number(a.tolerance_pct)))} % (balanza, redondeos).${hora ? ` Ventas de Invu de hoy hasta las ${esc(hora)}.` : ''}</p>`;
  }

  async function countAnalysisFor(countId) {
    if (countAnalyses.has(countId)) return countAnalyses.get(countId);
    const a = await api.get(`/inventory/counts/${countId}/analysis`);
    countAnalyses.set(countId, a);
    return a;
  }

  function showCountResult(conteo) {
    const a = conteo.analysis;
    if (a) countAnalyses.set(conteo.id, a);
    $('countResultTitle').textContent = conteo.is_first_count ? 'Inventario de arranque cargado' : 'Resultado del conteo';
    $('countResultSubtitle').textContent = `${conteo.branch_name} · ${pluralize(conteo.items.length, 'insumo contado', 'insumos contados')}`;
    $('countResultBody').innerHTML = a ? countAnalysisHtml(a) : '<p class="inv-ca-foot">Conteo guardado.</p>';
    openModal('modalCountResult');
  }

  function renderCountDetail() {
    const detail = $('countDetail');
    const c = state.counts.find((x) => x.id === state.selected.count);
    if (!c) {
      detail.innerHTML = emptyStateHtml('mouse-pointer-click', 'Elegí un conteo', 'Lo que decía el sistema, lo que se contó y la diferencia aparecen acá.');
      utils.renderIcons();
      return;
    }

    const nota = c.is_first_count
      ? '<p class="inv-detail-note">Primer conteo de la sucursal: es su inventario de arranque. Las diferencias son lo que ya había antes de que el sistema llevara la cuenta, no faltantes.</p>'
      : '';

    detail.innerHTML = `
      ${detailBackHtml()}
      <div class="inv-detail-header">
        <span class="inv-detail-thumb inv-detail-thumb-count"><i data-lucide="${c.is_first_count ? 'flag' : 'clipboard-check'}"></i></span>
        <span class="inv-badge muted">Conteo #${c.id}</span>
      </div>
      <h3>${esc(countTitle(c))}</h3>
      <p class="inv-detail-sub">${esc(utils.formatDateTime(c.counted_at))} · ${esc(c.branch_name)}</p>
      ${nota}
      ${c.notes ? `<p class="inv-detail-note">${esc(c.notes)}</p>` : ''}
      <div class="inv-metrics">
        <div><span>Contados</span><strong>${c.items.length} <small>${c.items.length === 1 ? 'insumo' : 'insumos'}</small></strong></div>
      </div>
      <div class="inv-detail-section-header"><span>Qué faltó, qué sobró y qué cuadró</span></div>
      <div class="inv-count-analysis" id="countAnalysisBox"><div class="inv-ca-loading">Calculando…</div></div>
      <div class="inv-detail-section-header"><span>Detalles</span></div>
      <div class="inv-detail-rows">
        <div><span>Sucursal</span><strong>${esc(c.branch_name)}</strong></div>
        <div><span>Contó</span><strong>${esc(c.counted_by_name)}</strong></div>
        <div><span>Cuándo</span><strong>${esc(utils.formatDateTime(c.counted_at))}</strong></div>
      </div>`;
    utils.renderIcons();
    countAnalysisFor(c.id)
      .then((a) => {
        const box = $('countAnalysisBox');
        if (!box || state.selected.count !== c.id) return;
        box.innerHTML = countAnalysisHtml(a);
        utils.renderIcons();
      })
      .catch(() => {
        const box = $('countAnalysisBox');
        if (box) box.innerHTML = '<p class="inv-ca-foot">No se pudo calcular el análisis. Probá de nuevo en un rato.</p>';
      });
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
    const seq = ++loadSeq.countStock;
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
      // Si se cambió la sucursal del conteo mientras tanto, las cifras de "Sistema" de la
      // anterior no se muestran en la nueva.
      if (seq !== loadSeq.countStock) return;
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
      if (state.countOnlyStocked && !Number(r.entered) && !Number(r.wasted) && !Number(r.adjusted) && !Number(r.transferred)) return false;
      return !q || `${r.item_name} ${r.category || ''}`.toLowerCase().includes(q);
    });
  }

  function countDiffHtml(row) {
    const valor = state.countEntries.get(row.inventory_item_id);
    if (valor == null) return '—';
    const dif = Number(valor) - Number(row.on_hand);
    if (state.countIsFirst || Math.abs(dif) < 0.0005) return '<span class="inv-count-diff-neutral">✓</span>';
    return `<span class="inv-count-diff-neutral">${esc(signedQty(dif))}</span>`;
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
            <span class="inv-line-label">Registrado</span>
            <span class="inv-count-system${sistema < 0 ? ' is-negative' : ''}">${esc(qty(sistema))}</span>
          </div>
          <div class="inv-count-cell">
            <span class="inv-line-label">Contado</span>
            <input type="number" class="modal-input inv-count-input" min="0" step="0.001" inputmode="decimal"
                   placeholder="—" value="${valor != null ? esc(valor) : ''}" aria-label="Cantidad contada de ${esc(r.item_name)}">
          </div>
          <div class="inv-count-cell">
            <span class="inv-line-label">Dif.</span>
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

    $('countProgress').textContent = contados ? pluralize(contados, 'insumo contado', 'insumos contados') : 'Nada contado todavía';

    const total = $('countTotal');
    total.classList.remove('is-short', 'is-over');
    // Qué faltó y qué sobró no se puede saber acá: falta descontar lo que se usó en los platos
    // vendidos (ventas de Invu × recetas), y eso lo calcula el servidor al guardar.
    if (state.countIsFirst && conCosto) {
      total.textContent = money(Math.abs(valor));
    } else {
      total.textContent = contados ? String(contados) : '—';
    }
    const nota = document.querySelector('#modalCount .inv-total-box small');
    if (nota) {
      nota.textContent = state.countIsFirst
        ? 'Valor de lo que había, al costo del último cargamento'
        : 'Al guardar ves qué faltó y qué sobró, descontando lo vendido';
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
      showCountResult(creado);

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
    const dashFilter = $('dashboardBranch');

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
      if (dashFilter) dashFilter.hidden = true;
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
      if (dashFilter) {
        dashFilter.innerHTML = `<option value="">Todas las sucursales</option>${options}`;
        dashFilter.hidden = false;
      }
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
  FarmhouseShell.fillUserHeader({ nameId: 'invAgentName', roleId: 'invAgentRole', avatarId: 'invAgentAvatar' }, existingUser);

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

  // Fase 4: la vista de tablet (tablet.html) linkea acá con ?open=shipment|waste|count en vez
  // de reconstruir esos formularios — un botón grande que abre el modal de siempre.
  // Merma entra a su pantalla y NO abre el formulario sola: quien llega quiere ver lo registrado
  // y decide si registra (lo pidió el negocio); el formulario se abre con "Registrar merma".
  const openParam = new URLSearchParams(window.location.search).get('open');
  const AUTO_OPEN = {
    shipment: () => { setView('cargamentos'); openShipmentModal(); },
    waste: () => { setView('merma'); },
    count: () => { setView('conteo'); openCountModal(); },
  };
  AUTO_OPEN[openParam]?.();
});
