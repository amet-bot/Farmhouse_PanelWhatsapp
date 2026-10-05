/**
 * Farmhouse - Inventario
 *
 * Rail de navegación + columna de lista + panel de detalle, con seis vistas — Resumen, Mercancía
 * recibida (cargamentos), Existencias, Conteo y, en Catálogo, Insumos y Proveedores. La merma se
 * registra aparte en /merma (merma rápida) y se analiza en Reportes → Merma.
 *
 * La existencia de un insumo es lo que entró, menos lo que salió por merma, más las diferencias de
 * los conteos físicos. El servidor la calcula y la sirve en /inventory/stock; aquí no se recalcula
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
  {
    // La merma ya no está en Inventario (se registra en /merma y se analiza en Reportes).
    const q = new URLSearchParams(window.location.search);
    if (q.get('open') === 'waste') { window.location.replace('/merma'); return; }
    if (q.get('view') === 'merma') {
      const w = Number(q.get('waste'));
      window.location.replace(w ? `/merma?waste=${w}` : '/link?view=merma');
      return;
    }
  }

  // ---- Llegar desde Operación de Sucursal ----
  // El inicio de la sucursal (/operacion) abre "Recibir mercancía" y "Hacer conteo" con
  // ?open=shipment|count&from=operacion. Ahí quien recibe no viene a mirar Inventario: el
  // formulario abre apenas se puede, la flecha de arriba vuelve a Operación y, al guardar (después
  // de ver el resultado) o al cancelar, se vuelve allá solo. Sin `from` todo sigue como siempre.
  const FROM_OPERACION = new URLSearchParams(window.location.search).get('from') === 'operacion';
  const OPERACION_URL = '/operacion';
  // replace y no href: con "atrás" no tiene que volver a abrirse el formulario ya guardado.
  const volverAOperacion = () => { window.location.replace(OPERACION_URL); };
  if (FROM_OPERACION) {
    const back = document.getElementById('invBack');
    if (back) {
      back.href = OPERACION_URL;
      back.title = 'Volver a Operación de Sucursal';
      back.dataset.short = 'Operación';
      back.classList.add('inv-back-from-op');
      const label = back.querySelector('span');
      if (label) label.textContent = 'Operación de Sucursal';
    }
    ['btnShipmentResultDone', 'btnCountResultDone'].forEach((id) => {
      const b = document.getElementById(id);
      if (b) b.textContent = 'Listo, volver a Operación';
    });
  }

  const PAGE_SIZE = 50;         // tamaño de página de la lista de cargamentos
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
    selected: { shipment: null, item: null, supplier: null, count: null },
    search: { shipment: '', item: '', supplier: '', stock: '', count: '', countItem: '' },
    itemKind: '',   // filtro de Insumos: '' | 'materia_prima' | 'casa'
    selectedSupplierId: '',

    // ---- Merma y existencias ----
    // Tanda aparte y SIN filtrar, igual que state.analytics para los cargamentos: el Resumen
    // no puede cambiar de cifras porque alguien movió el filtro de sucursal en la vista Merma.
    stock: [],
    stockBranchFilter: '',
    stockOnlyMoved: true,
    // Existencias de la sucursal elegida en el modal de merma, para poder mostrar "te quedan 4"
    // al lado de cada línea sin pedirle una consulta al servidor por cada tecla.

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

    // Qué lista no se pudo traer: así una falla de red no se confunde con "todavía no hay nada"
    // y la lista ofrece "Reintentar" en vez de un vacío que miente.
    loadError: { shipments: false, counts: false, stock: false, catalogs: false, countStock: false },
  };

  // Quien registró un cargamento puede borrarlo durante 24 horas (igual que el servidor).
  const SHIPMENT_SELF_DELETE_MS = 24 * 60 * 60 * 1000;

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

  // Fotos de evidencia de la merma (ver "Evidencia de la merma" más abajo). Arriba y no allá: el detalle de una
  // merma se puede pintar antes de que el módulo llegue a esa parte.
  const PHOTO_MAX_SIDE = 1600;          // px del lado largo: se ve bien el detalle y pesa ~300 KB
  const SHIPMENT_PHOTOS_MAX = 4;        // factura (y lo que llegó mal): mismo tope que el servidor
  let pendingShipmentPhotos = [];       // [{ blob, url }] del formulario; se suben al guardar
  const shipmentPhotoUrls = new Map();

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
  // hacer nada. Aquí la lista y el detalle pasan a ser dos pantallas que se turnan (la clase
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

  /** Lo que se muestra cuando una lista no se pudo traer: el motivo y un botón para reintentar. */
  const errorStateHtml = (retryKey, text = 'Revisa la conexión a internet y vuelve a intentarlo.') => `
    <div class="inv-empty is-error" role="alert">
      <span class="inv-empty-icon"><i data-lucide="wifi-off"></i></span>
      <strong>No se pudo cargar</strong>
      <p>${esc(text)}</p>
      <button type="button" class="inv-secondary-btn" data-retry="${retryKey}"><i data-lucide="refresh-cw"></i> Reintentar</button>
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

  /** Cierre "por código" (después de guardar): no pregunta nada ni dispara lo de `modalAfterClose`. */
  function closeModal(id) {
    $(id)?.classList.remove('active');
  }

  // Cierre pedido por la persona (Cancelar, ✕, tocar afuera, Escape). Cada modal puede tener:
  //   · modalCanClose[id]()  → false para no cerrarse (formulario a medio llenar o guardando).
  //   · modalAfterClose[id]() → qué pasa después (desde Operación: volver allá).
  const modalCanClose = {};
  const modalAfterClose = {};
  // Los formularios largos no se cierran por tocar afuera sin querer: con el dedo pasa seguido y
  // se perdía lo anotado. Se cierran con Cancelar o con la ✕.
  const MODALS_SIN_CIERRE_AFUERA = new Set(['modalShipment', 'modalCount']);

  function requestCloseModal(id) {
    if (!$(id)?.classList.contains('active')) return;
    if (modalCanClose[id] && !modalCanClose[id]()) return;
    closeModal(id);
    modalAfterClose[id]?.();
  }

  document.querySelectorAll('[data-close-modal]').forEach((btn) => {
    btn.addEventListener('click', () => requestCloseModal(btn.dataset.closeModal));
  });

  document.querySelectorAll('.modal-backdrop').forEach((backdrop) => {
    backdrop.addEventListener('click', (e) => {
      if (e.target !== backdrop || MODALS_SIN_CIERRE_AFUERA.has(backdrop.id)) return;
      requestCloseModal(backdrop.id);
    });
  });

  document.addEventListener('keydown', (e) => {
    if (e.key !== 'Escape') return;
    // La cámara y el visor de fotos van encima de todo y se cierran solos con Escape (más abajo).
    if ($('shipmentCamera')?.hidden === false || $('wastePhotoViewer')?.hidden === false) return;
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
    if (open.length) requestCloseModal(open[open.length - 1].id);
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
      existencias: $('stockTable'),
      conteo: $('countList'),
    }[view]);

    // El tablero es la consulta más pesada de la página: se pide recién cuando se mira.
    if (view === 'resumen') ensureDashboard();

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

  document.querySelectorAll('[data-open-count]').forEach((btn) => {
    btn.addEventListener('click', () => openCountModal());
  });

  // ==========================================================================
  // Carga de datos
  // ==========================================================================
  // Número de la petición más reciente por lista. Cambiar el filtro de sucursal dos veces
  // seguidas (o tocar "Cargar más" durante un reinicio) dejaba que la respuesta vieja llegara
  // después y se concatenara: filas de dos sucursales mezcladas y el offset corrido.
  const loadSeq = { shipments: 0, counts: 0, stock: 0, countStock: 0 };

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
      state.loadError.shipments = false;
    } catch (err) {
      if (seq !== loadSeq.shipments) return;
      utils.showToast(err.message || 'No se pudo cargar el historial.', 'error');
      state.shipmentsHasMore = false;
      // Solo si no hay nada que mostrar: si falló "Cargar más", lo ya cargado sigue sirviendo.
      state.loadError.shipments = !state.shipments.length;
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
      utils.showToast('No se pudieron cargar las cifras de los cargamentos. Recarga la página en un rato.', 'error');
    }
  }

  async function loadCatalogs() {
    let fallo = false;
    const [items, suppliers] = await Promise.all([
      api.get('/inventory/items?limit=500').catch(() => { fallo = true; return []; }),
      api.get('/inventory/suppliers?limit=200').catch(() => { fallo = true; return []; }),
    ]);
    state.items = items;
    state.suppliers = suppliers;
    state.loadError.catalogs = fallo;
    if (fallo) utils.showToast('No se pudo cargar el catálogo de insumos y proveedores.', 'error');

    // Alimenta el datalist de categorías del modal con las que ya existen.
    const categories = Array.from(new Set(items.map((i) => i.category).filter(Boolean))).sort();
    $('categoryOptions').innerHTML = categories.map((c) => `<option value="${esc(c)}"></option>`).join('');
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
      state.loadError.counts = false;
    } catch (err) {
      if (seq !== loadSeq.counts) return;
      utils.showToast(err.message || 'No se pudieron cargar los conteos.', 'error');
      state.countsHasMore = false;
      state.loadError.counts = !state.counts.length;
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
      `Los insumos se traen de Invu (Ingredientes) una vez al día, y se pueden seguir creando aquí. ` +
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

  async function loadStock() {
    const seq = ++loadSeq.stock;
    const params = new URLSearchParams();
    if (state.stockBranchFilter) params.set('branch_id', state.stockBranchFilter);
    if (state.stockOnlyMoved) params.set('only_stocked', 'true');
    try {
      const rows = await api.get(`/inventory/stock?${params.toString()}`);
      if (seq !== loadSeq.stock) return;
      state.stock = rows;
      state.loadError.stock = false;
    } catch (err) {
      if (seq !== loadSeq.stock) return;
      state.stock = [];
      state.loadError.stock = true;
      utils.showToast(err.message || 'No se pudieron cargar las existencias.', 'error');
    }
    renderStockTable();
    animarEntrada($('stockTable'));
  }

  // "Reintentar" de las listas que no se pudieron traer (ver errorStateHtml).
  document.addEventListener('click', async (e) => {
    const btn = e.target.closest('[data-retry]');
    if (!btn) return;
    btn.disabled = true;
    btn.textContent = 'Cargando…';
    const key = btn.dataset.retry;
    if (key === 'shipments') await loadShipments({ reset: true });
    else if (key === 'counts') await loadCounts({ reset: true });
    else if (key === 'stock') await loadStock();
    else if (key === 'countStock') await loadCountStock();
    else if (key === 'catalogs') {
      await loadCatalogs();
      renderItemList();
      renderSupplierList();
    }
  });

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
  // ==========================================================================
  // Tablero del Resumen: ventas, compras, merma, faltantes y costo de lo vendido
  // ==========================================================================
  const dashboard = { days: 7, branch: '', data: null, seq: 0, stale: true };

  /** Pide el tablero si hace falta (la primera vez que se mira, o si algo cambió desde entonces). */
  function ensureDashboard() {
    if (!dashboard.stale) return;
    dashboard.stale = false;
    loadDashboard();
  }

  /** Un cargamento, un conteo o un borrado cambian las cifras: se recalcula al volver a mirarlo. */
  function invalidateDashboard() {
    dashboard.stale = true;
    if (state.view === 'resumen') ensureDashboard();
  }

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
      { label: 'Compras', value: money(t.purchases), sub: t.purchases_pct_sales != null ? `${pctTxt(t.purchases_pct_sales)} de la venta` : 'Cargamentos con costo', delta: dashDelta(t.purchases, p.purchases, false) },
      { label: 'Merma', value: `${t.waste_estimated ? '≈ ' : ''}${money(t.waste)}`, sub: t.waste_pct_sales != null ? `${pctTxt(t.waste_pct_sales)} de la venta` : 'Lo que se botó', delta: dashDelta(t.waste, p.waste, false), bad: true },
      { label: 'Faltó en conteos', value: t.counts ? money(t.count_missing) : '—', sub: t.counts ? pluralize(t.counts, 'conteo', 'conteos') : 'Sin conteos en el período', delta: t.counts ? dashDelta(t.count_missing, p.count_missing, false) : '', bad: true },
    ];

    const tabla = d.branches.length > 1 ? `
      <div class="inv-dash-table-wrap">
        <table class="inv-dash-table">
          <thead><tr><th>Sucursal</th><th class="num">Compras</th><th class="num">Merma</th><th class="num">Merma / venta</th><th class="num">Faltó</th></tr></thead>
          <tbody>${d.branches.map((b) => `
            <tr>
              <td data-label="Sucursal"><strong>${esc(b.branch_name)}</strong></td>
              <td class="num" data-label="Compras">${esc(money(b.purchases))}</td>
              <td class="num" data-label="Merma">${esc(money(b.waste))}</td>
              <td class="num" data-label="Merma / venta">${esc(pctTxt(b.waste_pct_sales))}</td>
              <td class="num" data-label="Faltó">${b.counts ? esc(money(b.count_missing)) : '—'}</td>
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
      box.innerHTML = '<p class="inv-ca-foot">No se pudo calcular el tablero. Prueba de nuevo en un rato.</p>';
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
        sub: recent.length ? `${costed} de ${recent.length} con costo cargado` : 'Carga el costo unitario para verlo',
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
    // renderResumen solo corre al cargar y después de registrar algo, nunca por un clic suelto:
    // aquí animar siempre es correcto.
    animarEntrada($('viewResumen'), $('recentShipments'));
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
      container.innerHTML = emptyStateHtml('truck', 'Todavía no hay cargamentos', 'Registra el primero y aparecerá aquí.');
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
      list.innerHTML = state.loadError.shipments
        ? errorStateHtml('shipments')
        : state.search.shipment
          ? emptyStateHtml('search-x', 'Sin resultados', 'Prueba con otro proveedor, insumo o persona.')
          : emptyStateHtml('truck', 'Todavía no hay cargamentos', 'Registra lo que llegó y va a quedar aquí, con su detalle y su costo.');
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
          ${s.has_issues
            ? '<span class="inv-badge warn">Con diferencias</span>'
            : `<span class="inv-badge muted">${pluralize(s.items.length, 'ítem', 'ítems')}</span>`}
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
      frases.push('<span class="inv-line-warn">Sin costo: cárgalo en el próximo cargamento para que la merma y el conteo usen el precio real.</span>');
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
    let historial = '';
    if (ins.supplier_name && ins.supplier_shipments_90d) {
      historial = ins.supplier_issues_90d
        ? `<p class="inv-wi-branch"><strong>${esc(ins.supplier_name)}</strong>: ${ins.supplier_issues_90d} de ${ins.supplier_shipments_90d} ${ins.supplier_shipments_90d === 1 ? 'entrega revisada' : 'entregas revisadas'} contra factura ${ins.supplier_issues_90d === 1 ? 'vino' : 'vinieron'} con diferencias en 90 días${Number(ins.supplier_claim_90d) ? ` (${esc(money(ins.supplier_claim_90d))} faltante)` : ''}.</p>`
        : `<p class="inv-wi-branch"><strong>${esc(ins.supplier_name)}</strong>: ${ins.supplier_shipments_90d === 1 ? 'la entrega revisada' : `las ${ins.supplier_shipments_90d} entregas revisadas`} contra factura en 90 días llegaron completas.</p>`;
    }
    return `
      ${sinCosto}
      ${ins.items.map(shipmentInsightItemHtml).join('')}
      <p class="inv-wi-branch">${semana}${prov}</p>
      ${historial}`;
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
    $('shipmentResultBody').innerHTML = shipmentIssuesHtml(cargamento, true)
      + (cargamento.insights ? shipmentInsightsHtml(cargamento.insights) : '');
    openModal('modalShipmentResult');
    utils.renderIcons();
  }

  /** Un renglón con diferencia, en una frase. */
  function lineIssueText(l) {
    const u = unitShort(l.unit);
    let txt = `<strong>${esc(l.item_name)}</strong>: `;
    const partes = [];
    if (l.invoiced_quantity != null) partes.push(`facturado ${esc(qty(l.invoiced_quantity))} ${esc(u)}, llegó ${esc(qty(l.quantity))} ${esc(u)}`);
    if ((l.line_status === 'falto' || l.line_status === 'sobro') && l.invoiced_quantity != null) {
      partes.push(`${RECV_TEXT[l.line_status]} ${esc(qty(Math.abs(Number(l.invoiced_quantity) - Number(l.quantity))))} ${esc(u)}`);
    } else if (RECV_TEXT[l.line_status]) {
      partes.push(RECV_TEXT[l.line_status]);
    }
    txt += partes.join(' · ');
    if (l.line_note) txt += ` — ${esc(l.line_note)}`;
    if (l.claim_value != null && Number(l.claim_value)) txt += ` · ${esc(money(l.claim_value))}`;
    return txt;
  }

  /** Cómo llegó contra la factura. `alGuardar`: además, si se avisó al encargado. */
  function shipmentIssuesHtml(c, alGuardar) {
    if (c.has_issues == null) return '';
    if (!c.has_issues) {
      return `<div class="inv-recv-result is-ok"><i data-lucide="check-circle-2"></i><div><strong>Todo llegó como decía la factura${c.invoice_number ? ` ${esc(c.invoice_number)}` : ''}.</strong></div></div>`;
    }
    const lineas = c.items.filter((l) => l.line_status && l.line_status !== 'ok').map(lineIssueText);
    let aviso = '';
    if (alGuardar) {
      aviso = c.notified
        ? 'Se abrió una incidencia y se le avisó al encargado por notificación.'
        : 'Se abrió una incidencia en Operación de Sucursal. Ningún encargado tiene las notificaciones activadas en este momento: avísale también por el grupo.';
    } else if (c.incident_id) {
      aviso = `Incidencia #${c.incident_id} en Operación de Sucursal.`;
    }
    return `
      <div class="inv-recv-result is-warn">
        <i data-lucide="alert-triangle"></i>
        <div>
          <strong>${pluralize(c.issues_count, 'diferencia', 'diferencias')} con la factura${c.invoice_number ? ` ${esc(c.invoice_number)}` : ''}</strong>
          <ul>${lineas.map((x) => `<li>${x}</li>`).join('')}</ul>
          ${c.claim_total != null && Number(c.claim_total) ? `<p>Para reclamar: <strong>${esc(money(c.claim_total))}</strong></p>` : ''}
          ${aviso ? `<p class="inv-recv-result-note">${aviso}</p>` : ''}
        </div>
      </div>`;
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
      detail.innerHTML = emptyStateHtml('mouse-pointer-click', 'Elige un cargamento', 'Su detalle completo — insumos, cantidades y costos — aparece aquí.');
      utils.renderIcons();
      return;
    }

    const conFactura = s.items.some((l) => l.invoiced_quantity != null);
    const rowsHtml = s.items.map((l) => {
      const subtotal = l.unit_cost != null ? money(Number(l.quantity) * Number(l.unit_cost)) : '—';
      const marca = l.line_status && l.line_status !== 'ok'
        ? ` <span class="inv-badge warn">${esc(RECV_TEXT[l.line_status] || l.line_status)}</span>` : '';
      return `
        <tr>
          <td class="inv-td-name" data-label="Insumo">${esc(l.item_name)}${marca}</td>
          ${conFactura ? `<td class="num" data-label="Facturado">${l.invoiced_quantity != null ? `${esc(qty(l.invoiced_quantity))} ${esc(l.unit)}` : '—'}</td>` : ''}
          <td class="num" data-label="${conFactura ? 'Llegó' : 'Cantidad'}">${esc(qty(l.quantity))} ${esc(l.unit)}</td>
          <td class="num" data-label="Costo unit.">${l.unit_cost != null ? unitCost(l.unit_cost) : '—'}</td>
          <td class="num" data-label="Subtotal">${subtotal}</td>
        </tr>`;
    }).join('');

    const footHtml = s.total_cost != null ? `
      <tfoot>
        <tr>
          <td colspan="${conFactura ? 4 : 3}" class="inv-td-total-label">Total</td>
          <td class="num" data-label="Total">${money(s.total_cost)}</td>
        </tr>
      </tfoot>` : '';

    detail.innerHTML = `
      ${detailBackHtml()}
      <div class="inv-detail-header">
        <span class="inv-detail-thumb"><i data-lucide="truck"></i></span>
        <span class="inv-badge ok">Cargamento #${s.id}</span>
        ${s.has_issues === true ? '<span class="inv-badge warn">Con diferencias</span>' : ''}
        ${s.has_issues === false ? '<span class="inv-badge ok">Cuadró con la factura</span>' : ''}
      </div>
      <h3>${esc(s.supplier_name || 'Sin proveedor')}</h3>
      <p class="inv-detail-sub">${esc(utils.formatDateTime(s.received_at))} · ${esc(s.branch_name)}${s.invoice_number ? ` · Factura ${esc(s.invoice_number)}` : ''}</p>
      ${s.notes ? `<p class="inv-detail-note">${esc(s.notes)}</p>` : ''}
      ${s.has_issues ? shipmentIssuesHtml(s, false) : ''}
      <div class="inv-metrics">
        <div><span>Ítems</span><strong>${s.items.length} <small>${s.items.length === 1 ? 'línea' : 'líneas'}</small></strong></div>
        <div><span>Total</span><strong>${s.total_cost != null ? money(s.total_cost) : '—'}</strong></div>
      </div>
      <div class="inv-detail-section-header"><span>Insumos recibidos</span></div>
      <table class="inv-detail-table">
        <thead>
          <tr><th>Insumo</th>${conFactura ? '<th class="num">Facturado</th><th class="num">Llegó</th>' : '<th class="num">Cantidad</th>'}<th class="num">Costo unit.</th><th class="num">Subtotal</th></tr>
        </thead>
        <tbody>${rowsHtml}</tbody>
        ${footHtml}
      </table>
      ${(s.photos || []).length ? `
        <div class="inv-detail-section-header"><span>Foto de la factura</span></div>
        <div class="inv-photo-grid" id="shipmentDetailPhotos">
          ${s.photos.map((p) => `<button type="button" class="inv-photo-thumb is-loading" data-shipment-photo="${p.id}" aria-label="Ver foto"></button>`).join('')}
        </div>` : ''}
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
          <small>${hasPerm('inventory.adjust') ? 'Si se cargó por error.' : 'Si te equivocaste: puedes borrarlo hasta 24 horas después de cargarlo.'}</small>
        </div>`);
      $('btnDeleteShipment').addEventListener('click', () => openShipmentDelete(s));
    }
    utils.renderIcons();
    $('shipmentDetail').querySelectorAll('[data-shipment-photo]').forEach((btn) => {
      shipmentPhotoUrl(s.id, Number(btn.dataset.shipmentPhoto))
        .then((url) => {
          if (!btn.isConnected) return;
          btn.classList.remove('is-loading');
          btn.innerHTML = '<img src="' + url + '" alt="Foto de la factura">';
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
    return Date.now() - (utils._parseServerDate(s.created_at) || new Date(0)).getTime() <= SHIPMENT_SELF_DELETE_MS;
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
      invalidateDashboard();
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
    loadExpected();
    loadSupplierIssues();
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
      list.innerHTML = state.loadError.catalogs
        ? errorStateHtml('catalogs')
        : q
          ? emptyStateHtml('search-x', 'Sin resultados', 'Ningún insumo del catálogo coincide con esa búsqueda.')
          : emptyStateHtml('layout-list', 'Catálogo vacío', 'Crea tu primer insumo o agrégalo al vuelo mientras registras un cargamento.');
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
      detail.innerHTML = emptyStateHtml('mouse-pointer-click', 'Elige un insumo', 'Cuánto entró, de quién y a qué costo aparece aquí.');
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
      btn.textContent = 'Guardando…';
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
        btn.textContent = 'Guardar';
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
      list.innerHTML = state.loadError.catalogs
        ? errorStateHtml('catalogs')
        : q
          ? emptyStateHtml('search-x', 'Sin resultados', 'Ningún proveedor coincide con esa búsqueda.')
          : (state.invu.configured
              ? emptyStateHtml('building-2', 'Sin proveedores', 'Se cargan en Invu. Toca "Sincronizar con Invu" para traerlos.')
              : emptyStateHtml('building-2', 'Sin proveedores', 'Crea el primero o agrégalo al vuelo mientras registras un cargamento.'));
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
      detail.innerHTML = emptyStateHtml('mouse-pointer-click', 'Elige un proveedor', 'Cuánto te trae y cuánto te cuesta aparece aquí.');
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
    if (!name) { showModalError('itemError', 'Pon un nombre para el insumo.'); return; }
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
    if (!name) { showModalError('supplierError', 'Pon un nombre para el proveedor.'); return; }

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
    // En los formularios largos el aviso queda fijo arriba (CSS .inv-sticky-error); en el resto,
    // se trae a la vista por si se estaba más abajo.
    if (!box.closest('.inv-sticky-body')) box.scrollIntoView({ block: 'nearest' });
  }

  // ==========================================================================
  // Modal: registrar cargamento
  // ==========================================================================
  const linesContainer = $('shipmentLines');
  const lineTemplate = $('shipmentLineTemplate');
  const supplierInput = $('supplierInput');
  const supplierSuggestions = $('supplierSuggestions');

  /** `expected`: el cargamento agendado que se está recibiendo (llena proveedor y sucursal). */
  function openShipmentModal(expected = null) {
    resetShipmentForm();
    if (expected) {
      state.receivingExpected = expected;
      if (expected.supplier_id) selectSupplier({ id: expected.supplier_id, name: expected.supplier_name });
      if (!state.fixedBranchId) $('branchSelect').value = String(expected.branch_id);
      const banner = $('shipmentExpectedBanner');
      banner.innerHTML = `<i data-lucide="calendar-check"></i><span>Recibiendo lo agendado: <strong>${esc(expected.supplier_name || 'Cargamento')}</strong> · ${esc(expectedWhen(expected))}${expected.notes ? ` · ${esc(expected.notes)}` : ''}${expected.items?.length ? ` · ${expected.items.length} línea${expected.items.length === 1 ? '' : 's'} de la orden ya cargadas` : ''}</span>`;
      banner.hidden = false;
      prefillLinesFromOrder(expected.items || []);
    }
    openModal('modalShipment');
    utils.renderIcons();
    // Cómo quedó al abrir (vacío, o con las líneas de la orden): "sin guardar" es distinto de esto.
    shipmentSnapshot = shipmentFormSignature();
  }

  // ---- Cambios sin guardar ----
  let shipmentSnapshot = '';
  let shipmentSaving = false;

  /** Todo lo que la persona puede haber escrito en el formulario, en una sola cadena. */
  function shipmentFormSignature() {
    const lines = Array.from(linesContainer.querySelectorAll('.inv-line-row')).map((row) => [
      row.querySelector('.inv-item-input').value,
      row.querySelector('.inv-line-invoiced').value,
      row.querySelector('.inv-line-qty').value,
      row.querySelector('.inv-line-cost').value,
      row.dataset.recvStatus || '',
      row.querySelector('.inv-recv-note').value,
    ].join('\u0001'));
    return [
      supplierInput.value, $('notesInput').value, $('invoiceNumberInput').value,
      $('invoiceModeToggle').checked, $('receivedAtInput').value, pendingShipmentPhotos.length,
      ...lines,
    ].join('\u0002');
  }

  const shipmentIsDirty = () => $('modalShipment').classList.contains('active')
    && shipmentFormSignature() !== shipmentSnapshot;

  modalCanClose.modalShipment = () => {
    if (shipmentSaving) return false;   // a medio guardar: cerrar ahora dejaría sin ver el resultado
    if (!shipmentIsDirty()) return true;
    return window.confirm('¿Salir sin guardar? Se pierde lo que anotaste de esta mercancía.');
  };
  modalAfterClose.modalShipment = () => { if (FROM_OPERACION) volverAOperacion(); };
  modalAfterClose.modalShipmentResult = () => { if (FROM_OPERACION) volverAOperacion(); };

  /**
   * Una orden de compra (Abastecimiento) trae sus líneas: la recepción arranca con cada una como
   * "facturado" y "llegó" iguales, y el equipo solo corrige lo que vino distinto.
   */
  function prefillLinesFromOrder(lines) {
    if (!lines.length) return;
    linesContainer.innerHTML = '';
    lines.forEach((l) => {
      createLineRow();
      const row = linesContainer.lastElementChild;
      const itemInput = row.querySelector('.inv-item-input');
      itemInput.value = l.item_name;
      itemInput.dataset.itemId = String(l.inventory_item_id);
      itemInput.dataset.unit = l.unit || '';
      row.querySelector('.inv-line-unit').textContent = l.unit ? `Se cuenta en ${l.unit}` : '';
      row.querySelector('.inv-line-invoiced').value = String(Number(l.quantity));
      row.querySelector('.inv-line-qty').value = String(Number(l.quantity));
      row.dataset.qtyAuto = '1';
      if (l.unit_cost != null) row.querySelector('.inv-line-cost').value = String(Number(l.unit_cost));
      updateShipmentCostHint(row);
      updateLineReceipt(row);
    });
    updateShipmentTotal();
  }

  // ---- Recibir contra factura ----
  const invoiceMode = () => $('invoiceModeToggle').checked;

  function applyInvoiceMode() {
    const on = invoiceMode();
    $('modalShipment').classList.toggle('inv-invoice-mode', on);
    $('invoiceFields').hidden = !on;
    $('shipmentQtyHead').textContent = on ? 'Llegó' : 'Cantidad';
    linesContainer.querySelectorAll('.inv-line-row').forEach(updateLineReceipt);
  }
  $('invoiceModeToggle')?.addEventListener('change', applyInvoiceMode);

  /** Lo facturado, lo que llegó y cómo llegó un renglón del formulario. */
  function lineReceipt(row) {
    const inv = row.querySelector('.inv-line-invoiced').value;
    const q = row.querySelector('.inv-line-qty').value;
    return {
      invoiced: inv === '' ? null : Number(inv),
      qty: q === '' ? null : Number(q),
      status: row.dataset.recvStatus || '',
      note: row.querySelector('.inv-recv-note').value.trim(),
    };
  }

  /** La franja de "cómo llegó" debajo de cada renglón: la diferencia al vuelo y los botones. */
  function updateLineReceipt(row) {
    const on = invoiceMode();
    row.querySelector('.inv-ship-qty-label').textContent = on ? 'Llegó' : 'Cantidad';
    const strip = row.querySelector('.inv-recv-strip');
    const itemInput = row.querySelector('.inv-item-input');
    strip.hidden = !(on && itemInput.dataset.itemId);
    if (strip.hidden) return;
    const { invoiced, qty: q, status } = lineReceipt(row);
    const u = unitShort(itemInput.dataset.unit || '');
    const diff = strip.querySelector('.inv-recv-diff');
    let txt = 'Anota lo facturado';
    let cls = 'muted';
    if (invoiced != null && q != null) {
      const d = q - invoiced;
      if (Math.abs(d) < 0.0005) { txt = 'Completo'; cls = 'ok'; }
      else if (d < 0) { txt = `Faltan ${qty(-d)} ${u}`; cls = 'short'; }
      else { txt = `Sobran ${qty(d)} ${u}`; cls = 'over'; }
    }
    diff.textContent = txt;
    diff.className = `inv-recv-diff is-${cls}`;
    strip.querySelectorAll('.inv-recv-chip').forEach((c) => {
      const activo = c.dataset.status === status;
      c.classList.toggle('is-active', activo);
      c.setAttribute('aria-checked', String(activo));
    });
    strip.querySelector('.inv-recv-note').hidden = !status;
  }

  const RECV_TEXT = { falto: 'faltó', sobro: 'sobró', equivocado: 'llegó equivocado', danado: 'llegó dañado' };

  /** Lo que no coincide con la factura, en frases, para enseñárselo a quien entrega. */
  function shipmentProblems(rows) {
    const problemas = [];
    let reclamo = 0;
    rows.forEach((row) => {
      const itemInput = row.querySelector('.inv-item-input');
      if (!itemInput.dataset.itemId) return;
      const { invoiced, qty: q, status, note } = lineReceipt(row);
      const u = unitShort(itemInput.dataset.unit || '');
      const d = invoiced != null && q != null ? q - invoiced : 0;
      if (!status && Math.abs(d) < 0.0005) return;
      let txt = `<strong>${esc(itemInput.value)}</strong>: `;
      if (invoiced != null) txt += `facturado ${esc(qty(invoiced))} ${esc(u)}, llegó ${esc(qty(q ?? 0))} ${esc(u)}`;
      if (Math.abs(d) >= 0.0005) txt += ` (${d < 0 ? 'faltan' : 'sobran'} ${esc(qty(Math.abs(d)))} ${esc(u)})`;
      if (status) txt += `${invoiced != null ? ' · ' : ''}${RECV_TEXT[status]}`;
      if (note) txt += ` — ${esc(note)}`;
      const costo = row.querySelector('.inv-line-cost').value;
      if (d < 0 && costo !== '') reclamo += -d * Number(costo);
      problemas.push(txt);
    });
    return { problemas, reclamo };
  }

  function resetShipmentForm() {
    $('shipmentError').style.display = 'none';
    supplierInput.value = '';
    state.selectedSupplierId = '';
    state.receivingExpected = null;
    $('notesInput').value = '';
    $('invoiceNumberInput').value = '';
    $('invoiceModeToggle').checked = true;
    $('shipmentExpectedBanner').hidden = true;
    $('receivedAtInput').value = toLocalInputValue(new Date());
    shipmentCostConfirmed = '';
    resetShipmentPhotos();
    linesContainer.innerHTML = '';
    createLineRow();
    applyInvoiceMode();
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
    const invoicedInput = row.querySelector('.inv-line-invoiced');
    const noteInput = row.querySelector('.inv-recv-note');

    itemInput.dataset.itemId = '';
    itemInput._reqId = 0;
    row.dataset.recvStatus = '';

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
      updateLineReceipt(row);
      // El costo de Invu como sugerencia, no como valor: si no se escribe nada, el cargamento
      // queda sin costo (como siempre) en vez de guardar uno que nadie confirmó.
      if (!('defaultPlaceholder' in costInput.dataset)) costInput.dataset.defaultPlaceholder = costInput.placeholder;
      costInput.placeholder = item.reference_cost != null
        ? `Ref. ${unitCost(item.reference_cost)}`
        : costInput.dataset.defaultPlaceholder;
      hideSuggestions();
      (invoiceMode() ? invoicedInput : qtyInput).focus();
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
      // En la última fila la lista quedaba debajo del borde del formulario (o del teclado).
      suggestBox.scrollIntoView({ block: 'nearest' });
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
      updateLineReceipt(row);
      clearTimeout(itemInput._debounce);
      const query = itemInput.value;
      itemInput._debounce = setTimeout(() => fetchSuggestions(query), 300);
    });

    qtyInput.addEventListener('input', () => {
      row.dataset.qtyAuto = '';   // lo escribió la persona: ya no se copia de la factura
      updateShipmentTotal();
      updateLineReceipt(row);
    });
    costInput.addEventListener('input', () => { updateShipmentTotal(); updateShipmentCostHint(row); });
    // Casi siempre llega lo facturado: "llegó" se completa igual, y se corrige si no.
    invoicedInput.addEventListener('input', () => {
      if (qtyInput.value === '' || row.dataset.qtyAuto === '1') {
        qtyInput.value = invoicedInput.value;
        row.dataset.qtyAuto = invoicedInput.value === '' ? '' : '1';
      }
      updateShipmentTotal();
      updateLineReceipt(row);
    });
    // "Siguiente" del teclado: facturado → llegó → costo → se cierra el teclado.
    [invoicedInput, qtyInput, costInput].forEach((input, i, campos) => {
      input.addEventListener('keydown', (e) => {
        if (e.key !== 'Enter') return;
        e.preventDefault();
        const siguiente = campos.slice(i + 1).find((c) => c.offsetParent !== null);
        if (siguiente) siguiente.focus();
        else input.blur();
      });
    });
    row.querySelector('.inv-recv-chips').addEventListener('click', (e) => {
      const chip = e.target.closest('.inv-recv-chip');
      if (!chip) return;
      row.dataset.recvStatus = chip.dataset.status;
      updateLineReceipt(row);
      if (chip.dataset.status) noteInput.focus();
    });

    removeBtn.addEventListener('click', () => {
      if (linesContainer.children.length > 1) {
        row.remove();
      } else {
        itemInput.value = '';
        itemInput.dataset.itemId = '';
        unitLabel.textContent = '';
        qtyInput.value = '';
        costInput.value = '';
        invoicedInput.value = '';
        noteInput.value = '';
        row.dataset.recvStatus = '';
        row.dataset.qtyAuto = '';
        updateLineReceipt(row);
      }
      updateShipmentTotal();
    });

    linesContainer.appendChild(row);
    updateLineReceipt(row);
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
      html = '<div class="inv-item-suggestion-empty">No está en Invu. Cárgalo allá y sincroniza desde Proveedores.</div>';
    }
    supplierSuggestions.innerHTML = html;
    supplierSuggestions.hidden = false;
    supplierSuggestions.scrollIntoView({ block: 'nearest' });
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
    if (!branchId) { showModalError('shipmentError', 'Elige una sucursal.'); return; }

    const rows = Array.from(linesContainer.querySelectorAll('.inv-line-row'));
    const conFactura = invoiceMode();
    const items = [];
    for (const row of rows) {
      const itemInput = row.querySelector('.inv-item-input');
      const qtyInput = row.querySelector('.inv-line-qty');
      const costInput = row.querySelector('.inv-line-cost');
      const itemId = itemInput.dataset.itemId;
      const qtyValue = qtyInput.value;
      const r = lineReceipt(row);
      const facturado = conFactura ? row.querySelector('.inv-line-invoiced').value : '';
      if (!itemId && !qtyValue && !facturado) continue; // fila vacía, se ignora
      if (!itemId) { showModalError('shipmentError', 'Elige un insumo de la lista (o crea uno nuevo) en cada fila con cantidad.'); itemInput.focus(); return; }
      if (qtyValue === '' || Number(qtyValue) < 0) {
        showModalError('shipmentError', conFactura ? `Falta cuánto llegó de "${itemInput.value}" (0 si no llegó nada).` : `Falta la cantidad de "${itemInput.value}".`);
        qtyInput.focus();
        return;
      }
      // Cero solo vale si es un faltante completo o algo que llegó mal y se devolvió.
      const ceroValido = conFactura && ((facturado !== '' && Number(facturado) > 0) || (r.status && r.status !== ''));
      if (Number(qtyValue) === 0 && !ceroValido) {
        showModalError('shipmentError', conFactura
          ? `"${itemInput.value}" dice 0: si no llegó nada, anota cuánto dice la factura.`
          : `Falta la cantidad de "${itemInput.value}".`);
        qtyInput.focus();
        return;
      }
      items.push({
        inventory_item_id: Number(itemId),
        quantity: qtyValue,
        unit_cost: costInput.value !== '' ? costInput.value : null,
        invoiced_quantity: facturado !== '' ? facturado : null,
        line_status: conFactura && r.status ? r.status : null,
        line_note: conFactura && r.status && r.note ? r.note : null,
      });
    }
    if (!items.length) { showModalError('shipmentError', 'Agrega al menos un insumo.'); return; }

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
      showModalError('shipmentError', `Revisa el costo de ${sospechosos.join(', ')}: parece el precio del paquete y va por unidad del insumo (g, ml). Si está bien, toca "Guardar lo recibido" otra vez.`);
      return;
    }

    if (supplierInput.value.trim() && !state.selectedSupplierId) {
      showModalError('shipmentError', 'Elige un proveedor de la lista (o crea uno nuevo), o deja el campo vacío.');
      return;
    }

    const receivedAtValue = $('receivedAtInput').value;
    let receivedAt = null;
    if (receivedAtValue) {
      const parsed = new Date(receivedAtValue);
      if (Number.isNaN(parsed.getTime())) {
        $('shipmentMore').open = true;   // la fecha está en "Más datos"
        showModalError('shipmentError', 'La fecha de recepción no es válida.');
        return;
      }
      // El backend guarda en UTC (ver utils._parseServerDate): se manda con zona explícita para
      // que la hora que escribió el encargado sea la que después se muestra.
      receivedAt = parsed.toISOString();
    }

    const payload = {
      branch_id: branchId,
      received_at: receivedAt,
      supplier_id: state.selectedSupplierId ? Number(state.selectedSupplierId) : null,
      notes: $('notesInput').value.trim() || null,
      invoice_number: conFactura ? ($('invoiceNumberInput').value.trim() || null) : null,
      expected_shipment_id: state.receivingExpected ? state.receivingExpected.id : null,
      items,
    };

    // Si algo no coincide con la factura, primero el resumen para enseñárselo a quien entrega.
    const { problemas, reclamo } = conFactura ? shipmentProblems(rows) : { problemas: [], reclamo: 0 };
    if (problemas.length) {
      pendingShipmentPayload = payload;
      $('shipmentCheckList').innerHTML = problemas.map((p) => `<li>${p}</li>`).join('');
      const claim = $('shipmentCheckClaim');
      claim.hidden = !(reclamo > 0);
      claim.innerHTML = reclamo > 0 ? `Para reclamar: <strong>${esc(money(reclamo))}</strong>` : '';
      openModal('modalShipmentCheck');
      utils.renderIcons();
      return;
    }
    await sendShipment(payload);
  });

  let pendingShipmentPayload = null;
  $('btnConfirmShipmentCheck')?.addEventListener('click', async () => {
    if (!pendingShipmentPayload) return;
    const payload = pendingShipmentPayload;
    pendingShipmentPayload = null;
    closeModal('modalShipmentCheck');
    await sendShipment(payload);
  });

  /** Mientras se guarda: el botón dice qué está pasando y no se puede cancelar ni tocar dos veces. */
  function setFormSaving(modalId, submitBtn, saving, text) {
    submitBtn.disabled = saving;
    submitBtn.textContent = text;
    submitBtn.setAttribute('aria-busy', String(saving));
    document.querySelectorAll(`[data-close-modal="${modalId}"]`).forEach((b) => { b.disabled = saving; });
  }

  async function sendShipment(payload) {
    const btn = $('btnSubmitShipment');
    shipmentSaving = true;
    setFormSaving('modalShipment', btn, true, 'Guardando…');
    try {
      let creado = await api.post('/inventory/shipments', payload);
      if (pendingShipmentPhotos.length) {
        btn.textContent = 'Subiendo la foto…';
        const { ultima, fallidas } = await uploadShipmentPhotos(creado.id, pendingShipmentPhotos.map((p) => p.blob));
        if (ultima) creado = { ...ultima, insights: creado.insights, notified: creado.notified };
        if (fallidas) utils.showToast(`Se guardó, pero ${pluralize(fallidas, 'foto no se pudo', 'fotos no se pudieron')} subir. Puedes agregarla después desde el cargamento.`, 'error');
      }
      shipmentSaving = false;
      closeModal('modalShipment');
      // La ventana del resultado ya dice "registrado": un aviso más abajo taparía su botón "Listo".
      showShipmentResult(creado);
      state.selected.shipment = null;
      // Desde Operación se vuelve allá al tocar "Listo": no hace falta refrescar Inventario.
      if (FROM_OPERACION) return;
      invalidateDashboard();
      // loadStock también: Existencias quedaba mostrando lo de antes del cargamento hasta
      // recargar la página (merma y conteo ya la refrescaban).
      await Promise.all([loadShipments({ reset: true }), loadAnalytics(), loadStock(), loadExpected(), loadSupplierIssues()]);
      renderResumen();
      renderItemList();
      renderSupplierList();
    } catch (err) {
      showModalError('shipmentError', err.message || 'No se pudo guardar. Revisa la conexión e inténtalo de nuevo.');
    } finally {
      shipmentSaving = false;
      setFormSaving('modalShipment', btn, false, 'Guardar lo recibido');
    }
  }

  // ---- Fotos de la factura (formulario) ----
  function renderShipmentPhotos() {
    const grid = $('shipmentPhotoPreviews');
    if (!grid) return;
    const add = grid.querySelector('.inv-photo-add-group');
    grid.querySelectorAll('.inv-photo-thumb').forEach((n) => n.remove());
    pendingShipmentPhotos.forEach((p, i) => {
      const el = document.createElement('div');
      el.className = 'inv-photo-thumb';
      el.innerHTML = `<img src="${p.url}" alt="Foto ${i + 1}">
        <button type="button" class="inv-photo-remove" data-idx="${i}" aria-label="Quitar foto ${i + 1}">&times;</button>`;
      grid.insertBefore(el, add);
    });
    add.hidden = pendingShipmentPhotos.length >= SHIPMENT_PHOTOS_MAX;
  }

  function resetShipmentPhotos() {
    pendingShipmentPhotos.forEach((p) => URL.revokeObjectURL(p.url));
    pendingShipmentPhotos = [];
    renderShipmentPhotos();
  }

  async function addShipmentPhotos(files) {
    for (const f of files) {
      if (pendingShipmentPhotos.length >= SHIPMENT_PHOTOS_MAX) break;
      const blob = await compressPhoto(f);
      pendingShipmentPhotos.push({ blob, url: URL.createObjectURL(blob) });
    }
    renderShipmentPhotos();
  }

  $('shipmentPhotoInput')?.addEventListener('change', async (e) => {
    const files = Array.from(e.target.files || []);
    e.target.value = '';
    await addShipmentPhotos(files);
  });

  $('shipmentPhotoPreviews')?.addEventListener('click', (e) => {
    const btn = e.target.closest('.inv-photo-remove');
    if (!btn) return;
    const [quitada] = pendingShipmentPhotos.splice(Number(btn.dataset.idx), 1);
    if (quitada) URL.revokeObjectURL(quitada.url);
    renderShipmentPhotos();
  });

  // ---- "Tomar foto" de la factura ----
  // (Se había ido junto con la merma y el botón quedó sin hacer nada.) Celular/tablet: el input con
  // `capture` abre la cámara del equipo, que enfoca y maneja la luz mejor que cualquier cosa hecha
  // aquí. Computadora: ahí `capture` se ignora y abriría el explorador de archivos, así que se usa
  // la webcam en vivo; sin webcam o sin permiso, cae a elegir un archivo.
  let cameraStream = null;

  function openShipmentCamera() {
    const tactil = window.matchMedia('(pointer: coarse)').matches;
    if (tactil || !navigator.mediaDevices?.getUserMedia) {
      $('shipmentCameraInput').click();
      return;
    }
    openWebcam();
  }

  async function openWebcam() {
    const shoot = $('btnCameraShoot');
    shoot.disabled = true;
    $('shipmentCamera').hidden = false;
    try {
      cameraStream = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: 'environment', width: { ideal: 1920 }, height: { ideal: 1080 } },
        audio: false,
      });
      $('shipmentCameraVideo').srcObject = cameraStream;
      shoot.disabled = false;
    } catch (err) {
      closeWebcam();
      const sinPermiso = err && (err.name === 'NotAllowedError' || err.name === 'SecurityError');
      utils.showToast(
        sinPermiso
          ? 'No hay permiso para usar la cámara. Puedes elegir la foto con «Galería».'
          : 'No se encontró una cámara. Puedes elegir la foto con «Galería».',
        'warning'
      );
      $('shipmentPhotoInput').click();
    }
  }

  function closeWebcam() {
    if (cameraStream) cameraStream.getTracks().forEach((t) => t.stop());
    cameraStream = null;
    $('shipmentCameraVideo').srcObject = null;
    $('shipmentCamera').hidden = true;
  }

  document.addEventListener('click', (e) => {
    if (e.target.closest('[data-photo-camera]')) openShipmentCamera();
  });
  $('btnCameraCancel')?.addEventListener('click', closeWebcam);
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && $('shipmentCamera') && !$('shipmentCamera').hidden) closeWebcam();
  });

  $('btnCameraShoot')?.addEventListener('click', async () => {
    const video = $('shipmentCameraVideo');
    if (!video.videoWidth) return;
    const scale = Math.min(1, PHOTO_MAX_SIDE / Math.max(video.videoWidth, video.videoHeight));
    const canvas = document.createElement('canvas');
    canvas.width = Math.round(video.videoWidth * scale);
    canvas.height = Math.round(video.videoHeight * scale);
    canvas.getContext('2d').drawImage(video, 0, 0, canvas.width, canvas.height);
    const blob = await new Promise((resolve) => canvas.toBlob(resolve, 'image/jpeg', 0.85));
    closeWebcam();
    if (blob) await addShipmentPhotos([blob]);
  });

  $('shipmentCameraInput')?.addEventListener('change', async (e) => {
    const files = Array.from(e.target.files || []);
    e.target.value = '';
    await addShipmentPhotos(files);
  });

  async function uploadShipmentPhotos(shipmentId, blobs) {
    let ultima = null;
    let fallidas = 0;
    for (const blob of blobs) {
      const fd = new FormData();
      fd.append('file', blob, blob.type === 'image/png' ? 'factura.png' : 'factura.jpg');
      try {
        ultima = await api.request(`/inventory/shipments/${shipmentId}/photos`, { method: 'POST', body: fd });
      } catch (err) {
        fallidas += 1;
      }
    }
    return { ultima, fallidas };
  }

  async function shipmentPhotoUrl(shipmentId, photoId) {
    if (shipmentPhotoUrls.has(photoId)) return shipmentPhotoUrls.get(photoId);
    const headers = { 'X-Requested-With': 'XMLHttpRequest' };
    const deviceId = api.getDeviceId();
    if (deviceId) headers['X-Device-ID'] = deviceId;
    const res = await fetch(`${api.baseUrl}/inventory/shipments/${shipmentId}/photos/${photoId}`, { credentials: 'include', headers });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const url = URL.createObjectURL(await res.blob());
    shipmentPhotoUrls.set(photoId, url);
    return url;
  }

  // ==========================================================================
  // Cargamentos agendados y proveedores con diferencias
  // ==========================================================================
  const panamaDate = (d) => d.toLocaleDateString('en-CA', { timeZone: 'America/Panama' });   // AAAA-MM-DD
  function hora12(hhmm) {
    if (!hhmm) return '';
    const [h, m] = hhmm.split(':').map(Number);
    return `${(h % 12) || 12}:${String(m).padStart(2, '0')} ${h < 12 ? 'am' : 'pm'}`;
  }
  function expectedWhen(e) {
    const hoy = panamaDate(new Date());
    const manana = panamaDate(new Date(Date.now() + 86400000));
    const fecha = new Date(`${e.expected_date}T12:00:00`).toLocaleDateString('es-PA', { weekday: 'short', day: 'numeric', month: 'short' });
    const dia = e.expected_date === hoy ? 'Hoy' : e.expected_date === manana ? 'Mañana'
      : e.expected_date < hoy ? `Atrasado · era el ${fecha}` : fecha;
    return `${dia}${e.time_from ? ` · desde las ${hora12(e.time_from)}` : ''}`;
  }

  async function loadExpected() {
    const params = new URLSearchParams();
    if (state.branchFilter) params.set('branch_id', state.branchFilter);
    try {
      state.expected = await api.get(`/inventory/expected-shipments?${params.toString()}`);
    } catch (err) {
      state.expected = [];
    }
    renderExpected();
  }

  function renderExpected() {
    const box = $('expectedBox');
    const lista = state.expected || [];
    box.hidden = !lista.length;
    if (!lista.length) return;
    const hoy = panamaDate(new Date());
    $('expectedList').innerHTML = lista.map((e) => `
      <div class="inv-expected-card${e.expected_date < hoy ? ' is-late' : ''}${e.expected_date === hoy ? ' is-today' : ''}">
        <span class="inv-expected-icon"><i data-lucide="truck"></i></span>
        <div class="inv-expected-info">
          <strong>${esc(e.supplier_name || 'Cargamento')}</strong>
          <small>${esc(expectedWhen(e))}${state.isGlobalScope ? ` · ${esc(e.branch_name)}` : ''}</small>
          ${e.notes ? `<small class="inv-expected-note">${esc(e.notes)}</small>` : ''}
        </div>
        <div class="inv-expected-actions">
          ${hasPerm('inventory.adjust') ? `<button type="button" class="inv-expected-cancel" data-expected-cancel="${e.id}">Cancelar</button>` : ''}
          <button type="button" class="inv-primary-btn inv-expected-receive" data-expected-receive="${e.id}"><i data-lucide="package-check"></i> Recibir</button>
        </div>
      </div>`).join('');
    utils.renderIcons();
  }

  $('expectedList')?.addEventListener('click', async (e) => {
    const recibir = e.target.closest('[data-expected-receive]');
    const cancelar = e.target.closest('[data-expected-cancel]');
    if (recibir) {
      const agendado = state.expected.find((x) => x.id === Number(recibir.dataset.expectedReceive));
      if (agendado) openShipmentModal(agendado);
    } else if (cancelar) {
      const agendado = state.expected.find((x) => x.id === Number(cancelar.dataset.expectedCancel));
      if (!agendado || !window.confirm(`¿Cancelar el cargamento de ${agendado.supplier_name || 'este proveedor'} (${expectedWhen(agendado)})?`)) return;
      cancelar.disabled = true;
      cancelar.textContent = 'Cancelando…';
      try {
        await api.post(`/inventory/expected-shipments/${agendado.id}/cancel`, {});
        utils.showToast('Cargamento agendado cancelado.', 'success');
        await loadExpected();
      } catch (err) {
        utils.showToast(err.message || 'No se pudo cancelar.', 'error');
        cancelar.disabled = false;
        cancelar.textContent = 'Cancelar';
      }
    }
  });

  function openExpectedModal() {
    $('expectedError').style.display = 'none';
    const sel = $('expectedBranch');
    if (state.fixedBranchId) {
      sel.innerHTML = `<option value="${state.fixedBranchId}">${esc(state.user.branch ? state.user.branch.name : 'Mi sucursal')}</option>`;
      sel.disabled = true;
    } else {
      sel.innerHTML = (state.branches || []).map((b) => `<option value="${b.id}">${esc(b.name)}</option>`).join('');
      sel.disabled = false;
      if (state.branchFilter) sel.value = state.branchFilter;
    }
    const proveedores = [...(state.suppliers || [])].sort((a, b) => a.name.localeCompare(b.name, 'es'));
    $('expectedSupplier').innerHTML = '<option value="">Sin proveedor / otro</option>'
      + proveedores.map((s) => `<option value="${s.id}">${esc(s.name)}</option>`).join('');
    const hoy = panamaDate(new Date());
    $('expectedDate').min = hoy;
    $('expectedDate').value = panamaDate(new Date(Date.now() + 86400000));
    $('expectedTime').value = '';
    $('expectedNotes').value = '';
    openModal('modalExpected');
  }
  $('btnScheduleShipment')?.addEventListener('click', openExpectedModal);

  $('btnSaveExpected')?.addEventListener('click', async () => {
    const fecha = $('expectedDate').value;
    if (!fecha) { showModalError('expectedError', 'Elige el día.'); return; }
    const btn = $('btnSaveExpected');
    btn.disabled = true;
    btn.textContent = 'Agendando…';
    try {
      await api.post('/inventory/expected-shipments', {
        branch_id: Number($('expectedBranch').value),
        supplier_id: $('expectedSupplier').value ? Number($('expectedSupplier').value) : null,
        expected_date: fecha,
        time_from: $('expectedTime').value || null,
        notes: $('expectedNotes').value.trim() || null,
      });
      closeModal('modalExpected');
      utils.showToast('Agendado. Se le avisó a la sucursal.', 'success');
      await loadExpected();
    } catch (err) {
      showModalError('expectedError', err.message || 'No se pudo agendar.');
    } finally {
      btn.disabled = false;
      btn.textContent = 'Agendar y avisar';
    }
  });

  async function loadSupplierIssues() {
    const params = new URLSearchParams();
    if (state.branchFilter) params.set('branch_id', state.branchFilter);
    let filas = [];
    try {
      filas = await api.get(`/inventory/suppliers/issues?${params.toString()}`);
    } catch (err) {
      filas = [];
    }
    const conFallas = filas.filter((f) => f.with_issues > 0);
    $('supplierIssuesBox').hidden = !conFallas.length;
    $('supplierIssuesList').innerHTML = conFallas.map((f) => `
      <div class="inv-supplier-issue">
        <strong>${esc(f.supplier_name)}</strong>
        <span>${f.with_issues} de ${f.shipments} ${f.shipments === 1 ? 'entrega revisada' : 'entregas revisadas'} con diferencias (${Number(f.issue_pct).toLocaleString('es-PA', { maximumFractionDigits: 0 })}%)</span>
        <span class="inv-supplier-issue-claim">${Number(f.claim_value) ? `${esc(money(f.claim_value))} faltante` : ''}</span>
      </div>`).join('');
  }


  // ==========================================================================
  // Utilidades que antes vivían en la vista de Merma (la merma se registra ahora en /merma y se
  // analiza en Reportes) y que Cargamentos sigue usando: permisos, tendencia semanal, achicar la
  // foto de la factura y el visor de fotos.
  // ==========================================================================
  function hasPerm(code) {
    return (state.user?.permissions || []).includes(code);
  }

  function trendTxt(ahora, antes) {
    const a = Number(ahora);
    const b = Number(antes);
    if (!b) return '';
    const pct = Math.round(((a - b) / b) * 100);
    if (Math.abs(pct) < 5) return ', parecido a la semana anterior';
    if (pct > 300) return `, <strong>mucho más</strong> que la semana anterior (${esc(money(b))})`;
    return pct > 0 ? `, <strong>${pct}% más</strong> que la semana anterior (${esc(money(b))})` : `, ${Math.abs(pct)}% menos que la semana anterior`;
  }

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

  // Visor de fotos (las de la factura de un cargamento).
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
    if (e.key === 'Escape' && $('wastePhotoViewer') && !$('wastePhotoViewer').hidden) closePhotoViewer();
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
          : (negativos.some((r) => Number(r.sold_since_count))
              ? 'se vendió más de lo que el sistema tiene registrado desde el último conteo: falta registrar algún cargamento, o la receta de Invu pide más de lo que se usa. '
              : 'salió más de lo que el sistema tiene registrado como entrada. ')) +
        'Se arregla registrando los cargamentos que llegan, o con un conteo de lo que hay hoy: el conteo fija el punto de partida.';
    } else {
      note.hidden = true;
    }

    const table = $('stockTable');
    if (!rows.length) {
      table.innerHTML = state.loadError.stock
        ? errorStateHtml('stock')
        : q
          ? emptyStateHtml('search-x', 'Sin resultados', 'Prueba con otro insumo o categoría.')
          : emptyStateHtml('boxes', 'Todavía no hay movimientos', 'En cuanto registres un cargamento o un conteo, las existencias aparecen aquí.');
      utils.renderIcons();
      return;
    }

    // La columna de traslados solo aparece cuando hubo alguno: la mayoría de las sucursales
    // todavía no los usa y una columna llena de "—" solo le quita lugar al resto en celular.
    const hayTraslados = rows.some((r) => Number(r.transferred));
    // Lo vendido desde el último conteo (ventas de Invu × recetas): solo si algún insumo lo tiene.
    const hayVendido = rows.some((r) => Number(r.sold_since_count));
    // Lo registrado en "Registro de consumo": solo aparece si algún insumo lo tiene.
    const hayConsumo = rows.some((r) => Number(r.consumed));
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
            ${hayConsumo ? '<th class="num" title="Lo que el equipo anotó como consumido en el Registro de consumo">Consumo<small>registrado</small></th>' : ''}
            ${hayTraslados ? '<th class="num" title="Recibido de otras sucursales menos lo enviado">Traslados<small>entre sucursales</small></th>' : ''}
            ${hayVendido ? '<th class="num" title="Lo que se usó en los platos vendidos desde el último conteo, según las recetas de Invu">Vendido<small>desde el conteo</small></th>' : ''}
            <th class="num" title="Entró − salió − consumo ± ajuste ± traslados − vendido">Queda<small>hoy</small></th>
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
                  <small>${esc(r.category || 'Sin categoría')} · se cuenta en ${esc(r.unit)} · ${r.last_counted_at ? `contado el ${esc(fechaServidor(r.last_counted_at))}` : 'nunca contado'}</small>
                </td>
                <td class="num" data-label="Entró (cargamentos)">${Number(r.entered) ? cant(r.entered, r.unit) : '<span class="inv-stock-none">nada</span>'}</td>
                <td class="num" data-label="Salió por merma">${Number(r.wasted) ? cant(r.wasted, r.unit) : '—'}</td>
                <td class="num" data-label="Ajuste por conteo">${Number(r.adjusted) ? cant(r.adjusted, r.unit, true) : '—'}</td>
                ${hayConsumo ? `<td class="num" data-label="Consumo registrado">${Number(r.consumed) ? `−${cant(r.consumed, r.unit)}` : '—'}</td>` : ''}
                ${hayTraslados ? `<td class="num" data-label="Traslados">${Number(r.transferred) ? cant(r.transferred, r.unit, true) : '—'}</td>` : ''}
                ${hayVendido ? `<td class="num" data-label="Vendido desde el conteo">${Number(r.sold_since_count) ? `−${cant(r.sold_since_count, r.unit)}` : '—'}</td>` : ''}
                <td class="num inv-stock-onhand" data-label="Queda hoy"><span class="inv-stock-pill${clase}">${cant(r.on_hand, r.unit)}</span></td>
                <td class="num" data-label="Pérdida en $">${r.wasted_cost != null
                  ? `${r.wasted_cost_estimated ? '<small aria-label="estimado con el costo de Invu">≈ </small>' : ''}${money(r.wasted_cost)}`
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
      list.innerHTML = state.loadError.counts
        ? errorStateHtml('counts')
        : state.search.count
          ? emptyStateHtml('search-x', 'Sin resultados', 'Prueba con otro insumo o persona.')
          : emptyStateHtml('clipboard-check', 'Todavía no hay conteos', 'Cuenta lo que hay en el estante: el primero de cada sucursal pasa a ser su inventario de arranque.');
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
    sin_conversion: { label: 'No se pudo calcular', cls: 'norecipe', icon: 'help-circle' },
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
      if (t.no_conversion) {
        hallazgos.push(`En ${pluralize(t.no_conversion, 'insumo', 'insumos')} no se pudo calcular cuánto se usó: la receta de Invu lo pide en otra unidad (por ejemplo gramos) y el insumo se cuenta por pieza. Anota cuánto pesa una pieza (al registrar una merma, o en el insumo) y el conteo lo descuenta solo.`);
      }
      if (t.surplus) {
        hallazgos.push(`${t.surplus === 1 ? 'Sobró 1 insumo' : `Sobraron ${t.surplus} insumos`}: casi siempre es una compra que no se registró en Cargamentos.`);
      }
      if (!t.missing && !t.no_recipe && !t.no_conversion && !t.surplus && t.ok) {
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

    const revisar = a.lines.filter((l) => ['falta', 'sin_receta', 'sin_conversion', 'sobra'].includes(l.status));
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
      detail.innerHTML = emptyStateHtml('mouse-pointer-click', 'Elige un conteo', 'Lo que decía el sistema, lo que se contó y la diferencia aparecen aquí.');
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
        if (box) box.innerHTML = '<p class="inv-ca-foot">No se pudo calcular el análisis. Prueba de nuevo en un rato.</p>';
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
    state.loadError.countStock = false;
    $('countIntro').hidden = true;
    const branchId = countModalBranchId();
    countBranchPrev = $('countBranchSelect').value;
    if (!branchId) { renderCountLines(); updateCountTotal(); return; }

    $('countLines').innerHTML = skeletonListHtml(6);
    updateCountTotal();
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
      if (seq !== loadSeq.countStock) return;
      state.loadError.countStock = true;
    }
    $('countIntro').hidden = !state.countIsFirst;
    renderCountLines();
    updateCountTotal();
  }

  /** Lo anotado en un renglón, como número (acepta coma decimal: "2,5"). NaN si no es un número. */
  const countValue = (v) => (v == null || String(v).trim() === '' ? NaN : Number(String(v).trim().replace(',', '.')));

  function visibleCountRows() {
    const q = sinTildes(state.search.countItem.trim());
    return state.countStock.filter((r) => {
      // Lo que ya se anotó nunca se esconde: desaparecer de la vista algo que se contó hace
      // pensar que se perdió.
      if (state.countEntries.has(r.inventory_item_id)) return !q || sinTildes(r.item_name).includes(q);
      if (state.countOnlyStocked && !Number(r.entered) && !Number(r.wasted) && !Number(r.adjusted) && !Number(r.transferred) && !Number(r.consumed)) return false;
      return !q || sinTildes(`${r.item_name} ${r.category || ''}`).includes(q);
    });
  }

  function countDiffHtml(row) {
    const valor = state.countEntries.get(row.inventory_item_id);
    if (valor == null) return '—';
    const n = countValue(valor);
    if (Number.isNaN(n) || n < 0) return '<span class="inv-count-diff-bad">Revisa</span>';
    const dif = n - Number(row.on_hand);
    if (state.countIsFirst || Math.abs(dif) < 0.0005) return '<span class="inv-count-diff-neutral">✓</span>';
    return `<span class="inv-count-diff-neutral">${esc(signedQty(dif))}</span>`;
  }

  function renderCountLines() {
    const box = $('countLines');
    const rows = visibleCountRows();

    if (state.loadError.countStock) {
      box.innerHTML = errorStateHtml('countStock', 'No se pudo traer la lista de insumos de esta sucursal. Revisa la conexión y vuelve a intentarlo.');
      utils.renderIcons();
      return;
    }
    if (!state.countStock.length) {
      box.innerHTML = countModalBranchId()
        ? emptyStateHtml('layout-list', 'No hay insumos en el catálogo', 'Crea los insumos en la vista Insumos y vuelve a contar.')
        : emptyStateHtml('store', 'Elige la sucursal', 'Aparece la lista de insumos para contar.');
      utils.renderIcons();
      return;
    }
    if (!rows.length) {
      box.innerHTML = emptyStateHtml('search-x', 'Sin resultados', 'Prueba con otro nombre, o desmarca "Solo con movimiento".');
      utils.renderIcons();
      return;
    }

    // `type="text"` con teclado numérico (inputmode) y no `type="number"`: con number, un "2,5"
    // escrito con coma quedaba vacío sin avisar y ese insumo no se contaba.
    box.innerHTML = rows.map((r) => {
      const valor = state.countEntries.get(r.inventory_item_id);
      const sistema = Number(r.on_hand);
      const malo = valor != null && (Number.isNaN(countValue(valor)) || countValue(valor) < 0);
      return `
        <div class="inv-count-row${valor != null ? ' is-counted' : ''}${malo ? ' is-invalid' : ''}" data-item-id="${r.inventory_item_id}">
          <div class="inv-count-name">
            <strong>${esc(r.item_name)}</strong>
            <small>${esc(r.category || 'Sin categoría')} · en ${esc(r.unit)}</small>
          </div>
          <div class="inv-count-cell">
            <span class="inv-line-label">Registrado</span>
            <span class="inv-count-system${sistema < 0 ? ' is-negative' : ''}">${esc(qty(sistema))} <small>${esc(unitShort(r.unit))}</small></span>
          </div>
          <div class="inv-count-cell">
            <span class="inv-line-label">Contado</span>
            <input type="text" class="modal-input inv-count-input" inputmode="decimal" enterkeyhint="next"
                   autocomplete="off" placeholder="—" value="${valor != null ? esc(valor) : ''}"
                   aria-label="Cantidad contada de ${esc(r.item_name)}, en ${esc(r.unit)}">
          </div>
          <div class="inv-count-cell">
            <span class="inv-line-label">Diferencia</span>
            <span class="inv-count-diff">${countDiffHtml(r)}</span>
          </div>
        </div>`;
    }).join('');
    utils.renderIcons();
  }

  // Un solo juego de escuchas para toda la lista (se vuelve a dibujar al buscar o filtrar).
  const countLinesBox = $('countLines');
  // Solo se refresca la fila y el pie: volver a dibujar la lista con cada tecla le quitaría el
  // foco al campo que se está escribiendo.
  countLinesBox?.addEventListener('input', (e) => {
    const input = e.target.closest('.inv-count-input');
    if (!input) return;
    const el = input.closest('.inv-count-row');
    const itemId = Number(el.dataset.itemId);
    const row = state.countStock.find((r) => r.inventory_item_id === itemId);
    const v = input.value.trim();
    if (v === '') state.countEntries.delete(itemId);
    else state.countEntries.set(itemId, v);
    const n = countValue(v);
    el.classList.toggle('is-counted', v !== '');
    el.classList.toggle('is-invalid', v !== '' && (Number.isNaN(n) || n < 0));
    if (row) el.querySelector('.inv-count-diff').innerHTML = countDiffHtml(row);
    updateCountTotal();
  });
  // "Siguiente" (Enter) pasa al próximo insumo: contar un estante es anotar un número tras otro.
  countLinesBox?.addEventListener('keydown', (e) => {
    const input = e.target.closest('.inv-count-input');
    if (!input || e.key !== 'Enter') return;
    e.preventDefault();
    const inputs = Array.from(countLinesBox.querySelectorAll('.inv-count-input'));
    const next = inputs[inputs.indexOf(input) + 1];
    if (!next) { input.blur(); return; }
    // Centrado: arriba está la búsqueda fija y abajo el teclado; en el borde quedaba tapado.
    next.focus({ preventScroll: true });
    next.closest('.inv-count-row')?.scrollIntoView({ block: 'center' });
  });
  // Tocar cualquier parte del renglón (el nombre, lo registrado) va directo a anotar.
  countLinesBox?.addEventListener('click', (e) => {
    if (e.target.closest('input, button, a')) return;
    e.target.closest('.inv-count-row')?.querySelector('.inv-count-input')?.focus();
  });

  /** Pie del modal y avance: cuántos se contaron de cuántos hay en la lista. */
  function updateCountTotal() {
    let contados = 0;
    let valor = 0;
    let conCosto = 0;
    state.countEntries.forEach((v, itemId) => {
      const row = state.countStock.find((r) => r.inventory_item_id === itemId);
      const n = countValue(v);
      if (!row || Number.isNaN(n) || n < 0) return;
      contados += 1;
      const dif = n - Number(row.on_hand);
      if (Math.abs(dif) < 0.0005) return;
      if (row.last_unit_cost != null) {
        valor += dif * Number(row.last_unit_cost);
        conCosto += 1;
      }
    });

    const total = state.countStock.length;
    const avance = total ? `${contados} de ${total} contados` : (contados ? pluralize(contados, 'contado', 'contados') : '');
    $('countProgress').textContent = contados ? avance : 'Nada contado todavía';
    $('countProgressTop').textContent = total ? avance : 'Cargando la lista…';
    $('countProgressFill').style.width = total ? `${Math.round((contados / total) * 100)}%` : '0%';

    const totalBox = $('countTotal');
    totalBox.classList.remove('is-short', 'is-over');
    // Qué faltó y qué sobró no se puede saber aquí: falta descontar lo que se usó en los platos
    // vendidos (ventas de Invu × recetas), y eso lo calcula el servidor al guardar.
    if (state.countIsFirst && conCosto) {
      totalBox.textContent = money(Math.abs(valor));
    } else {
      totalBox.textContent = contados ? String(contados) : '—';
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
  // Enter en la búsqueda va al primer insumo encontrado: "fre" → Enter → se anota la fresa.
  $('countItemSearch')?.addEventListener('keydown', (e) => {
    if (e.key !== 'Enter') return;
    e.preventDefault();
    const first = countLinesBox?.querySelector('.inv-count-input');
    if (first) first.focus();
    else e.target.blur();
  });

  $('countOnlyStocked')?.addEventListener('change', (e) => {
    state.countOnlyStocked = e.target.checked;
    renderCountLines();
  });

  let countBranchPrev = '';
  $('countBranchSelect')?.addEventListener('change', (e) => {
    // Cambiar de sucursal a mitad del conteo descarta lo anotado: los números eran de otro
    // estante, y mandarlos a esta sucursal sería peor que perderlos. Por eso se pregunta antes.
    if (state.countEntries.size && !window.confirm('Al cambiar de sucursal se borra lo que ya anotaste en este conteo. ¿Cambiar igual?')) {
      e.target.value = countBranchPrev;
      return;
    }
    loadCountStock();
  });

  // ---- Cambios sin guardar ----
  let countSaving = false;
  const countIsDirty = () => $('modalCount').classList.contains('active')
    && (state.countEntries.size > 0 || $('countNotes').value.trim() !== '');

  modalCanClose.modalCount = () => {
    if (countSaving) return false;
    if (!countIsDirty()) return true;
    return window.confirm(`¿Salir sin guardar? Se pierde lo que anotaste (${pluralize(state.countEntries.size, 'insumo', 'insumos')}).`);
  };
  modalAfterClose.modalCount = () => { if (FROM_OPERACION) volverAOperacion(); };
  modalAfterClose.modalCountResult = () => { if (FROM_OPERACION) volverAOperacion(); };

  // Cerrar la pestaña o recargar con un formulario a medio llenar: el navegador pregunta.
  window.addEventListener('beforeunload', (e) => {
    if (shipmentIsDirty() || countIsDirty()) {
      e.preventDefault();
      e.returnValue = '';
    }
  });

  /** Devuelve la carga de la lista: desde Operación, lo demás de la página se pide después. */
  function openCountModal() {
    $('countError').style.display = 'none';
    $('countNotes').value = '';
    $('countItemSearch').value = '';
    state.search.countItem = '';
    $('countOnlyStocked').checked = state.countOnlyStocked;
    $('countTotal').textContent = '—';
    $('countProgress').textContent = 'Nada contado todavía';
    openModal('modalCount');
    return loadCountStock();
  }

  $('btnSubmitCount')?.addEventListener('click', async () => {
    $('countError').style.display = 'none';

    const branchId = countModalBranchId();
    if (!branchId) { showModalError('countError', 'Elige una sucursal.'); return; }

    const items = [];
    for (const [itemId, v] of state.countEntries) {
      const cantidad = countValue(v);
      if (Number.isNaN(cantidad) || cantidad < 0) {
        const nombre = state.countStock.find((r) => r.inventory_item_id === itemId)?.item_name || 'un insumo';
        showModalError('countError', `La cantidad de "${nombre}" no es un número válido. Corrígela o déjala vacía.`);
        // Que se vea y se pueda corregir aunque esté filtrado o más abajo.
        const fila = countLinesBox?.querySelector(`.inv-count-row[data-item-id="${itemId}"]`);
        fila?.scrollIntoView({ block: 'center' });
        fila?.querySelector('.inv-count-input')?.focus({ preventScroll: true });
        return;
      }
      items.push({ inventory_item_id: itemId, counted_quantity: String(cantidad) });
    }
    if (!items.length) { showModalError('countError', 'Anota la cantidad de al menos un insumo.'); return; }

    const btn = $('btnSubmitCount');
    countSaving = true;
    setFormSaving('modalCount', btn, true, 'Guardando…');
    try {
      const creado = await api.post('/inventory/counts', {
        branch_id: branchId,
        notes: $('countNotes').value.trim() || null,
        items,
      });
      countSaving = false;
      closeModal('modalCount');
      showCountResult(creado);

      // Desde Operación se vuelve allá al tocar "Listo": no hace falta refrescar Inventario.
      if (FROM_OPERACION) return;
      state.selected.count = creado.id;
      invalidateDashboard();
      await Promise.all([loadCounts({ reset: true }), loadStock()]);
      setView('conteo');
    } catch (err) {
      showModalError('countError', err.message || 'No se pudo guardar el conteo. Revisa la conexión e inténtalo de nuevo.');
    } finally {
      countSaving = false;
      setFormSaving('modalCount', btn, false, 'Guardar conteo');
    }
  });

  // ==========================================================================
  // Contexto de sucursal
  // ==========================================================================
  async function resolveBranchContext(user) {
    const badge = $('branchFixedBadge');
    const select = $('branchSelect');
    const filter = $('shipmentBranchFilter');

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
      stockFilter.hidden = true;
      if (dashFilter) dashFilter.hidden = true;
      countBadge.hidden = false;
      countBadge.textContent = branchName;
      countSelect.hidden = true;
      countFilter.hidden = true;
      $('invScopeValue').textContent = branchName;
      $('invHeaderScope').textContent = `Lo que llega, lo que hay y los conteos de ${branchName}`;
      return;
    }

    state.isGlobalScope = true;
    badge.hidden = true;
    select.hidden = false;
    countBadge.hidden = true;
    countSelect.hidden = false;
    $('invScopeValue').textContent = 'Todas las sucursales';
    $('invHeaderScope').textContent = 'Lo que llega, lo que hay y los conteos de todas las sucursales';

    try {
      state.branches = await api.get('/branches/');
      const options = state.branches.map((b) => `<option value="${b.id}">${esc(b.name)}</option>`).join('');
      select.innerHTML = options;
      filter.innerHTML = `<option value="">Todas las sucursales</option>${options}`;
      filter.hidden = false;
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
  $('countList').innerHTML = skeletonListHtml(3);

  // La vista de tablet (/operacion) linkea aquí con ?open=shipment|count en vez de reconstruir
  // esos formularios — un botón grande que abre el modal de siempre. La merma ya no vive aquí: se
  // registra en /merma y se analiza en Reportes; los enlaces viejos van allá (arriba de todo).
  const urlParams = new URLSearchParams(window.location.search);
  const openParam = urlParams.get('open');
  const viewParam = urlParams.get('view');
  const AUTO_OPEN = {
    shipment: () => { setView('cargamentos'); openShipmentModal(); },
    count: () => { setView('conteo'); return openCountModal(); },
  };
  const autoOpen = AUTO_OPEN[openParam];

  await resolveBranchContext(existingUser);
  $('btnScheduleShipment').hidden = !hasPerm('inventory.adjust');

  // Con ?open=..., el formulario se abre apenas tiene lo que necesita (la sucursal, y para recibir
  // también el catálogo y si Invu manda en los proveedores), sin esperar el tablero, el historial
  // ni las existencias: eso se pide después, por detrás.
  const yaCargado = new Set();
  if (autoOpen) {
    if (openParam === 'shipment') {
      await Promise.all([loadCatalogs(), loadInvuStatus()]);
      yaCargado.add('catalogs').add('invu');
    }
    const listo = autoOpen();
    // Desde Operación, lo de atrás casi nunca se mira: primero la lista del conteo.
    if (FROM_OPERACION && listo) await listo;
  } else {
    // La vista se muestra ya, con sus esqueletos; las cifras llegan a medida que responden.
    setView(VIEWS[viewParam] ? viewParam : 'resumen');
  }

  const resto = [
    loadShipments({ reset: true }),
    loadAnalytics(),
    loadStock(),
    loadCounts({ reset: true }),
    loadExpected(),
    loadSupplierIssues(),
  ];
  if (!yaCargado.has('catalogs')) resto.push(loadCatalogs());
  if (!yaCargado.has('invu')) resto.push(loadInvuStatus());
  const todoCargado = Promise.all(resto).then(() => {
    renderResumen();
    renderItemList();
    renderSupplierList();
    utils.renderIcons();
  });

  if (autoOpen) return;
  await todoCargado;

  // Desde una notificación: ?view=cargamentos&shipment=ID abre ese cargamento.
  const shipmentParam = Number(urlParams.get('shipment'));
  if (viewParam === 'cargamentos' && shipmentParam && state.shipments.some((s) => s.id === shipmentParam)) {
    state.selected.shipment = shipmentParam;
    renderShipmentList();
    openDetailOnMobile($('shipmentList'));
  }
});
