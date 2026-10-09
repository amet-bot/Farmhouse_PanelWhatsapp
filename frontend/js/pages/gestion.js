/**
 * Farmhouse Link — Centro de operación
 *
 * Una sola pantalla con lo pendiente de todas las sucursales (o de la propia, para un
 * supervisor local): incidencias, tareas, solicitudes de insumos, traslados y cargamentos
 * esperados, con sus acciones. Es la pantalla de quien maneja varias sucursales sin estar en
 * ninguna: encargado de logística, gerencia. Los agentes reportan desde la tablet (/operacion);
 * esta pantalla es para encargados (permiso purchasing.approve).
 *
 * Pensada para tablet con el dedo: las tablas pasan a tarjetas en pantallas angostas (cada celda
 * lleva su data-label), lo que mueve inventario (despachar, recibir) pide confirmación mostrando
 * qué se mueve, y los motivos se escriben en un modal propio en vez de prompt().
 */
document.addEventListener('DOMContentLoaded', async () => {
  const $ = (id) => document.getElementById(id);
  const esc = (s) => utils.escapeHtml(s ?? '');

  FarmhouseShell.initTheme();
  FarmhouseShell.initLogout({ redirectTo: '/' });
  window.addEventListener('auth:unauthorized', () => { window.location.href = '/'; });

  const user = await auth.checkSession();
  if (!user) { window.location.href = '/'; return; }
  const perms = user.permissions || [];
  if (!perms.includes('purchasing.approve')) {
    utils.showToast('El Centro de operación es para encargados.', 'info');
    window.location.href = '/operacion';
    return;
  }
  const isGlobal = user.role === 'admin' || (user.role === 'supervisor' && !user.branch_id);
  const canAdjust = perms.includes('inventory.adjust');

  $('opsGate').hidden = true;
  $('opsMain').hidden = false;
  FarmhouseShell.fillUserHeader({ nameId: 'opsAgentName', roleId: 'opsAgentRole', avatarId: 'opsAgentAvatar' }, user);

  const params = new URLSearchParams(window.location.search);
  const state = {
    branches: [],
    branch: isGlobal ? (params.get('branch') || '') : String(user.branch_id),
    tab: params.get('tab') || 'resumen',
    team: {},           // branch_id -> [{id, name, role}]
    transferItems: [],  // líneas del traslado nuevo
    rows: { trf: new Map(), req: new Map(), exp: new Map(), task: new Map() },   // lo último pintado, para las confirmaciones
    refreshTimer: null,
  };
  const TABS = ['resumen', 'incidencias', 'tareas', 'solicitudes', 'traslados', 'cargamentos', 'actividad'];
  const VIEW = { resumen: 'viewResumen', incidencias: 'viewIncidencias', tareas: 'viewTareas', solicitudes: 'viewSolicitudes', traslados: 'viewTraslados', cargamentos: 'viewCargamentos', actividad: 'viewActividad' };

  // ==========================================================================
  // Utilidades
  // ==========================================================================
  const bq = (prefix = '?') => (state.branch ? `${prefix}branch_id=${state.branch}` : '');
  const branchName = (id) => (state.branches.find((b) => b.id === Number(id)) || {}).name || `Sucursal #${id}`;
  const todayPanama = () => new Date().toLocaleDateString('en-CA', { timeZone: 'America/Panama' });
  const fmt = (iso) => (iso ? utils.formatDateTime(iso) : '—');
  const numFmt = new Intl.NumberFormat('es-PA', { maximumFractionDigits: 3 });
  const num = (n) => numFmt.format(Number(n) || 0);
  const plural = (n, uno, varios) => `${n} ${n === 1 ? uno : varios}`;
  // "2026-10-03" -> "vie, 3 oct" (como el resto de la página, sin el año).
  const fechaDia = (ymd) => {
    if (!ymd) return '—';
    const [y, m, d] = String(ymd).slice(0, 10).split('-').map(Number);
    if (!y || !m || !d) return String(ymd);
    return new Date(y, m - 1, d).toLocaleDateString('es-PA', { weekday: 'short', day: 'numeric', month: 'short' });
  };
  // "07:30" -> "7:30 am"
  const hora12 = (t) => {
    if (!t) return '';
    const [h, m] = String(t).split(':').map(Number);
    if (Number.isNaN(h)) return String(t);
    return `${(h % 12) || 12}:${String(m || 0).padStart(2, '0')} ${h < 12 ? 'am' : 'pm'}`;
  };
  const ago = (iso) => {
    if (!iso) return '';
    const mins = Math.max(0, Math.round((Date.now() - utils._parseServerDate(iso)) / 60000));
    if (mins < 60) return `hace ${mins} min`;
    if (mins < 60 * 24) return `hace ${Math.round(mins / 60)} h`;
    return `hace ${Math.round(mins / 1440)} d`;
  };
  const SEV_LABEL = { alta: 'Alta', media: 'Media', baja: 'Baja' };
  const STATUS_LABEL = {
    open: 'Sin aprobar', approved: 'Aprobada', fulfilled: 'Entregada', cancelled: 'Cancelada',
    requested: 'Por aprobar', dispatched: 'En camino', received: 'Recibido', rejected: 'Rechazado',
    abierta: 'Abierta', en_proceso: 'En proceso', resuelta: 'Resuelta',
    pendiente: 'Pendiente', hecha: 'Hecha', cancelada: 'Cancelada',
    active: 'Activo', disabled: 'Deshabilitado', revoked: 'Revocado',
  };
  const ROLE_LABEL = { agent: 'Empleado', supervisor: 'Encargado', admin: 'Administrador', logistica: 'Gerente de logística', rrhh: 'Recursos Humanos' };
  const chip = (cls, text) => `<span class="ops-chip ${cls}">${esc(text)}</span>`;
  const empty = (text) => `<div class="ops-empty">${esc(text)}</div>`;
  const loading = () => '<div class="ops-loading">Cargando…</div>';
  const actionsCell = (html) => `<td class="ops-td-actions${html.trim() ? '' : ' is-empty'}"><div class="ops-actions">${html}</div></td>`;
  const setCount = (id, n) => { const el = $(id); if (!el) return; el.hidden = !n; el.textContent = n; };
  const openModal = (id) => { $(id).classList.add('active'); utils.renderIcons(); };
  function closeModal(id) {
    $(id).classList.remove('active');
    if (id === 'modalConfirm' && pendingConfirm) { const r = pendingConfirm; pendingConfirm = null; r(null); }
  }
  document.querySelectorAll('[data-close]').forEach((b) => b.addEventListener('click', () => closeModal(b.dataset.close)));
  // Escape cierra el modal de arriba (laptop); tocar fuera solo cierra la confirmación, que no
  // tiene nada escrito que perder.
  document.addEventListener('keydown', (e) => {
    if (e.key !== 'Escape') return;
    const abiertos = [...document.querySelectorAll('.modal-backdrop.active')];
    if (abiertos.length) closeModal(abiertos[abiertos.length - 1].id);
  });
  $('modalConfirm').addEventListener('click', (e) => { if (e.target === $('modalConfirm')) closeModal('modalConfirm'); });

  // ---- Confirmación en la página (reemplaza confirm() y prompt()) ----
  let pendingConfirm = null;
  /**
   * Abre el modal de confirmación. Devuelve una promesa: null si se cerró sin confirmar, o
   * { reason } (texto del motivo, si se pidió) si se confirmó.
   */
  function confirmar({ title, subtitle = '', html = '', okText = 'Confirmar', danger = false, reason = null }) {
    if (pendingConfirm) { const r = pendingConfirm; pendingConfirm = null; r(null); }
    $('confirmTitle').textContent = title;
    $('confirmSubtitle').textContent = subtitle;
    $('confirmBody').innerHTML = html + (reason
      ? `<label class="modal-label" for="confirmReason">${esc(reason.label)}</label>
         <textarea class="modal-input" id="confirmReason" rows="3" maxlength="300" placeholder="${esc(reason.placeholder || '')}"></textarea>`
      : '');
    const ok = $('confirmOk');
    ok.textContent = okText;
    ok.classList.toggle('is-danger', danger);
    openModal('modalConfirm');
    return new Promise((resolve) => { pendingConfirm = resolve; });
  }
  $('confirmForm').addEventListener('submit', (e) => {
    e.preventDefault();
    const r = pendingConfirm;
    pendingConfirm = null;
    const reasonEl = $('confirmReason');
    $('modalConfirm').classList.remove('active');
    if (r) r({ reason: reasonEl ? reasonEl.value.trim() : '' });
  });
  const itemsList = (items) => `<ul class="ops-confirm-list">${items.map((i) => `<li><span>${esc(i.name)}</span><b>${esc(i.qty)}</b></li>`).join('')}</ul>`;

  // Bloquea el botón mientras corre la acción; en los botones de guardar muestra "Guardando…".
  async function withBusy(btn, fn, busyText) {
    if (!btn) { await fn(); return; }
    if (btn.disabled) return;
    const prev = btn.innerHTML;
    btn.disabled = true;
    btn.setAttribute('aria-busy', 'true');
    if (busyText && btn.tagName === 'BUTTON') btn.textContent = busyText;
    try { await fn(); } finally {
      btn.disabled = false;
      btn.removeAttribute('aria-busy');
      if (busyText && btn.tagName === 'BUTTON') { btn.innerHTML = prev; utils.renderIcons(); }
    }
  }
  async function act(btn, fn, okMsg, busyText) {
    await withBusy(btn, async () => {
      try {
        await fn();
        if (okMsg) utils.showToast(okMsg, 'success');
        await loadTab(state.tab);
        loadOverviewCounts();
      } catch (err) {
        utils.showToast(err.message || 'No se pudo completar la acción.', 'error');
      }
    }, busyText);
  }
  const submitterOf = (e) => e.submitter || e.target.querySelector('[type="submit"]');
  async function team(branchId) {
    if (!state.team[branchId]) {
      try { state.team[branchId] = await api.get(`/ops/team?branch_id=${branchId}`); } catch (e) { state.team[branchId] = []; }
    }
    return state.team[branchId];
  }
  function teamOptions(members, selectedId) {
    return `<option value="">Sin asignar</option>` + members.map((m) => `<option value="${m.id}" ${m.id === selectedId ? 'selected' : ''}>${esc(m.name)}${m.branch_id ? '' : ' (todas las sucursales)'}</option>`).join('');
  }
  function branchOptions(selectedId, { includeAll = false } = {}) {
    const rows = state.branches.filter((b) => b.active !== false && b.code !== 'CAT');
    return (includeAll ? `<option value="">Todas las sucursales</option>` : '') +
      rows.map((b) => `<option value="${b.id}" ${String(b.id) === String(selectedId) ? 'selected' : ''}>${esc(b.name)}</option>`).join('');
  }
  function showFormError(id, msg) { $(id).textContent = msg; $(id).hidden = false; }

  // ==========================================================================
  // Sucursales y pestañas
  // ==========================================================================
  try { state.branches = await api.get('/branches/'); } catch (e) { state.branches = []; }
  const sel = $('branchSelect');
  sel.innerHTML = branchOptions(state.branch, { includeAll: isGlobal });
  sel.disabled = !isGlobal;
  sel.addEventListener('change', () => { state.branch = sel.value; syncUrl(); updateScope(); loadTab(state.tab); loadOverviewCounts(); });
  function updateScope() {
    $('opsHeaderScope').textContent = state.branch ? branchName(state.branch) : 'Todas las sucursales';
  }
  function syncUrl() {
    const p = new URLSearchParams();
    if (state.tab !== 'resumen') p.set('tab', state.tab);
    if (state.branch) p.set('branch', state.branch);
    const qs = p.toString();
    history.replaceState(null, '', qs ? `?${qs}` : window.location.pathname);
  }
  // En tablet de pie y celular la fila de pestañas se desliza de lado: que la activa quede a la
  // vista (también al llegar con ?tab=...). Se repite cuando aparecen los íconos y contadores.
  function revealActiveTab() {
    const fila = $('opsTabs');
    const activa = fila.querySelector('.ops-tab.active');
    if (!activa || fila.scrollWidth <= fila.clientWidth) return;
    const a = activa.getBoundingClientRect(), f = fila.getBoundingClientRect();
    if (a.left < f.left || a.right > f.right) fila.scrollLeft += a.left - f.left - 12;
  }
  function setTab(tab) {
    if (!TABS.includes(tab)) tab = 'resumen';
    state.tab = tab;
    document.querySelectorAll('.ops-tab').forEach((b) => {
      const on = b.dataset.tab === tab;
      b.classList.toggle('active', on);
      b.setAttribute('aria-current', on ? 'page' : 'false');
    });
    TABS.forEach((t) => { $(VIEW[t]).hidden = t !== tab; });
    revealActiveTab();
    syncUrl();
    loadTab(tab);
  }
  $('opsTabs').addEventListener('click', (e) => { const b = e.target.closest('.ops-tab'); if (b) setTab(b.dataset.tab); });
  $('btnRefresh').addEventListener('click', () => {
    withBusy($('btnRefresh'), async () => { await loadTab(state.tab); loadOverviewCounts(); });
  });

  async function loadTab(tab) {
    const loaders = { resumen: loadResumen, incidencias: loadIncidencias, tareas: loadTareas, solicitudes: loadSolicitudes, traslados: loadTraslados, cargamentos: loadCargamentos, actividad: () => loadActividad(false) };
    await loaders[tab]();
    utils.renderIcons();
  }

  // Abre una foto protegida (tarea) en otra pestaña: se baja con la sesión, porque un enlace
  // directo no lleva el dispositivo que el servidor exige a encargados y agentes.
  async function openProtectedPhoto(url) {
    const w = window.open('', '_blank');
    try {
      const headers = { 'X-Requested-With': 'XMLHttpRequest' };
      const dev = api.getDeviceId();
      if (dev) headers['X-Device-ID'] = dev;
      const res = await fetch(`${api.baseUrl}${url}`, { credentials: 'include', headers });
      if (!res.ok) throw new Error(`Error ${res.status}`);
      const blobUrl = URL.createObjectURL(await res.blob());
      if (w) w.location.href = blobUrl; else window.location.href = blobUrl;
    } catch (err) {
      if (w) w.close();
      utils.showToast('No se pudo abrir la foto.', 'error');
    }
  }

  // ==========================================================================
  // Actividad: quién hizo qué, cuándo y en qué sucursal (auditoría)
  // ==========================================================================
  const ACT_LABELS = {
    'task.create': 'Creó una tarea', 'task.update': 'Editó una tarea', 'task.hecha': 'Marcó una tarea hecha',
    'task.en_proceso': 'Empezó una tarea', 'task.cancelada': 'Canceló una tarea', 'task.pendiente': 'Reabrió una tarea',
    'task.remind': 'Recordó una tarea', 'task.delete': 'Eliminó una tarea', 'task.photo_add': 'Subió la foto de una tarea',
    'closing_sheet.create': 'Cerró turno', 'closing_sheet.config': 'Armó la hoja de cierre',
    'count.create': 'Hizo un conteo', 'waste.create': 'Registró merma', 'waste.delete': 'Borró una merma',
    'consumption.create': 'Anotó consumo', 'consumption.delete': 'Borró un consumo',
    'shipment.create': 'Recibió mercancía (cargamento)', 'shipment.delete': 'Borró un cargamento', 'shipment.photo_add': 'Agregó foto a un cargamento',
    'expected_shipment.create': 'Programó un cargamento', 'expected_shipment.cancel': 'Canceló un cargamento programado',
    'purchase_order.create': 'Creó una orden de compra', 'supply_setting.save': 'Cambió mínimos o cantidad ideal',
    'supply_request.create': 'Pidió insumos', 'supply_request.approved': 'Aprobó una solicitud', 'supply_request.fulfilled': 'Marcó una solicitud entregada',
    'supply_request.cancelled': 'Canceló una solicitud', 'supply_request.open': 'Reabrió una solicitud',
    'transfer.create': 'Pidió un traslado', 'transfer.request': 'Pidió un traslado', 'transfer.approve': 'Aprobó un traslado', 'transfer.dispatch': 'Despachó un traslado',
    'transfer.receive': 'Recibió un traslado', 'transfer.reject': 'Rechazó un traslado', 'transfer.cancel': 'Canceló un traslado',
    'incident.create': 'Reportó una incidencia', 'incident.assign': 'Asignó una incidencia', 'incident.resuelta': 'Resolvió una incidencia',
    'incident.en_proceso': 'Tomó una incidencia', 'incident.abierta': 'Reabrió una incidencia',
    'waste.photo_add': 'Agregó foto a una merma', 'prep_check.create': 'Llenó la preparación del día', 'prep_check.update': 'Corrigió la preparación del día',
    'recurring_task.create': 'Creó una tarea recurrente', 'recurring_task.fire': 'Se creó sola una tarea recurrente',
    'item.piece_size': 'Cambió el tamaño de pieza', 'item.photo': 'Cambió la foto de un insumo', 'item.photo_delete': 'Quitó la foto de un insumo',
    'item.grams_per_ml': 'Cambió los gramos por ml de un insumo',
    'recipes.import': 'Importó recetas', 'recipes.ingredient_map': 'Emparejó un ingrediente', 'recipes.dish_link': 'Enlazó un plato con su receta',
    'user.create': 'Creó un usuario', 'user.update': 'Editó un usuario',
    'user.delete': 'Eliminó un usuario', 'user.toggle_active': 'Activó o desactivó un usuario',
    'backup.create': 'Hizo un respaldo', 'backup.download': 'Bajó un respaldo',
    'alert.low_stock': 'Aviso de stock bajo', 'digest.weekly': 'Resumen semanal', 'digest.daily': 'Resumen diario',
    'task.overdue_alert': 'Aviso: tarea vencida', 'alert.closing_missing': 'Aviso: faltó el cierre de turno',
    'recurring_task.update': 'Editó una tarea recurrente', 'recurring_task.delete': 'Eliminó una tarea recurrente',
    'prep_template.create': 'Creó una plantilla de preparación', 'prep_template.update': 'Editó una plantilla de preparación', 'prep_template.delete': 'Eliminó una plantilla de preparación',
  };
  // Para acciones nuevas que todavía no están en la lista: "Canceló un traslado" en vez de "transfer cancel".
  const ACT_ENTITY = {
    task: 'una tarea', recurring_task: 'una tarea recurrente', transfer: 'un traslado', incident: 'una incidencia',
    supply_request: 'una solicitud', shipment: 'un cargamento', expected_shipment: 'un cargamento programado',
    waste: 'una merma', consumption: 'un consumo', count: 'un conteo', item: 'un insumo', user: 'un usuario',
    backup: 'un respaldo', prep_template: 'una plantilla de preparación', prep_check: 'la preparación del día',
    closing_sheet: 'la hoja de cierre', purchase_order: 'una orden de compra', supply_setting: 'los mínimos',
    recipes: 'las recetas', alert: 'un aviso', digest: 'un resumen', branch: 'una sucursal', device: 'un dispositivo',
  };
  const ACT_VERB = {
    create: 'Creó', update: 'Editó', delete: 'Eliminó', cancel: 'Canceló', approve: 'Aprobó', reject: 'Rechazó',
    dispatch: 'Despachó', receive: 'Recibió', request: 'Pidió', assign: 'Asignó', save: 'Guardó', remind: 'Recordó',
    photo_add: 'Agregó foto a', photo: 'Cambió la foto de', photo_delete: 'Quitó la foto de', import: 'Importó',
    toggle_active: 'Activó o desactivó', download: 'Bajó',
  };
  function actLabel(a) {
    if (ACT_LABELS[a]) return ACT_LABELS[a];
    const [ent, verb] = String(a || '').split('.');
    if (ACT_VERB[verb] && ACT_ENTITY[ent]) return `${ACT_VERB[verb]} ${ACT_ENTITY[ent]}`;
    if (STATUS_LABEL[verb] && ACT_ENTITY[ent]) return `Cambió ${ACT_ENTITY[ent]} a «${STATUS_LABEL[verb]}»`;
    if (ACT_ENTITY[ent]) return `Cambio en ${ACT_ENTITY[ent]}`;
    return 'Otra acción del sistema';
  }
  // Valores del detalle en palabras: nada de "approved", "agent" ni "en_proceso".
  const plainValue = (v) => {
    const s = String(v);
    if (STATUS_LABEL[s]) return STATUS_LABEL[s];
    if (/^[a-z]+(_[a-z]+)+$/.test(s)) return s.replace(/_/g, ' ');
    return s;
  };
  function actDetail(e) {
    const m = e.metadata || {};
    const partes = [];
    for (const k of ['title', 'name', 'item_name', 'dish_name', 'dish', 'reason']) if (m[k] != null && m[k] !== '') partes.push(String(m[k]));
    if (m.username) partes.push(`@${m.username}`);
    if (m.role) partes.push(ROLE_LABEL[m.role] || plainValue(m.role));
    if (m.severity) partes.push(`severidad ${(SEV_LABEL[m.severity] || plainValue(m.severity)).toLowerCase()}`);
    if (m.status != null && m.status !== '') partes.push(`ahora: ${plainValue(m.status)}`);
    if (m.from != null && m.from !== '') partes.push(`antes: ${plainValue(m.from)}`);
    if (typeof m.active === 'boolean') partes.push(m.active ? 'quedó activo' : 'quedó inactivo');
    if (m.items != null) partes.push(plural(Number(m.items), 'insumo', 'insumos'));
    if (m.saved != null) partes.push(`${plural(Number(m.saved), 'insumo guardado', 'insumos guardados')}`);
    if (m.total_qty != null) partes.push(`total ${num(m.total_qty)}`);
    return partes.slice(0, 3).join(' · ');
  }
  const ACT_LINKS = {
    task: () => '/gestion?tab=tareas', recurring_task: () => '/gestion?tab=tareas', stock_count: () => '/inventario?view=conteo', waste: (id) => `/merma?waste=${id}`,
    shipment: () => '/inventario?view=cargamentos', transfer: () => '/gestion?tab=traslados', incident: () => '/gestion?tab=incidencias',
    supply_request: () => '/gestion?tab=solicitudes', expected_shipment: () => '/abastecimiento?tab=ordenes',
  };
  const actState = { offset: 0, rows: [], actors: new Map() };
  (function initActDates() {
    const hoy = new Date();
    const hace7 = new Date(hoy); hace7.setDate(hoy.getDate() - 7);
    const iso = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
    $('actFrom').value = iso(hace7);
    $('actTo').value = iso(hoy);
    if (user.role !== 'admin') document.querySelectorAll('#actGroup option[data-admin]').forEach((o) => o.remove());
  })();
  async function loadActividad(append) {
    const box = $('actList');
    if (!append) { actState.offset = 0; actState.rows = []; box.innerHTML = loading(); }
    if ($('actFrom').value && $('actTo').value && $('actFrom').value > $('actTo').value) {
      box.innerHTML = empty('La fecha "Desde" es posterior a "Hasta". Cambia una de las dos.');
      $('btnActMore').hidden = true;
      return;
    }
    const p = new URLSearchParams({ limit: '100', offset: String(actState.offset) });
    if (state.branch) p.set('branch_id', state.branch);
    if ($('actGroup').value) p.set('group', $('actGroup').value);
    if ($('actActor').value) p.set('actor_user_id', $('actActor').value);
    if ($('actFrom').value) p.set('date_from', $('actFrom').value);
    if ($('actTo').value) p.set('date_to', $('actTo').value);
    let data;
    try { data = await api.get(`/system/audit?${p}`); } catch (err) { box.innerHTML = empty(err.message || 'No se pudo cargar la actividad.'); return; }
    actState.rows = actState.rows.concat(data.events);
    actState.offset += data.events.length;
    data.events.forEach((e) => { if (e.actor) actState.actors.set(e.actor.id, e.actor.name); });
    const actual = $('actActor').value;
    $('actActor').innerHTML = '<option value="">Todas las personas</option>' + [...actState.actors.entries()].sort((a, b) => a[1].localeCompare(b[1])).map(([id, n]) => `<option value="${id}">${esc(n)}</option>`).join('');
    $('actActor').value = actual;
    $('btnActMore').hidden = !data.has_more;
    if (!actState.rows.length) { box.innerHTML = empty('No hay actividad con ese filtro.'); return; }
    box.innerHTML = `<table class="ops-table ops-cards"><thead><tr><th>Cuándo</th><th>Quién</th><th>Sucursal</th><th>Qué hizo</th><th>Detalle</th></tr></thead><tbody>${actState.rows.map((e) => {
      const link = ACT_LINKS[e.entity_type] ? ACT_LINKS[e.entity_type](e.entity_id) : null;
      const detalle = actDetail(e);
      return `<tr>
        <td class="ops-td-main"><span class="ops-title">${esc(fmt(e.created_at))}</span><span class="ops-sub">${esc(ago(e.created_at))}</span></td>
        <td data-label="Quién">${esc(e.actor ? e.actor.name : 'Sistema (automático)')}</td>
        <td data-label="Sucursal">${esc(e.branch ? e.branch.name : '—')}</td>
        <td data-label="Qué hizo">${link ? `<a class="ops-link" href="${link}">${esc(actLabel(e.action))} →</a>` : esc(actLabel(e.action))}</td>
        <td data-label="Detalle" class="ops-td-wide"><span class="ops-sub">${esc(detalle) || '—'}</span></td>
      </tr>`;
    }).join('')}</tbody></table>`;
  }
  ['actGroup', 'actActor', 'actFrom', 'actTo'].forEach((id) => $(id).addEventListener('change', () => loadActividad(false)));
  $('btnActMore').addEventListener('click', () => withBusy($('btnActMore'), () => loadActividad(true), 'Cargando…'));

  // ==========================================================================
  // Resumen
  // ==========================================================================
  async function fetchOverview() { return api.get(`/ops/overview${bq()}`); }

  async function loadOverviewCounts() {
    try {
      const o = await fetchOverview();
      const t = o.totals;
      setCount('countIncidencias', t.incidents_open);
      setCount('countTareas', t.tasks_pending);
      setCount('countSolicitudes', t.requests_open + t.requests_approved);
      setCount('countTraslados', t.transfers_to_approve + t.transfers_to_dispatch + t.transfers_to_receive);
      setCount('countCargamentos', t.expected_today + t.expected_overdue);
      revealActiveTab();
    } catch (e) { /* los contadores son un extra */ }
  }

  async function loadResumen() {
    $('opsBranchGrid').innerHTML = loading();
    let o;
    try { o = await fetchOverview(); } catch (err) { $('opsBranchGrid').innerHTML = empty('No se pudo cargar el resumen. Toca "Actualizar" para reintentar.'); return; }
    const t = o.totals;
    // Cada total lleva a su pestaña; "chats sin asignar" lleva al Centro WhatsApp.
    const total = (label, n, tone, tab) => `<button type="button" class="ops-total ${n > 0 ? tone : ''}" data-tab="${tab}"><b>${n}</b> ${esc(label)}</button>`;
    $('opsTotals').innerHTML = [
      total(n1(t.incidents_open, 'incidencia abierta', 'incidencias abiertas'), t.incidents_open, t.incidents_alta ? 'bad' : 'warn', 'incidencias'),
      total(n1(t.tasks_overdue, 'tarea vencida', 'tareas vencidas'), t.tasks_overdue, 'bad', 'tareas'),
      total(n1(t.requests_open, 'solicitud sin aprobar', 'solicitudes sin aprobar'), t.requests_open, 'warn', 'solicitudes'),
      total('traslados por mover', t.transfers_to_approve + t.transfers_to_dispatch + t.transfers_to_receive, 'warn', 'traslados'),
      total(n1(t.expected_overdue, 'cargamento atrasado', 'cargamentos atrasados'), t.expected_overdue, 'bad', 'cargamentos'),
      `<a class="ops-total ops-row-link ${t.unassigned_conversations > 0 ? 'warn' : ''}" href="/app"><b>${t.unassigned_conversations}</b> chats sin asignar <span class="ops-row-go">Ver chats →</span></a>`,
    ].join('');

    const row = (tab, label, n, tone) => `<button type="button" class="ops-row ${n ? tone : 'zero'}" data-tab="${tab}"><span>${esc(label)}</span><b>${n}</b></button>`;
    $('opsBranchGrid').innerHTML = o.branches.length ? o.branches.map((b) => {
      const cls = b.attention >= 6 ? 'attn-high' : (b.attention >= 2 ? 'attn-mid' : '');
      const conteo = b.days_since_count == null ? 'Sin conteo todavía' : (b.days_since_count === 0 ? 'Conteo hoy' : `Último conteo hace ${plural(b.days_since_count, 'día', 'días')}`);
      const prep = b.prep_checkpoints_today ? `Preparación del día: ${b.prep_filled_today} de ${b.prep_checkpoints_today}` : 'Sin plantilla de preparación';
      // En palabras, no en "puntos": cuántas cosas esperan a alguien.
      const pendientes = b.incidents_open + b.tasks_overdue + b.expected_overdue + b.transfers_to_approve + b.transfers_to_receive + b.requests_open;
      const badge = !b.attention ? 'Al día'
        : `${cls === 'attn-high' ? 'Urgente · ' : ''}${pendientes ? plural(pendientes, 'pendiente', 'pendientes') : 'Revisar'}`;
      return `
        <article class="ops-branch-card ${cls}" data-branch="${b.branch_id}">
          <div class="ops-branch-head"><strong>${esc(b.branch_name)}</strong><span class="ops-attn">${esc(badge)}</span></div>
          <div class="ops-branch-rows">
            ${row('incidencias', b.incidents_alta ? `Incidencias (${plural(b.incidents_alta, 'grave', 'graves')})` : 'Incidencias', b.incidents_open, b.incidents_alta ? 'bad' : 'warn')}
            ${row('tareas', b.tasks_overdue ? `Tareas (${plural(b.tasks_overdue, 'vencida', 'vencidas')})` : 'Tareas', b.tasks_pending, b.tasks_overdue ? 'bad' : 'warn')}
            ${row('solicitudes', 'Solicitudes', b.requests_open + b.requests_approved, 'warn')}
            ${row('traslados', 'Traslados', b.transfers_to_approve + b.transfers_to_dispatch + b.transfers_to_receive, 'warn')}
            ${row('cargamentos', b.expected_overdue ? `Cargamentos (${plural(b.expected_overdue, 'atrasado', 'atrasados')})` : 'Cargamentos hoy', b.expected_today + b.expected_overdue, b.expected_overdue ? 'bad' : 'warn')}
            <span class="ops-row ${b.days_since_count != null && b.days_since_count > 7 ? 'warn' : 'ok'}" style="cursor:default"><span>${esc(conteo)}</span></span>
            <span class="ops-row ${b.prep_checkpoints_today && b.prep_filled_today < b.prep_checkpoints_today ? 'warn' : 'ok'}" style="cursor:default"><span>${esc(prep)}</span></span>
            <a class="ops-row ops-row-link ${b.unassigned_conversations ? 'warn' : 'zero'}" href="/app" style="grid-column:1 / -1"><span>Chats sin asignar: <b>${b.unassigned_conversations}</b></span><span class="ops-row-go">Ver chats →</span></a>
          </div>
        </article>`;
    }).join('') : empty('No hay sucursales activas para mostrar.');
    $('opsUpdated').textContent = `Actualizado a las ${new Date().toLocaleTimeString('es-PA', { hour: 'numeric', minute: '2-digit' })}. Se actualiza solo cada minuto y medio.`;
    loadOverviewCounts();
  }
  function n1(n, uno, varios) { return n === 1 ? uno : varios; }
  $('opsTotals').addEventListener('click', (e) => {
    const b = e.target.closest('button[data-tab]');
    if (b) setTab(b.dataset.tab);
  });
  $('opsBranchGrid').addEventListener('click', (e) => {
    const b = e.target.closest('button.ops-row[data-tab]');
    if (!b) return;
    const card = e.target.closest('.ops-branch-card');
    if (isGlobal && card) { state.branch = card.dataset.branch; sel.value = state.branch; updateScope(); }
    setTab(b.dataset.tab);
    window.scrollTo({ top: 0, behavior: 'smooth' });
  });

  // ==========================================================================
  // Incidencias
  // ==========================================================================
  async function loadIncidencias() {
    const box = $('incList');
    box.innerHTML = loading();
    const st = $('incStatus').value, sev = $('incSeverity').value;
    let rows;
    try {
      rows = await api.get(`/ops/incidents?limit=200${bq('&')}${st ? `&status=${st}` : ''}${sev ? `&severity=${sev}` : ''}`);
    } catch (err) { box.innerHTML = empty('No se pudieron cargar las incidencias.'); return; }
    if (!rows.length) { box.innerHTML = empty('No hay incidencias con ese filtro.'); return; }
    const teams = {};
    for (const id of new Set(rows.map((r) => r.branch_id))) teams[id] = await team(id);
    box.innerHTML = `<table class="ops-table ops-cards"><thead><tr><th>Incidencia</th><th>Sucursal</th><th>Reportó</th><th>A cargo</th><th>Estado</th><th><span class="sr-only">Acciones</span></th></tr></thead><tbody>${rows.map((i) => {
      const acciones = `
          ${i.status === 'abierta' ? `<button type="button" class="ops-btn" data-inc="${i.id}" data-status="en_proceso">Marcar en proceso</button>` : ''}
          ${i.status !== 'resuelta' ? `<button type="button" class="ops-btn primary" data-inc="${i.id}" data-resolve="${esc(i.title)}">Resolver</button>` : `<button type="button" class="ops-btn" data-inc="${i.id}" data-status="abierta">Reabrir</button>`}`;
      return `
      <tr class="${i.severity === 'alta' && i.status !== 'resuelta' ? 'overdue' : ''}">
        <td class="ops-td-main">${chip(`sev-${i.severity}`, SEV_LABEL[i.severity] || i.severity)} <span class="ops-title">${esc(i.title)}</span>${i.description ? `<span class="ops-sub">${esc(i.description)}</span>` : ''}${i.resolution_notes ? `<span class="ops-sub">✔ ${esc(i.resolution_notes)}</span>` : ''}</td>
        <td data-label="Sucursal">${esc(i.branch_name)}</td>
        <td data-label="Reportó">${esc(i.reported_by_name)}<span class="ops-sub">${esc(ago(i.created_at))}${i.hours_open != null && i.hours_open >= 24 ? ` · abierta hace ${plural(Math.round(i.hours_open / 24), 'día', 'días')}` : ''}</span></td>
        <td data-label="A cargo">${i.status === 'resuelta' ? esc(i.assigned_to_name || '—') : `<select class="ops-select" data-assign="${i.id}" data-branch="${i.branch_id}" aria-label="Quién se encarga">${teamOptions(teams[i.branch_id] || [], i.assigned_to_user_id)}</select>`}</td>
        <td data-label="Estado">${chip(`st-${i.status}`, STATUS_LABEL[i.status] || i.status)}${i.resolved_at ? `<span class="ops-sub">${esc(fmt(i.resolved_at))} · ${esc(i.resolved_by_name || '')}</span>` : ''}</td>
        ${actionsCell(acciones)}
      </tr>`;
    }).join('')}</tbody></table>`;
  }
  ['incStatus', 'incSeverity'].forEach((id) => $(id).addEventListener('change', loadIncidencias));
  $('incList').addEventListener('click', (e) => {
    const b = e.target.closest('button[data-inc]');
    if (!b) return;
    if (b.dataset.resolve !== undefined) {
      $('resolveForm').dataset.id = b.dataset.inc;
      $('resolveSubtitle').textContent = b.dataset.resolve;
      $('resolveNotes').value = '';
      openModal('modalResolve');
      return;
    }
    act(b, () => api.post(`/ops/incidents/${b.dataset.inc}/status`, { status: b.dataset.status }), 'Incidencia actualizada.');
  });
  $('incList').addEventListener('change', (e) => {
    const s = e.target.closest('select[data-assign]');
    if (!s) return;
    act(s, () => api.post(`/ops/incidents/${s.dataset.assign}/assign`, { user_id: s.value ? Number(s.value) : null }), s.value ? 'Incidencia asignada.' : 'Asignación quitada.');
  });
  $('resolveForm').addEventListener('submit', (e) => {
    e.preventDefault();
    const id = $('resolveForm').dataset.id;
    act(submitterOf(e), async () => {
      await api.post(`/ops/incidents/${id}/status`, { status: 'resuelta', resolution_notes: $('resolveNotes').value.trim() || null });
      closeModal('modalResolve');
    }, 'Incidencia resuelta.', 'Guardando…');
  });
  $('btnNewIncident').addEventListener('click', () => {
    $('incidentForm').reset();
    $('incBranch').innerHTML = branchOptions(state.branch || user.branch_id);
    $('incBranch').disabled = !isGlobal;
    $('incError').hidden = true;
    openModal('modalIncident');
  });
  $('incidentForm').addEventListener('submit', (e) => {
    e.preventDefault();
    $('incError').hidden = true;
    if (!$('incTitle').value.trim()) { showFormError('incError', 'Escribe qué pasó.'); return; }
    act(submitterOf(e), async () => {
      try {
        await api.post('/ops/incidents', { branch_id: Number($('incBranch').value), title: $('incTitle').value.trim(), severity: $('incSev').value, description: $('incDesc').value.trim() || null });
        closeModal('modalIncident');
      } catch (err) { showFormError('incError', err.message); throw err; }
    }, 'Incidencia reportada.', 'Enviando…');
  });

  // ==========================================================================
  // Tareas
  // ==========================================================================
  async function loadTareas() {
    const box = $('taskList');
    box.innerHTML = loading();
    const st = $('taskStatus').value, overdue = $('taskOverdue').checked;
    let rows;
    try {
      rows = await api.get(`/ops/tasks?limit=200${bq('&')}${st ? `&status=${st}` : ''}${overdue ? '&overdue=true' : ''}`);
    } catch (err) { box.innerHTML = empty('No se pudieron cargar las tareas.'); await loadRecurringTasks(); return; }
    if (!rows.length) { box.innerHTML = empty('No hay tareas con ese filtro.'); await loadRecurringTasks(); return; }
    state.rows.task = new Map(rows.map((t) => [String(t.id), t]));
    const teams = {};
    for (const id of new Set(rows.map((r) => r.branch_id))) teams[id] = await team(id);
    const abierta = (t) => t.status === 'pendiente' || t.status === 'en_proceso';
    box.innerHTML = `<table class="ops-table ops-cards"><thead><tr><th>Tarea</th><th>Sucursal</th><th>Asignada a</th><th>Vence</th><th>Estado</th><th><span class="sr-only">Acciones</span></th></tr></thead><tbody>${rows.map((t) => {
      // Primero lo que avanza la tarea; al final, apartado, lo que la cancela o la borra.
      const acciones = `
          ${t.status === 'pendiente' ? `<button type="button" class="ops-btn" data-task="${t.id}" data-status="en_proceso">Marcar en proceso</button>` : ''}
          ${abierta(t) ? `<button type="button" class="ops-btn primary" data-task="${t.id}" data-status="hecha"><i data-lucide="check"></i> Hecha</button>` : ''}
          ${abierta(t) ? `<button type="button" class="ops-btn" data-task-remind="${t.id}"><i data-lucide="bell"></i> Recordar</button>` : ''}
          ${abierta(t) ? `<button type="button" class="ops-btn danger" data-task="${t.id}" data-status="cancelada">Cancelar tarea</button>` : ''}
          ${user.role === 'admin' ? `<button type="button" class="ops-btn danger" data-task-delete="${t.id}"><i data-lucide="trash-2"></i> Eliminar</button>` : ''}`;
      return `
      <tr class="${t.overdue ? 'overdue' : ''}">
        <td class="ops-td-main"><span class="ops-title">${esc(t.title)}</span>${t.description ? `<span class="ops-sub">${esc(t.description)}</span>` : ''}<span class="ops-sub">creada por ${esc(t.created_by_name)} · ${esc(ago(t.created_at))}</span>${t.requires_photo || (t.photos || []).length ? `<span class="ops-task-photos">${t.requires_photo && !(t.photos || []).length ? '<span class="ops-chip st-pendiente"><i data-lucide="camera"></i> Pide foto al terminar</span>' : ''}${(t.photos || []).map((p, i) => `<button type="button" class="ops-btn" data-task-photo="/ops/tasks/${t.id}/photos/${p.id}"><i data-lucide="image"></i> Ver foto ${i + 1}</button>`).join('')}</span>` : ''}</td>
        <td data-label="Sucursal">${esc(t.branch_name)}</td>
        <td data-label="Asignada a">${abierta(t) ? `<select class="ops-select" data-task-assign="${t.id}" aria-label="Asignar a">${teamOptions(teams[t.branch_id] || [], t.assigned_to_user_id)}</select>` : esc(t.assigned_to_name || '—')}</td>
        <td data-label="Vence"><span class="ops-due ${t.overdue ? 'overdue' : ''}">${t.due_date ? esc(fmt(t.due_date)) : 'Sin fecha'}</span>${t.overdue ? ' ' + chip('overdue', 'Vencida') : ''}</td>
        <td data-label="Estado">${chip(`st-${t.status}`, STATUS_LABEL[t.status] || t.status)}${t.completed_at ? `<span class="ops-sub">${esc(fmt(t.completed_at))}</span>` : ''}</td>
        ${actionsCell(acciones)}
      </tr>`;
    }).join('')}</tbody></table>`;
    await loadRecurringTasks();
  }
  $('taskStatus').addEventListener('change', loadTareas);
  $('taskOverdue').addEventListener('change', loadTareas);
  $('taskList').addEventListener('click', async (e) => {
    const foto = e.target.closest('button[data-task-photo]');
    if (foto) { openProtectedPhoto(foto.dataset.taskPhoto); return; }
    const remind = e.target.closest('button[data-task-remind]');
    if (remind) {
      act(remind, () => api.post(`/ops/tasks/${remind.dataset.taskRemind}/remind`, {}), 'Recordatorio enviado: le llega un aviso a quien le toca.');
      return;
    }
    const del = e.target.closest('button[data-task-delete]');
    if (del) {
      const t = state.rows.task.get(del.dataset.taskDelete);
      const ok = await confirmar({
        title: '¿Eliminar esta tarea?', subtitle: t ? t.title : '', danger: true, okText: 'Sí, eliminar',
        html: '<p class="ops-confirm-text">Se borra por completo: no queda ni como cancelada en el historial.</p>',
      });
      if (!ok) return;
      act(del, () => api.delete(`/ops/tasks/${del.dataset.taskDelete}`), 'Tarea eliminada.');
      return;
    }
    const b = e.target.closest('button[data-task]');
    if (!b) return;
    if (b.dataset.status === 'cancelada') {
      const t = state.rows.task.get(b.dataset.task);
      const ok = await confirmar({
        title: '¿Cancelar esta tarea?', subtitle: t ? t.title : '', danger: true, okText: 'Sí, cancelar tarea',
        html: '<p class="ops-confirm-text">Deja de estar pendiente. Queda en el historial como cancelada.</p>',
      });
      if (!ok) return;
    }
    const msgs = { en_proceso: 'Tarea en proceso.', hecha: 'Tarea marcada como hecha.', cancelada: 'Tarea cancelada.' };
    act(b, () => api.post(`/ops/tasks/${b.dataset.task}/status`, { status: b.dataset.status }), msgs[b.dataset.status] || 'Tarea actualizada.');
  });
  $('taskList').addEventListener('change', (e) => {
    const s = e.target.closest('select[data-task-assign]');
    if (!s) return;
    const body = s.value ? { assigned_to_user_id: Number(s.value) } : { clear_assignee: true };
    act(s, () => api.patch(`/ops/tasks/${s.dataset.taskAssign}`, body), s.value ? 'Tarea asignada.' : 'Asignación quitada.');
  });
  $('btnNewTask').addEventListener('click', async () => {
    $('taskForm').reset();
    $('taskBranch').innerHTML = branchOptions(state.branch || user.branch_id);
    $('taskBranch').disabled = !isGlobal;
    $('taskError').hidden = true;
    await fillTaskAssignees();
    updateTaskDueHint();
    openModal('modalTask');
  });
  async function fillTaskAssignees() {
    const members = await team(Number($('taskBranch').value));
    $('taskAssignee').innerHTML = teamOptions(members, null).replace('<option value="">Sin asignar</option>', '<option value="">Todo el equipo de la sucursal</option>');
  }

  // ---- Vence: día y hora por separado ----
  // Antes era un solo campo de fecha y hora: si se elegía el día y no la hora, el navegador lo
  // dejaba vacío sin avisar y la tarea se creaba sin vencimiento.
  const isoDay = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
  function taskDueValue() {
    const dia = $('taskDueDate').value;
    if (!dia) return null;
    const hora = $('taskDueTime').value || '23:59';   // sin hora: vence al final del día
    return new Date(`${dia}T${hora}`);
  }
  function updateTaskDueHint() {
    const d = taskDueValue();
    const hint = $('taskDueHint');
    hint.classList.remove('is-error');
    document.querySelectorAll('.task-due-chip').forEach((b) => b.classList.remove('active'));
    if (!d) {
      hint.textContent = 'Sin fecha: la tarea no vence.';
      document.querySelector('.task-due-chip[data-due="none"]').classList.add('active');
      return;
    }
    const hoy = new Date();
    const manana = new Date(); manana.setDate(manana.getDate() + 1);
    const chipDia = $('taskDueDate').value === isoDay(hoy) ? 'hoy' : $('taskDueDate').value === isoDay(manana) ? 'manana' : null;
    if (chipDia) document.querySelector(`.task-due-chip[data-due="${chipDia}"]`).classList.add('active');
    const diaTxt = d.toLocaleDateString('es-PA', { weekday: 'long', day: 'numeric', month: 'long' });
    const horaTxt = $('taskDueTime').value ? `a las ${d.toLocaleTimeString('es-PA', { hour: 'numeric', minute: '2-digit' })}` : 'al final del día';
    if (d < new Date()) {
      hint.textContent = `Esa fecha ya pasó (${diaTxt} ${horaTxt}).`;
      hint.classList.add('is-error');
    } else {
      const frase = `Vence el ${diaTxt} ${horaTxt}`;
      hint.textContent = frase.endsWith('.') ? frase : `${frase}.`;
    }
  }
  document.querySelector('.task-due-quick').addEventListener('click', (e) => {
    const b = e.target.closest('.task-due-chip');
    if (!b) return;
    if (b.dataset.due === 'none') { $('taskDueDate').value = ''; $('taskDueTime').value = ''; }
    else {
      const d = new Date();
      if (b.dataset.due === 'manana') d.setDate(d.getDate() + 1);
      $('taskDueDate').value = isoDay(d);
    }
    updateTaskDueHint();
  });
  $('taskDueDate').addEventListener('input', updateTaskDueHint);
  $('taskDueDate').addEventListener('change', updateTaskDueHint);
  $('taskDueTime').addEventListener('input', updateTaskDueHint);
  $('taskDueTime').addEventListener('change', updateTaskDueHint);
  $('taskBranch').addEventListener('change', fillTaskAssignees);
  $('taskForm').addEventListener('submit', (e) => {
    e.preventDefault();
    $('taskError').hidden = true;
    if (!$('taskTitle').value.trim()) { showFormError('taskError', 'Escribe qué hay que hacer.'); return; }
    const dueDate = taskDueValue();
    if (dueDate && dueDate < new Date()) { showFormError('taskError', 'La fecha de vencimiento ya pasó. Elige otra o deja "Sin fecha".'); return; }
    act(submitterOf(e), async () => {
      try {
        await api.post('/ops/tasks', {
          branch_id: Number($('taskBranch').value), title: $('taskTitle').value.trim(),
          description: $('taskDescription').value.trim() || null,
          assigned_to_user_id: $('taskAssignee').value ? Number($('taskAssignee').value) : null,
          due_date: dueDate ? dueDate.toISOString() : null,
          requires_photo: $('taskRequiresPhoto').checked,
        });
        closeModal('modalTask');
      } catch (err) { showFormError('taskError', err.message); throw err; }
    }, 'Tarea creada.', 'Guardando…');
  });

  // ==========================================================================
  // Tareas recurrentes
  // ==========================================================================
  const frequencyLabel = (t) => {
    const horas = (t.times || []).map(hora12).join(', ');
    return t.frequency === 'monthly' ? `El día ${t.day_of_month} de cada mes · ${horas}` : `Todos los días · ${horas}`;
  };
  async function loadRecurringTasks() {
    const box = $('recurringTaskList');
    box.innerHTML = loading();
    let rows;
    try { rows = await api.get(`/ops/recurring-tasks${bq('?')}`); }
    catch (err) { box.innerHTML = empty('No se pudieron cargar las tareas recurrentes.'); return; }
    if (!rows.length) { box.innerHTML = empty('No hay tareas recurrentes todavía. Crea una con "Nueva tarea recurrente".'); return; }
    state.rows.rt = new Map(rows.map((t) => [String(t.id), t]));
    box.innerHTML = `<table class="ops-table ops-cards"><thead><tr><th>Tarea</th><th>Sucursal</th><th>Se repite</th><th>Estado</th><th><span class="sr-only">Acciones</span></th></tr></thead><tbody>${rows.map((t) => `
      <tr>
        <td class="ops-td-main"><span class="ops-title">${esc(t.title)}</span>${t.description ? `<span class="ops-sub">${esc(t.description)}</span>` : ''}</td>
        <td data-label="Sucursal">${esc(t.branch_name || 'Todas las sucursales')}</td>
        <td data-label="Se repite">${esc(frequencyLabel(t))}</td>
        <td data-label="Estado">${chip(t.active ? 'st-hecha' : 'st-cancelada', t.active ? 'Activa' : 'Pausada')}</td>
        ${actionsCell(`
          <button type="button" class="ops-btn" data-rt="${t.id}" data-rt-action="toggle" data-rt-active="${t.active}"><i data-lucide="${t.active ? 'pause' : 'play'}"></i> ${t.active ? 'Pausar' : 'Reanudar'}</button>
          <button type="button" class="ops-btn danger" data-rt="${t.id}" data-rt-action="delete"><i data-lucide="trash-2"></i> Eliminar</button>`)}
      </tr>`).join('')}</tbody></table>`;
  }
  $('recurringTaskList').addEventListener('click', async (e) => {
    const b = e.target.closest('button[data-rt]');
    if (!b) return;
    const id = b.dataset.rt;
    if (b.dataset.rtAction === 'toggle') {
      const active = b.dataset.rtActive === 'true';
      act(b, () => api.patch(`/ops/recurring-tasks/${id}`, { active: !active }), active ? 'Tarea recurrente pausada: no se crea hasta que la reanudes.' : 'Tarea recurrente reanudada.');
    } else if (b.dataset.rtAction === 'delete') {
      const t = state.rows.rt && state.rows.rt.get(id);
      const ok = await confirmar({
        title: '¿Eliminar esta tarea recurrente?', subtitle: t ? t.title : '', danger: true, okText: 'Sí, eliminar',
        html: '<p class="ops-confirm-text">Ya no se va a volver a crear sola. Las tareas que ya se crearon no se borran.</p><p class="ops-confirm-note">Si solo quieres detenerla un tiempo, usa "Pausar".</p>',
      });
      if (!ok) return;
      act(b, () => api.delete(`/ops/recurring-tasks/${id}`), 'Tarea recurrente eliminada.');
    }
  });

  // ---- Hora(s): una fila con selector de hora por cada una, con "Agregar otra hora" ----
  const MAX_TIMES = 10;
  function syncRtTimes() {
    const rows = [...$('rtTimes').children];
    rows.forEach((r) => { r.querySelector('[data-remove-time]').hidden = rows.length === 1; });
    $('btnAddRtTime').disabled = rows.length >= MAX_TIMES;
  }
  function addRtTime(value = '') {
    const box = $('rtTimes');
    if (box.children.length >= MAX_TIMES) { utils.showToast(`Máximo ${MAX_TIMES} horas por tarea.`, 'info'); return null; }
    const row = document.createElement('div');
    row.className = 'rt-time-row';
    const n = box.children.length + 1;
    row.innerHTML = `<input class="modal-input" type="time" value="${esc(value)}" aria-label="Hora ${n}" /><button type="button" class="ops-btn danger" data-remove-time aria-label="Quitar la hora ${n}">Quitar</button>`;
    box.appendChild(row);
    syncRtTimes();
    return row.querySelector('input');
  }
  $('btnAddRtTime').addEventListener('click', () => { const inp = addRtTime(); if (inp) inp.focus(); });
  $('rtTimes').addEventListener('click', (e) => {
    const b = e.target.closest('[data-remove-time]');
    if (!b) return;
    b.closest('.rt-time-row').remove();
    syncRtTimes();
  });

  $('btnNewRecurringTask').addEventListener('click', () => {
    $('recurringTaskForm').reset();
    $('rtBranch').innerHTML = branchOptions(state.branch || user.branch_id, { includeAll: isGlobal });
    $('rtBranch').disabled = !isGlobal;
    $('rtDayOfMonthWrap').hidden = true;
    $('rtError').hidden = true;
    $('rtTimes').innerHTML = '';
    addRtTime();
    openModal('modalRecurringTask');
  });
  $('rtFrequency').addEventListener('change', () => {
    $('rtDayOfMonthWrap').hidden = $('rtFrequency').value !== 'monthly';
  });
  $('recurringTaskForm').addEventListener('submit', (e) => {
    e.preventDefault();
    $('rtError').hidden = true;
    const frequency = $('rtFrequency').value;
    // Mismo formato que antes: lista de "HH:MM" (24 horas), sin repetidas y en orden.
    const times = [...new Set([...$('rtTimes').querySelectorAll('input[type="time"]')]
      .map((i) => (i.value || '').slice(0, 5)).filter((v) => /^\d{2}:\d{2}$/.test(v)))].sort();
    const dayOfMonth = frequency === 'monthly' ? Number($('rtDayOfMonth').value) : null;
    if (!$('rtTitle').value.trim()) { showFormError('rtError', 'Escribe qué hay que hacer.'); return; }
    if (frequency === 'monthly' && !(Number.isInteger(dayOfMonth) && dayOfMonth >= 1 && dayOfMonth <= 31)) { showFormError('rtError', 'Escribe el día del mes: un número del 1 al 31.'); return; }
    if (!times.length) { showFormError('rtError', 'Elige al menos una hora.'); return; }
    act(submitterOf(e), async () => {
      try {
        await api.post('/ops/recurring-tasks', {
          branch_id: $('rtBranch').value ? Number($('rtBranch').value) : null,
          title: $('rtTitle').value.trim(), description: $('rtDescription').value.trim() || null,
          frequency, times, day_of_month: dayOfMonth,
        });
        closeModal('modalRecurringTask');
      } catch (err) { showFormError('rtError', err.message); throw err; }
    }, 'Tarea recurrente creada.', 'Guardando…');
  });

  // ==========================================================================
  // Solicitudes de insumos
  // ==========================================================================
  async function loadSolicitudes() {
    const box = $('reqList');
    box.innerHTML = loading();
    const st = $('reqStatus').value;
    let rows;
    try { rows = await api.get(`/ops/requests?limit=200${bq('&')}${st ? `&status=${st}` : ''}`); }
    catch (err) { box.innerHTML = empty('No se pudieron cargar las solicitudes.'); return; }
    if (!rows.length) { box.innerHTML = empty('No hay solicitudes con ese filtro.'); return; }
    state.rows.req = new Map(rows.map((r) => [String(r.id), r]));
    box.innerHTML = `<table class="ops-table ops-cards"><thead><tr><th>Insumo</th><th>Sucursal</th><th>Pidió</th><th>Estado</th><th><span class="sr-only">Acciones</span></th></tr></thead><tbody>${rows.map((r) => {
      const acciones = `
          ${r.status === 'open' ? `<button type="button" class="ops-btn primary" data-req="${r.id}" data-status="approved"><i data-lucide="check"></i> Aprobar</button>` : ''}
          ${r.status === 'open' || r.status === 'approved' ? `<button type="button" class="ops-btn" data-req="${r.id}" data-status="fulfilled"><i data-lucide="package-check"></i> Marcar entregada</button><button type="button" class="ops-btn danger" data-req="${r.id}" data-status="cancelled">Cancelar solicitud</button>` : ''}`;
      return `
      <tr>
        <td class="ops-td-main"><span class="ops-title">${esc(r.item_name)}</span>${r.quantity_hint ? ` · <b>${esc(r.quantity_hint)}</b>` : ''}${r.notes ? `<span class="ops-sub">${esc(r.notes)}</span>` : ''}</td>
        <td data-label="Sucursal">${esc(r.branch_name)}</td>
        <td data-label="Pidió">${esc(r.requested_by_name)}<span class="ops-sub">${esc(ago(r.created_at))}</span></td>
        <td data-label="Estado">${chip(`st-${r.status}`, STATUS_LABEL[r.status] || r.status)}${r.approved_at ? `<span class="ops-sub">aprobó ${esc(r.approved_by_name || '')} · ${esc(fmt(r.approved_at))}</span>` : ''}${r.resolved_at ? `<span class="ops-sub">${esc(r.resolved_by_name || '')} · ${esc(fmt(r.resolved_at))}</span>` : ''}</td>
        ${actionsCell(acciones)}
      </tr>`;
    }).join('')}</tbody></table>`;
  }
  $('reqStatus').addEventListener('change', loadSolicitudes);
  $('reqList').addEventListener('click', async (e) => {
    const b = e.target.closest('button[data-req]');
    if (!b) return;
    const r = state.rows.req.get(b.dataset.req);
    const quien = r ? `${r.item_name}${r.quantity_hint ? ` · ${r.quantity_hint}` : ''} — ${r.branch_name}` : '';
    if (b.dataset.status === 'fulfilled') {
      const ok = await confirmar({
        title: '¿Marcar como entregada?', subtitle: quien, okText: 'Sí, ya se entregó',
        html: `<p class="ops-confirm-text">Se cierra la solicitud y se le avisa a ${esc(r ? r.requested_by_name : 'quien la pidió')}.</p>
               <p class="ops-confirm-note">No cambia el inventario: lo que llega de un proveedor se registra con "Recibir mercancía" en Inventario, y lo que viene de otra sucursal, con un traslado.</p>`,
      });
      if (!ok) return;
    }
    if (b.dataset.status === 'cancelled') {
      const ok = await confirmar({
        title: '¿Cancelar esta solicitud?', subtitle: quien, danger: true, okText: 'Sí, cancelar solicitud',
        html: `<p class="ops-confirm-text">Se cierra sin entregarse y se le avisa a ${esc(r ? r.requested_by_name : 'quien la pidió')}.</p>`,
      });
      if (!ok) return;
    }
    const msgs = { approved: 'Solicitud aprobada.', fulfilled: 'Solicitud marcada como entregada.', cancelled: 'Solicitud cancelada.' };
    act(b, () => api.post(`/ops/requests/${b.dataset.req}/status?status=${b.dataset.status}`, {}), msgs[b.dataset.status] || 'Solicitud actualizada.');
  });

  // ==========================================================================
  // Traslados
  // ==========================================================================
  const canActOn = (branchId) => isGlobal || Number(user.branch_id) === Number(branchId);
  const TRF_LABEL = { requested: 'Por aprobar', approved: 'Por despachar', dispatched: 'En camino', received: 'Recibido', rejected: 'Rechazado', cancelled: 'Cancelado' };
  const trfLines = (t) => t.items.map((i) => ({ name: i.item_name, qty: `${num(i.quantity)} ${i.unit || ''}`.trim() }));
  async function loadTraslados() {
    const box = $('trfList');
    box.innerHTML = loading();
    const st = $('trfStatus').value;
    let rows;
    try {
      rows = await api.get(`/transfers/?limit=200${bq('&')}${st && st !== 'activos' ? `&status=${st}` : ''}`);
      if (st === 'activos') rows = rows.filter((t) => ['requested', 'approved', 'dispatched'].includes(t.status));
    } catch (err) { box.innerHTML = empty('No se pudieron cargar los traslados.'); return; }
    if (!rows.length) { box.innerHTML = empty('No hay traslados con ese filtro.'); return; }
    state.rows.trf = new Map(rows.map((t) => [String(t.id), t]));
    box.innerHTML = `<table class="ops-table ops-cards"><thead><tr><th>Traslado</th><th>Insumos</th><th>Pidió</th><th>Estado</th><th><span class="sr-only">Acciones</span></th></tr></thead><tbody>${rows.map((t) => {
      const items = trfLines(t).map((i) => `${i.qty} ${i.name}`);
      const acts = [];
      if (t.status === 'requested' && canActOn(t.from_branch_id)) acts.push(`<button type="button" class="ops-btn primary" data-trf="${t.id}" data-act="approve"><i data-lucide="check"></i> Aprobar</button>`);
      if (t.status === 'approved' && canActOn(t.from_branch_id)) acts.push(`<button type="button" class="ops-btn primary" data-trf="${t.id}" data-act="dispatch"><i data-lucide="truck"></i> Despachar</button>`);
      if (t.status === 'dispatched' && canActOn(t.to_branch_id)) acts.push(`<button type="button" class="ops-btn primary" data-trf="${t.id}" data-act="receive"><i data-lucide="package-check"></i> Recibir traslado</button>`);
      if (t.status === 'requested' && canActOn(t.from_branch_id)) acts.push(`<button type="button" class="ops-btn danger" data-trf="${t.id}" data-act="reject">Rechazar</button>`);
      if ((t.status === 'requested' || t.status === 'approved') && (isGlobal || t.requested_by_user_id === user.id)) acts.push(`<button type="button" class="ops-btn danger" data-trf="${t.id}" data-act="cancel">Cancelar traslado</button>`);
      return `
      <tr>
        <td class="ops-td-main"><span class="ops-title">${esc(t.from_branch_name)} → ${esc(t.to_branch_name)}</span><span class="ops-sub">Traslado #${t.id}</span>${t.notes ? `<span class="ops-sub">${esc(t.notes)}</span>` : ''}</td>
        <td data-label="Insumos" class="ops-td-wide">${items.slice(0, 4).map(esc).join('<br>')}${items.length > 4 ? `<span class="ops-sub">y ${items.length - 4} más</span>` : ''}</td>
        <td data-label="Pidió">${esc(t.requested_by_name)}<span class="ops-sub">${esc(ago(t.requested_at))}</span></td>
        <td data-label="Estado">${chip(`st-${t.status}`, TRF_LABEL[t.status] || t.status)}<span class="ops-sub">${t.dispatched_at ? `salió ${esc(fmt(t.dispatched_at))}` : (t.approved_at ? `aprobado ${esc(fmt(t.approved_at))}` : '')}${t.received_at ? ` · llegó ${esc(fmt(t.received_at))}` : ''}</span></td>
        ${actionsCell(acts.join(''))}
      </tr>`;
    }).join('')}</tbody></table>`;
  }
  $('trfStatus').addEventListener('change', loadTraslados);
  $('trfList').addEventListener('click', async (e) => {
    const b = e.target.closest('button[data-trf]');
    if (!b) return;
    const a = b.dataset.act;
    const t = state.rows.trf.get(b.dataset.trf);
    const ruta = t ? `${t.from_branch_name} → ${t.to_branch_name}` : '';
    const lista = t ? itemsList(trfLines(t)) : '';
    let body = {};
    let res = true;
    // Despachar y recibir mueven inventario en el acto: se confirma mostrando qué y hacia dónde.
    if (a === 'dispatch') {
      res = await confirmar({
        title: '¿Despachar este traslado?', subtitle: `Traslado #${b.dataset.trf} · ${ruta}`, okText: 'Sí, despachar',
        html: `<p class="ops-confirm-text">Esto <b>sale ahora del inventario de ${esc(t ? t.from_branch_name : 'origen')}</b> y queda en camino a ${esc(t ? t.to_branch_name : 'destino')}:</p>${lista}
               <p class="ops-confirm-note">Confírmalo cuando la mercancía ya esté saliendo.</p>`,
      });
    } else if (a === 'receive') {
      res = await confirmar({
        title: '¿Recibir este traslado?', subtitle: `Traslado #${b.dataset.trf} · ${ruta}`, okText: 'Sí, ya llegó',
        html: `<p class="ops-confirm-text">Esto <b>entra ahora al inventario de ${esc(t ? t.to_branch_name : 'destino')}</b> (viene de ${esc(t ? t.from_branch_name : 'origen')}):</p>${lista}
               <p class="ops-confirm-note">Revisa que llegó todo antes de confirmar.</p>`,
      });
    } else if (a === 'reject' || a === 'cancel') {
      res = await confirmar({
        title: a === 'reject' ? '¿Rechazar este traslado?' : '¿Cancelar este traslado?',
        subtitle: `Traslado #${b.dataset.trf} · ${ruta}`, danger: true,
        okText: a === 'reject' ? 'Sí, rechazar' : 'Sí, cancelar traslado',
        html: `${lista}<p class="ops-confirm-note">No se mueve nada del inventario. Se le avisa a quien lo pidió.</p>`,
        reason: { label: a === 'reject' ? 'Por qué se rechaza (opcional)' : 'Motivo (opcional)', placeholder: a === 'reject' ? 'Ej: no nos alcanza para el fin de semana' : 'Ej: ya no hace falta' },
      });
      if (res) body = { notes: res.reason || null };
    }
    if (!res) return;
    const msgs = { approve: 'Traslado aprobado. Falta despacharlo.', reject: 'Traslado rechazado.', dispatch: 'Traslado despachado: ya salió del inventario de origen.', receive: 'Traslado recibido: ya entró al inventario de destino.', cancel: 'Traslado cancelado.' };
    act(b, () => api.post(`/transfers/${b.dataset.trf}/${a}`, body), msgs[a]);
  });

  // ---- nuevo traslado ----
  let itemSearchTimer = null, itemSearchSeq = 0, pickedItem = null, lastResults = [];
  $('btnNewTransfer').addEventListener('click', () => {
    $('transferForm').reset();
    state.transferItems = [];
    pickedItem = null;
    setQtyLabel('');
    renderTransferItems();
    const own = user.branch_id ? String(user.branch_id) : '';
    $('trfFrom').innerHTML = branchOptions(state.branch || own);
    $('trfTo').innerHTML = branchOptions('');
    $('trfError').hidden = true;
    $('trfItemResults').hidden = true;
    openModal('modalTransfer');
  });
  function setQtyLabel(unit) { $('trfItemQtyLabel').textContent = unit ? `Cantidad (${unit})` : 'Cantidad'; }
  function pickItem(it) {
    pickedItem = it;
    $('trfItemSearch').value = it.name;
    $('trfItemResults').hidden = true;
    setQtyLabel(it.unit);
    $('trfItemQty').focus();
  }
  $('trfItemSearch').addEventListener('input', () => {
    const q = $('trfItemSearch').value.trim();
    pickedItem = null;
    setQtyLabel('');
    clearTimeout(itemSearchTimer);
    const box = $('trfItemResults');
    if (q.length < 2) { box.hidden = true; return; }
    itemSearchTimer = setTimeout(async () => {
      const seq = ++itemSearchSeq;
      try {
        const items = await api.get(`/inventory/items?q=${encodeURIComponent(q)}`);
        if (seq !== itemSearchSeq) return;
        lastResults = items;
        box.innerHTML = items.length
          ? items.map((i) => `<button type="button" data-id="${i.id}" data-name="${esc(i.name)}" data-unit="${esc(i.unit || '')}">${esc(i.name)} <small>${esc(i.unit || '')}</small></button>`).join('')
          : `<div class="ops-autocomplete-empty">No se encontró «${esc(q)}». Prueba con otra palabra.</div>`;
        box.hidden = false;
      } catch (e) {
        if (seq !== itemSearchSeq) return;
        box.innerHTML = '<div class="ops-autocomplete-empty">No se pudo buscar. Revisa la conexión e intenta de nuevo.</div>';
        box.hidden = false;
      }
    }, 250);
  });
  $('trfItemResults').addEventListener('click', (e) => {
    const b = e.target.closest('button[data-id]');
    if (!b) return;
    pickItem({ id: Number(b.dataset.id), name: b.dataset.name, unit: b.dataset.unit });
  });
  // Enter en el buscador o la cantidad no manda el formulario entero: elige o agrega la línea.
  $('trfItemSearch').addEventListener('keydown', (e) => {
    if (e.key !== 'Enter') return;
    e.preventDefault();
    if (pickedItem) { $('trfItemQty').focus(); return; }
    if (lastResults.length === 1 && !$('trfItemResults').hidden) {
      const i = lastResults[0];
      pickItem({ id: i.id, name: i.name, unit: i.unit || '' });
    }
  });
  $('trfItemQty').addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); addTransferLine(); } });
  function addTransferLine({ silent = false } = {}) {
    const qty = Number($('trfItemQty').value);
    if (!pickedItem) { if (!silent) utils.showToast('Elige un insumo de la lista que aparece al escribir.', 'error'); return false; }
    if (!(qty > 0)) { if (!silent) { utils.showToast('Escribe la cantidad a trasladar.', 'error'); $('trfItemQty').focus(); } return false; }
    state.transferItems = state.transferItems.filter((i) => i.id !== pickedItem.id).concat([{ ...pickedItem, qty }]);
    pickedItem = null;
    $('trfItemSearch').value = '';
    $('trfItemQty').value = '';
    setQtyLabel('');
    renderTransferItems();
    if (!silent) $('trfItemSearch').focus();
    return true;
  }
  $('btnAddTrfItem').addEventListener('click', () => addTransferLine());
  function renderTransferItems() {
    $('trfItems').innerHTML = state.transferItems.map((i, idx) => `<li><span><b>${esc(num(i.qty))} ${esc(i.unit)}</b> ${esc(i.name)}</span><button type="button" data-remove="${idx}" aria-label="Quitar ${esc(i.name)}">Quitar</button></li>`).join('');
    $('trfItemsEmpty').hidden = state.transferItems.length > 0;
  }
  $('trfItems').addEventListener('click', (e) => {
    const b = e.target.closest('button[data-remove]');
    if (!b) return;
    state.transferItems.splice(Number(b.dataset.remove), 1);
    renderTransferItems();
  });
  $('transferForm').addEventListener('submit', (e) => {
    e.preventDefault();
    // Si dejó un insumo elegido con cantidad sin tocar "Agregar", se agrega solo.
    if (pickedItem && Number($('trfItemQty').value) > 0) addTransferLine({ silent: true });
    const from = Number($('trfFrom').value), to = Number($('trfTo').value);
    const err = $('trfError');
    err.hidden = true;
    if (!from || !to) { showFormError('trfError', 'Elige de qué sucursal sale y a cuál llega.'); return; }
    if (from === to) { showFormError('trfError', 'La sucursal de origen y la de destino no pueden ser la misma.'); return; }
    if (!state.transferItems.length) { showFormError('trfError', 'Agrega al menos un insumo con su cantidad.'); return; }
    act(submitterOf(e), async () => {
      try {
        await api.post('/transfers/', {
          from_branch_id: from, to_branch_id: to, notes: $('trfNotes').value.trim() || null,
          items: state.transferItems.map((i) => ({ inventory_item_id: i.id, quantity: String(i.qty) })),
        });
        closeModal('modalTransfer');
        $('trfStatus').value = 'activos';
      } catch (ex) { showFormError('trfError', ex.message); throw ex; }
    }, 'Traslado pedido: la sucursal de origen recibió el aviso.', 'Enviando…');
  });

  // ==========================================================================
  // Cargamentos esperados
  // ==========================================================================
  async function loadCargamentos() {
    const box = $('expList');
    box.innerHTML = loading();
    let rows;
    try { rows = await api.get(`/inventory/expected-shipments?status=pendiente&days_ahead=30${bq('&')}`); }
    catch (err) { box.innerHTML = empty('No se pudieron cargar los cargamentos esperados.'); return; }
    if (!rows.length) { box.innerHTML = empty('No hay cargamentos programados pendientes.'); return; }
    state.rows.exp = new Map(rows.map((r) => [String(r.id), r]));
    const hoy = todayPanama();
    rows.sort((a, b) => a.expected_date.localeCompare(b.expected_date));
    box.innerHTML = `<table class="ops-table ops-cards"><thead><tr><th>Cargamento</th><th>Sucursal</th><th>Llega</th><th>Programó</th><th><span class="sr-only">Acciones</span></th></tr></thead><tbody>${rows.map((e) => {
      const atrasado = e.expected_date < hoy, esHoy = e.expected_date === hoy;
      return `
      <tr class="${atrasado ? 'overdue' : ''}">
        <td class="ops-td-main"><span class="ops-title">${esc(e.supplier_name || 'Proveedor sin nombre')}</span>${e.notes ? `<span class="ops-sub">${esc(e.notes)}</span>` : ''}</td>
        <td data-label="Sucursal">${esc(e.branch_name)}</td>
        <td data-label="Llega"><span class="ops-due ${atrasado ? 'overdue' : ''}">${esc(fechaDia(e.expected_date))}</span> ${atrasado ? chip('overdue', 'Atrasado') : (esHoy ? chip('st-pendiente', 'Hoy') : '')}${e.time_from ? `<span class="ops-sub">desde las ${esc(hora12(e.time_from))}</span>` : ''}</td>
        <td data-label="Programó">${esc(e.created_by_name || '—')}<span class="ops-sub">${esc(ago(e.created_at))}</span></td>
        ${actionsCell(`
          <a class="ops-btn primary" href="/inventario?view=cargamentos"><i data-lucide="truck"></i> Recibir mercancía</a>
          ${canAdjust ? `<button type="button" class="ops-btn danger" data-exp="${e.id}">Cancelar cargamento</button>` : ''}`)}
      </tr>`;
    }).join('')}</tbody></table>`;
  }
  $('expList').addEventListener('click', async (e) => {
    const b = e.target.closest('button[data-exp]');
    if (!b) return;
    const x = state.rows.exp.get(b.dataset.exp);
    const ok = await confirmar({
      title: '¿Cancelar este cargamento programado?', danger: true, okText: 'Sí, cancelar cargamento',
      subtitle: x ? `${x.supplier_name || 'Proveedor sin nombre'} · ${x.branch_name}` : '',
      html: `<p class="ops-confirm-text">${x ? `Llegaba el ${esc(fechaDia(x.expected_date))}. ` : ''}Se quita de los cargamentos esperados de la sucursal. No cambia el inventario.</p>`,
    });
    if (!ok) return;
    act(b, () => api.post(`/inventory/expected-shipments/${b.dataset.exp}/cancel`, {}), 'Cargamento cancelado.');
  });

  // ==========================================================================
  // Arranque
  // ==========================================================================
  updateScope();
  setTab(state.tab);
  loadOverviewCounts();
  setTimeout(revealActiveTab, 400);
  state.refreshTimer = setInterval(() => { if (!document.hidden) { if (state.tab === 'resumen') loadResumen(); else loadOverviewCounts(); } }, 90000);
  utils.renderIcons();
});
