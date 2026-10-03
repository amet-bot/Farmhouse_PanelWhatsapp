/**
 * Farmhouse Link — Centro de operación
 *
 * Una sola pantalla con lo pendiente de todas las sucursales (o de la propia, para un
 * supervisor local): incidencias, tareas, solicitudes de insumos, traslados y cargamentos
 * esperados, con sus acciones. Es la pantalla de quien maneja varias sucursales sin estar en
 * ninguna: encargado de logística, gerencia. Los agentes reportan desde la tablet (/operacion);
 * esta pantalla es para encargados (permiso purchasing.approve).
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
  const ago = (iso) => {
    if (!iso) return '';
    const mins = Math.max(0, Math.round((Date.now() - utils._parseServerDate(iso)) / 60000));
    if (mins < 60) return `hace ${mins} min`;
    if (mins < 60 * 24) return `hace ${Math.round(mins / 60)} h`;
    return `hace ${Math.round(mins / 1440)} d`;
  };
  const chip = (cls, text) => `<span class="ops-chip ${cls}">${esc(text)}</span>`;
  const empty = (text) => `<div class="ops-empty">${esc(text)}</div>`;
  const loading = () => '<div class="ops-loading">Cargando…</div>';
  const setCount = (id, n) => { const el = $(id); if (!el) return; el.hidden = !n; el.textContent = n; };
  const openModal = (id) => { $(id).classList.add('active'); utils.renderIcons(); };
  const closeModal = (id) => $(id).classList.remove('active');
  document.querySelectorAll('[data-close]').forEach((b) => b.addEventListener('click', () => closeModal(b.dataset.close)));

  async function withBusy(btn, fn) {
    if (btn) btn.disabled = true;
    try { await fn(); } finally { if (btn) btn.disabled = false; }
  }
  async function act(btn, fn, okMsg) {
    await withBusy(btn, async () => {
      try {
        await fn();
        if (okMsg) utils.showToast(okMsg, 'success');
        await loadTab(state.tab);
        loadOverviewCounts();
      } catch (err) {
        utils.showToast(err.message || 'No se pudo completar la acción.', 'error');
      }
    });
  }
  async function team(branchId) {
    if (!state.team[branchId]) {
      try { state.team[branchId] = await api.get(`/ops/team?branch_id=${branchId}`); } catch (e) { state.team[branchId] = []; }
    }
    return state.team[branchId];
  }
  function teamOptions(members, selectedId) {
    return `<option value="">Sin asignar</option>` + members.map((m) => `<option value="${m.id}" ${m.id === selectedId ? 'selected' : ''}>${esc(m.name)}${m.branch_id ? '' : ' (global)'}</option>`).join('');
  }
  function branchOptions(selectedId, { includeAll = false } = {}) {
    const rows = state.branches.filter((b) => b.active !== false && b.code !== 'CAT');
    return (includeAll ? `<option value="">Todas las sucursales</option>` : '') +
      rows.map((b) => `<option value="${b.id}" ${String(b.id) === String(selectedId) ? 'selected' : ''}>${esc(b.name)}</option>`).join('');
  }

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
  function setTab(tab) {
    if (!TABS.includes(tab)) tab = 'resumen';
    state.tab = tab;
    document.querySelectorAll('.ops-tab').forEach((b) => b.classList.toggle('active', b.dataset.tab === tab));
    TABS.forEach((t) => { $(VIEW[t]).hidden = t !== tab; });
    syncUrl();
    loadTab(tab);
  }
  $('opsTabs').addEventListener('click', (e) => { const b = e.target.closest('.ops-tab'); if (b) setTab(b.dataset.tab); });
  $('btnRefresh').addEventListener('click', () => { loadTab(state.tab); loadOverviewCounts(); });

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
    'shipment.create': 'Recibió un cargamento', 'shipment.delete': 'Borró un cargamento', 'shipment.photo_add': 'Agregó foto a un cargamento',
    'expected_shipment.create': 'Programó un cargamento', 'expected_shipment.cancel': 'Canceló un cargamento programado',
    'purchase_order.create': 'Creó una orden de compra', 'supply_setting.save': 'Cambió mínimos y pares',
    'supply_request.create': 'Pidió insumos', 'supply_request.approved': 'Aprobó una solicitud', 'supply_request.fulfilled': 'Entregó una solicitud',
    'supply_request.cancelled': 'Canceló una solicitud', 'supply_request.open': 'Reabrió una solicitud',
    'transfer.create': 'Pidió un traslado', 'transfer.approve': 'Aprobó un traslado', 'transfer.dispatch': 'Despachó un traslado',
    'transfer.receive': 'Recibió un traslado', 'transfer.reject': 'Rechazó un traslado', 'transfer.cancel': 'Canceló un traslado',
    'incident.create': 'Reportó una incidencia', 'incident.assign': 'Asignó una incidencia', 'incident.resuelta': 'Resolvió una incidencia',
    'incident.en_proceso': 'Tomó una incidencia', 'incident.abierta': 'Reabrió una incidencia',
    'waste.photo_add': 'Agregó foto a una merma', 'prep_check.create': 'Llenó el prep de bowls', 'prep_check.update': 'Corrigió el prep de bowls',
    'recurring_task.create': 'Creó una tarea recurrente', 'recurring_task.fire': 'Se creó sola una tarea recurrente',
    'item.piece_size': 'Cambió el tamaño de pieza', 'user.create': 'Creó un usuario', 'user.update': 'Editó un usuario',
    'user.delete': 'Eliminó un usuario', 'user.toggle_active': 'Activó/desactivó un usuario',
    'backup.create': 'Hizo un respaldo', 'backup.download': 'Bajó un respaldo',
    'alert.low_stock': 'Aviso de stock bajo', 'digest.weekly': 'Resumen semanal', 'digest.daily': 'Resumen diario',
    'task.overdue_alert': 'Aviso: tarea vencida', 'alert.closing_missing': 'Aviso: faltó el cierre de turno',
    'recurring_task.update': 'Editó una tarea recurrente', 'recurring_task.delete': 'Eliminó una tarea recurrente',
    'prep_template.create': 'Creó una plantilla de prep', 'prep_template.update': 'Editó una plantilla de prep', 'prep_template.delete': 'Eliminó una plantilla de prep',
  };
  const ACT_LINKS = {
    task: () => '/gestion?tab=tareas', recurring_task: () => '/gestion?tab=tareas', stock_count: () => '/inventario?view=conteos', waste: (id) => `/inventario?view=merma&waste=${id}`,
    shipment: () => '/inventario?view=cargamentos', transfer: () => '/gestion?tab=traslados', incident: () => '/gestion?tab=incidencias',
    supply_request: () => '/gestion?tab=solicitudes', expected_shipment: () => '/abastecimiento?tab=ordenes',
  };
  const actLabel = (a) => ACT_LABELS[a] || a.replace(/[._]/g, ' ');
  function actDetail(e) {
    const m = e.metadata || {};
    const partes = [];
    for (const k of ['title', 'name', 'item_name', 'reason', 'role', 'status', 'from']) if (m[k] != null && m[k] !== '') partes.push(String(m[k]));
    if (m.items != null) partes.push(`${m.items} insumo${Number(m.items) === 1 ? '' : 's'}`);
    if (m.total_qty != null) partes.push(`total ${m.total_qty}`);
    return partes.slice(0, 3).join(' · ');
  }
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
    box.innerHTML = `<table class="ops-table"><thead><tr><th>Cuándo</th><th>Quién</th><th>Sucursal</th><th>Qué hizo</th><th>Detalle</th></tr></thead><tbody>${actState.rows.map((e) => {
      const link = ACT_LINKS[e.entity_type] ? ACT_LINKS[e.entity_type](e.entity_id) : null;
      return `<tr>
        <td><span class="ops-title">${esc(fmt(e.created_at))}</span><span class="ops-sub">${esc(ago(e.created_at))}</span></td>
        <td>${esc(e.actor ? e.actor.name : 'Sistema')}</td>
        <td>${esc(e.branch ? e.branch.name : '—')}</td>
        <td>${link ? `<a class="ops-link" href="${link}">${esc(actLabel(e.action))}</a>` : esc(actLabel(e.action))}</td>
        <td><span class="ops-sub">${esc(actDetail(e)) || '—'}</span></td>
      </tr>`;
    }).join('')}</tbody></table>`;
  }
  ['actGroup', 'actActor', 'actFrom', 'actTo'].forEach((id) => $(id).addEventListener('change', () => loadActividad(false)));
  $('btnActMore').addEventListener('click', () => loadActividad(true));

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
    } catch (e) { /* los contadores son un extra */ }
  }

  async function loadResumen() {
    $('opsBranchGrid').innerHTML = loading();
    let o;
    try { o = await fetchOverview(); } catch (err) { $('opsBranchGrid').innerHTML = empty('No se pudo cargar el resumen.'); return; }
    const t = o.totals;
    const total = (label, n, tone) => `<span class="ops-total ${n > 0 ? tone : ''}"><b>${n}</b> ${label}</span>`;
    $('opsTotals').innerHTML = [
      total('incidencias abiertas', t.incidents_open, t.incidents_alta ? 'bad' : 'warn'),
      total('tareas vencidas', t.tasks_overdue, 'bad'),
      total('solicitudes sin aprobar', t.requests_open, 'warn'),
      total('traslados por mover', t.transfers_to_approve + t.transfers_to_dispatch + t.transfers_to_receive, 'warn'),
      total('cargamentos atrasados', t.expected_overdue, 'bad'),
      total('chats sin asignar', t.unassigned_conversations, 'warn'),
    ].join('');

    const row = (tab, label, n, tone) => `<button type="button" class="ops-row ${n ? tone : 'zero'}" data-tab="${tab}"><span>${esc(label)}</span><b>${n}</b></button>`;
    $('opsBranchGrid').innerHTML = o.branches.length ? o.branches.map((b) => {
      const cls = b.attention >= 6 ? 'attn-high' : (b.attention >= 2 ? 'attn-mid' : '');
      const conteo = b.days_since_count == null ? 'Sin conteo' : `Conteo hace ${b.days_since_count} d`;
      const prep = b.prep_checkpoints_today ? `Prep ${b.prep_filled_today}/${b.prep_checkpoints_today} hoy` : 'Sin plantilla de prep';
      return `
        <article class="ops-branch-card ${cls}" data-branch="${b.branch_id}">
          <div class="ops-branch-head"><strong>${esc(b.branch_name)}</strong><span class="ops-attn">${b.attention ? `${b.attention} pts` : 'al día'}</span></div>
          <div class="ops-branch-rows">
            ${row('incidencias', b.incidents_alta ? `Incidencias (${b.incidents_alta} grave${b.incidents_alta === 1 ? '' : 's'})` : 'Incidencias', b.incidents_open, b.incidents_alta ? 'bad' : 'warn')}
            ${row('tareas', b.tasks_overdue ? `Tareas (${b.tasks_overdue} vencida${b.tasks_overdue === 1 ? '' : 's'})` : 'Tareas', b.tasks_pending, b.tasks_overdue ? 'bad' : 'warn')}
            ${row('solicitudes', 'Solicitudes', b.requests_open + b.requests_approved, 'warn')}
            ${row('traslados', 'Traslados', b.transfers_to_approve + b.transfers_to_dispatch + b.transfers_to_receive, 'warn')}
            ${row('cargamentos', b.expected_overdue ? `Cargamentos (${b.expected_overdue} atrasado${b.expected_overdue === 1 ? '' : 's'})` : 'Cargamentos hoy', b.expected_today + b.expected_overdue, b.expected_overdue ? 'bad' : 'warn')}
            <span class="ops-row ${b.days_since_count != null && b.days_since_count > 7 ? 'warn' : 'ok'}" style="cursor:default"><span>${esc(conteo)}</span></span>
            <span class="ops-row ${b.prep_checkpoints_today && b.prep_filled_today < b.prep_checkpoints_today ? 'warn' : 'ok'}" style="cursor:default"><span>${esc(prep)}</span></span>
            ${row('resumen', 'Chats sin asignar', b.unassigned_conversations, 'warn')}
          </div>
        </article>`;
    }).join('') : empty('No hay sucursales activas para mostrar.');
    $('opsUpdated').textContent = `Actualizado ${new Date().toLocaleTimeString('es-PA', { hour: '2-digit', minute: '2-digit' })}`;
    loadOverviewCounts();
  }
  $('opsBranchGrid').addEventListener('click', (e) => {
    const b = e.target.closest('.ops-row[data-tab]');
    if (!b) return;
    const card = e.target.closest('.ops-branch-card');
    if (isGlobal && card) { state.branch = card.dataset.branch; sel.value = state.branch; updateScope(); }
    if (b.dataset.tab === 'resumen') { window.location.href = '/app'; return; }
    setTab(b.dataset.tab);
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
    box.innerHTML = `<table class="ops-table"><thead><tr><th>Incidencia</th><th>Sucursal</th><th>Reportó</th><th>A cargo</th><th>Estado</th><th></th></tr></thead><tbody>${rows.map((i) => `
      <tr class="${i.severity === 'alta' && i.status !== 'resuelta' ? 'overdue' : ''}">
        <td>${chip(`sev-${i.severity}`, i.severity)} <span class="ops-title">${esc(i.title)}</span>${i.description ? `<span class="ops-sub">${esc(i.description)}</span>` : ''}${i.resolution_notes ? `<span class="ops-sub">✔ ${esc(i.resolution_notes)}</span>` : ''}</td>
        <td>${esc(i.branch_name)}</td>
        <td>${esc(i.reported_by_name)}<span class="ops-sub">${esc(ago(i.created_at))}${i.hours_open != null && i.hours_open >= 24 ? ` · ${Math.round(i.hours_open / 24)} d abierta` : ''}</span></td>
        <td>${i.status === 'resuelta' ? esc(i.assigned_to_name || '—') : `<select class="ops-select" data-assign="${i.id}" data-branch="${i.branch_id}">${teamOptions(teams[i.branch_id] || [], i.assigned_to_user_id)}</select>`}</td>
        <td>${chip(`st-${i.status}`, { abierta: 'Abierta', en_proceso: 'En proceso', resuelta: 'Resuelta' }[i.status] || i.status)}${i.resolved_at ? `<span class="ops-sub">${esc(fmt(i.resolved_at))} · ${esc(i.resolved_by_name || '')}</span>` : ''}</td>
        <td><div class="ops-actions">
          ${i.status === 'abierta' ? `<button class="ops-btn" data-inc="${i.id}" data-status="en_proceso">En proceso</button>` : ''}
          ${i.status !== 'resuelta' ? `<button class="ops-btn primary" data-inc="${i.id}" data-resolve="${esc(i.title)}">Resolver</button>` : `<button class="ops-btn" data-inc="${i.id}" data-status="abierta">Reabrir</button>`}
        </div></td>
      </tr>`).join('')}</tbody></table>`;
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
    act(e.submitter, async () => {
      await api.post(`/ops/incidents/${id}/status`, { status: 'resuelta', resolution_notes: $('resolveNotes').value.trim() || null });
      closeModal('modalResolve');
    }, 'Incidencia resuelta.');
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
    act(e.submitter, async () => {
      try {
        await api.post('/ops/incidents', { branch_id: Number($('incBranch').value), title: $('incTitle').value.trim(), severity: $('incSev').value, description: $('incDesc').value.trim() || null });
        closeModal('modalIncident');
      } catch (err) { $('incError').textContent = err.message; $('incError').hidden = false; throw err; }
    }, 'Incidencia reportada.');
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
    const teams = {};
    for (const id of new Set(rows.map((r) => r.branch_id))) teams[id] = await team(id);
    const abierta = (t) => t.status === 'pendiente' || t.status === 'en_proceso';
    box.innerHTML = `<table class="ops-table"><thead><tr><th>Tarea</th><th>Sucursal</th><th>Asignada a</th><th>Vence</th><th>Estado</th><th></th></tr></thead><tbody>${rows.map((t) => `
      <tr class="${t.overdue ? 'overdue' : ''}">
        <td><span class="ops-title">${esc(t.title)}</span>${t.description ? `<span class="ops-sub">${esc(t.description)}</span>` : ''}<span class="ops-sub">creada por ${esc(t.created_by_name)} · ${esc(ago(t.created_at))}</span>${t.requires_photo || (t.photos || []).length ? `<span class="ops-task-photos">${t.requires_photo && !(t.photos || []).length ? '<span class="ops-chip st-pendiente"><i data-lucide="camera"></i> Pide foto</span>' : ''}${(t.photos || []).map((p, i) => `<button type="button" class="ops-btn" data-task-photo="/ops/tasks/${t.id}/photos/${p.id}"><i data-lucide="image"></i> Foto ${i + 1}</button>`).join('')}</span>` : ''}</td>
        <td>${esc(t.branch_name)}</td>
        <td>${abierta(t) ? `<select class="ops-select" data-task-assign="${t.id}">${teamOptions(teams[t.branch_id] || [], t.assigned_to_user_id)}</select>` : esc(t.assigned_to_name || '—')}</td>
        <td><span class="ops-due ${t.overdue ? 'overdue' : ''}">${t.due_date ? esc(fmt(t.due_date)) : 'Sin fecha'}</span>${t.overdue ? ' ' + chip('overdue', 'vencida') : ''}</td>
        <td>${chip(`st-${t.status}`, { pendiente: 'Pendiente', en_proceso: 'En proceso', hecha: 'Hecha', cancelada: 'Cancelada' }[t.status] || t.status)}${t.completed_at ? `<span class="ops-sub">${esc(fmt(t.completed_at))}</span>` : ''}</td>
        <td><div class="ops-actions">
          ${t.status === 'pendiente' ? `<button class="ops-btn" data-task="${t.id}" data-status="en_proceso">En proceso</button>` : ''}
          ${abierta(t) ? `<button class="ops-btn primary" data-task="${t.id}" data-status="hecha">Hecha</button><button class="ops-btn danger" data-task="${t.id}" data-status="cancelada">Cancelar</button>` : ''}
          ${abierta(t) ? `<button class="ops-btn" data-task-remind="${t.id}" title="Vuelve a avisar por push a quien le toca"><i data-lucide="bell"></i> Recordar</button>` : ''}
          ${user.role === 'admin' ? `<button class="ops-btn danger" data-task-delete="${t.id}">Eliminar</button>` : ''}
        </div></td>
      </tr>`).join('')}</tbody></table>`;
    await loadRecurringTasks();
  }
  $('taskStatus').addEventListener('change', loadTareas);
  $('taskOverdue').addEventListener('change', loadTareas);
  $('taskList').addEventListener('click', (e) => {
    const foto = e.target.closest('button[data-task-photo]');
    if (foto) { openProtectedPhoto(foto.dataset.taskPhoto); return; }
    const remind = e.target.closest('button[data-task-remind]');
    if (remind) {
      act(remind, () => api.post(`/ops/tasks/${remind.dataset.taskRemind}/remind`, {}), 'Recordatorio enviado.');
      return;
    }
    const del = e.target.closest('button[data-task-delete]');
    if (del) {
      if (!confirm('¿Eliminar esta tarea por completo? No queda ni como cancelada, se borra del historial.')) return;
      act(del, () => api.delete(`/ops/tasks/${del.dataset.taskDelete}`), 'Tarea eliminada.');
      return;
    }
    const b = e.target.closest('button[data-task]');
    if (!b) return;
    if (b.dataset.status === 'cancelada' && !confirm('¿Cancelar esta tarea?')) return;
    act(b, () => api.post(`/ops/tasks/${b.dataset.task}/status`, { status: b.dataset.status }), 'Tarea actualizada.');
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
    const chip = $('taskDueDate').value === isoDay(hoy) ? 'hoy' : $('taskDueDate').value === isoDay(manana) ? 'manana' : null;
    if (chip) document.querySelector(`.task-due-chip[data-due="${chip}"]`).classList.add('active');
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
    act(e.submitter, async () => {
      const dueDate = taskDueValue();
      if (dueDate && dueDate < new Date()) {
        const msg = 'La fecha de vencimiento ya pasó. Elige otra o deja "Sin fecha".';
        $('taskError').textContent = msg;
        $('taskError').hidden = false;
        throw new Error(msg);
      }
      const due = dueDate ? dueDate.toISOString() : null;
      try {
        await api.post('/ops/tasks', {
          branch_id: Number($('taskBranch').value), title: $('taskTitle').value.trim(),
          description: $('taskDescription').value.trim() || null,
          assigned_to_user_id: $('taskAssignee').value ? Number($('taskAssignee').value) : null, due_date: due,
          requires_photo: $('taskRequiresPhoto').checked,
        });
        closeModal('modalTask');
      } catch (err) { $('taskError').textContent = err.message; $('taskError').hidden = false; throw err; }
    }, 'Tarea creada.');
  });

  // ==========================================================================
  // Tareas recurrentes
  // ==========================================================================
  const frequencyLabel = (t) => {
    const horas = t.times.join(', ');
    return t.frequency === 'monthly' ? `Día ${t.day_of_month} de cada mes · ${horas}` : `Cada día · ${horas}`;
  };
  async function loadRecurringTasks() {
    const box = $('recurringTaskList');
    box.innerHTML = loading();
    let rows;
    try { rows = await api.get(`/ops/recurring-tasks${bq('?')}`); }
    catch (err) { box.innerHTML = empty('No se pudieron cargar las tareas recurrentes.'); return; }
    if (!rows.length) { box.innerHTML = empty('No hay tareas recurrentes todavía.'); return; }
    box.innerHTML = `<table class="ops-table"><thead><tr><th>Tarea</th><th>Sucursal</th><th>Repite</th><th>Estado</th><th></th></tr></thead><tbody>${rows.map((t) => `
      <tr>
        <td><span class="ops-title">${esc(t.title)}</span>${t.description ? `<span class="ops-sub">${esc(t.description)}</span>` : ''}</td>
        <td>${esc(t.branch_name || 'Todas las sucursales')}</td>
        <td>${esc(frequencyLabel(t))}</td>
        <td>${chip(t.active ? 'st-hecha' : 'st-cancelada', t.active ? 'Activa' : 'Pausada')}</td>
        <td><div class="ops-actions">
          <button class="ops-btn" data-rt="${t.id}" data-rt-action="toggle" data-rt-active="${t.active}">${t.active ? 'Pausar' : 'Reanudar'}</button>
          <button class="ops-btn danger" data-rt="${t.id}" data-rt-action="delete">Eliminar</button>
        </div></td>
      </tr>`).join('')}</tbody></table>`;
  }
  $('recurringTaskList').addEventListener('click', (e) => {
    const b = e.target.closest('button[data-rt]');
    if (!b) return;
    const id = b.dataset.rt;
    if (b.dataset.rtAction === 'toggle') {
      const active = b.dataset.rtActive === 'true';
      act(b, () => api.patch(`/ops/recurring-tasks/${id}`, { active: !active }), active ? 'Tarea recurrente pausada.' : 'Tarea recurrente reanudada.');
    } else if (b.dataset.rtAction === 'delete') {
      if (!confirm('¿Eliminar esta tarea recurrente? Ya no se va a volver a crear sola.')) return;
      act(b, () => api.delete(`/ops/recurring-tasks/${id}`), 'Tarea recurrente eliminada.');
    }
  });
  $('btnNewRecurringTask').addEventListener('click', () => {
    $('recurringTaskForm').reset();
    $('rtBranch').innerHTML = branchOptions(state.branch || user.branch_id, { includeAll: isGlobal });
    $('rtBranch').disabled = !isGlobal;
    $('rtDayOfMonthWrap').hidden = true;
    $('rtError').hidden = true;
    openModal('modalRecurringTask');
  });
  $('rtFrequency').addEventListener('change', () => {
    $('rtDayOfMonthWrap').hidden = $('rtFrequency').value !== 'monthly';
  });
  $('recurringTaskForm').addEventListener('submit', (e) => {
    e.preventDefault();
    act(e.submitter, async () => {
      const frequency = $('rtFrequency').value;
      const times = $('rtTimes').value.split(',').map((t) => t.trim()).filter(Boolean);
      const dayOfMonth = frequency === 'monthly' ? Number($('rtDayOfMonth').value) : null;
      if (frequency === 'monthly' && !dayOfMonth) { $('rtError').textContent = 'Indica el día del mes.'; $('rtError').hidden = false; throw new Error('día del mes'); }
      try {
        await api.post('/ops/recurring-tasks', {
          branch_id: $('rtBranch').value ? Number($('rtBranch').value) : null,
          title: $('rtTitle').value.trim(), description: $('rtDescription').value.trim() || null,
          frequency, times, day_of_month: dayOfMonth,
        });
        closeModal('modalRecurringTask');
      } catch (err) { $('rtError').textContent = err.message; $('rtError').hidden = false; throw err; }
    }, 'Tarea recurrente creada.');
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
    const labels = { open: 'Sin aprobar', approved: 'Aprobada', fulfilled: 'Entregada', cancelled: 'Cancelada' };
    box.innerHTML = `<table class="ops-table"><thead><tr><th>Insumo</th><th>Sucursal</th><th>Pidió</th><th>Estado</th><th></th></tr></thead><tbody>${rows.map((r) => `
      <tr>
        <td><span class="ops-title">${esc(r.item_name)}</span>${r.quantity_hint ? ` · ${esc(r.quantity_hint)}` : ''}${r.notes ? `<span class="ops-sub">${esc(r.notes)}</span>` : ''}</td>
        <td>${esc(r.branch_name)}</td>
        <td>${esc(r.requested_by_name)}<span class="ops-sub">${esc(ago(r.created_at))}</span></td>
        <td>${chip(`st-${r.status}`, labels[r.status] || r.status)}${r.approved_at ? `<span class="ops-sub">aprobó ${esc(r.approved_by_name || '')} · ${esc(fmt(r.approved_at))}</span>` : ''}${r.resolved_at ? `<span class="ops-sub">${esc(r.resolved_by_name || '')} · ${esc(fmt(r.resolved_at))}</span>` : ''}</td>
        <td><div class="ops-actions">
          ${r.status === 'open' ? `<button class="ops-btn primary" data-req="${r.id}" data-status="approved">Aprobar</button>` : ''}
          ${r.status === 'open' || r.status === 'approved' ? `<button class="ops-btn" data-req="${r.id}" data-status="fulfilled">Entregada</button><button class="ops-btn danger" data-req="${r.id}" data-status="cancelled">Cancelar</button>` : ''}
        </div></td>
      </tr>`).join('')}</tbody></table>`;
  }
  $('reqStatus').addEventListener('change', loadSolicitudes);
  $('reqList').addEventListener('click', (e) => {
    const b = e.target.closest('button[data-req]');
    if (!b) return;
    if (b.dataset.status === 'cancelled' && !confirm('¿Cancelar esta solicitud?')) return;
    act(b, () => api.post(`/ops/requests/${b.dataset.req}/status?status=${b.dataset.status}`, {}), 'Solicitud actualizada.');
  });

  // ==========================================================================
  // Traslados
  // ==========================================================================
  const canActOn = (branchId) => isGlobal || Number(user.branch_id) === Number(branchId);
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
    const labels = { requested: 'Por aprobar', approved: 'Por despachar', dispatched: 'En camino', received: 'Recibido', rejected: 'Rechazado', cancelled: 'Cancelado' };
    box.innerHTML = `<table class="ops-table"><thead><tr><th>Traslado</th><th>Insumos</th><th>Pidió</th><th>Estado</th><th></th></tr></thead><tbody>${rows.map((t) => {
      const items = t.items.map((i) => `${Number(i.quantity)} ${i.unit || ''} ${i.item_name}`.replace(/\s+/g, ' ').trim());
      const acts = [];
      if (t.status === 'requested' && canActOn(t.from_branch_id)) acts.push(`<button class="ops-btn primary" data-trf="${t.id}" data-act="approve">Aprobar</button><button class="ops-btn danger" data-trf="${t.id}" data-act="reject">Rechazar</button>`);
      if (t.status === 'approved' && canActOn(t.from_branch_id)) acts.push(`<button class="ops-btn primary" data-trf="${t.id}" data-act="dispatch">Despachar</button>`);
      if (t.status === 'dispatched' && canActOn(t.to_branch_id)) acts.push(`<button class="ops-btn primary" data-trf="${t.id}" data-act="receive">Recibir</button>`);
      if ((t.status === 'requested' || t.status === 'approved') && (isGlobal || t.requested_by_user_id === user.id)) acts.push(`<button class="ops-btn" data-trf="${t.id}" data-act="cancel">Cancelar</button>`);
      return `
      <tr>
        <td><span class="ops-title">#${t.id} · ${esc(t.from_branch_name)} → ${esc(t.to_branch_name)}</span>${t.notes ? `<span class="ops-sub">${esc(t.notes)}</span>` : ''}</td>
        <td>${items.slice(0, 4).map(esc).join('<br>')}${items.length > 4 ? `<span class="ops-sub">y ${items.length - 4} más</span>` : ''}</td>
        <td>${esc(t.requested_by_name)}<span class="ops-sub">${esc(ago(t.requested_at))}</span></td>
        <td>${chip(`st-${t.status}`, labels[t.status] || t.status)}<span class="ops-sub">${t.dispatched_at ? `salió ${esc(fmt(t.dispatched_at))}` : (t.approved_at ? `aprobado ${esc(fmt(t.approved_at))}` : '')}${t.received_at ? ` · llegó ${esc(fmt(t.received_at))}` : ''}</span></td>
        <td><div class="ops-actions">${acts.join('')}</div></td>
      </tr>`;
    }).join('')}</tbody></table>`;
  }
  $('trfStatus').addEventListener('change', loadTraslados);
  $('trfList').addEventListener('click', (e) => {
    const b = e.target.closest('button[data-trf]');
    if (!b) return;
    const a = b.dataset.act;
    let body = {};
    if (a === 'reject' || a === 'cancel') {
      const nota = prompt(a === 'reject' ? '¿Por qué se rechaza? (opcional)' : '¿Motivo de la cancelación? (opcional)');
      if (nota === null) return;
      body = { notes: nota.trim() || null };
    }
    const msgs = { approve: 'Traslado aprobado.', reject: 'Traslado rechazado.', dispatch: 'Traslado despachado: ya salió del inventario de origen.', receive: 'Traslado recibido: ya entró al inventario de destino.', cancel: 'Traslado cancelado.' };
    act(b, () => api.post(`/transfers/${b.dataset.trf}/${a}`, body), msgs[a]);
  });

  // ---- nuevo traslado ----
  let itemSearchTimer = null, itemSearchSeq = 0, pickedItem = null;
  $('btnNewTransfer').addEventListener('click', () => {
    $('transferForm').reset();
    state.transferItems = [];
    pickedItem = null;
    renderTransferItems();
    const own = user.branch_id ? String(user.branch_id) : '';
    $('trfFrom').innerHTML = branchOptions(state.branch || own);
    $('trfTo').innerHTML = branchOptions('');
    $('trfError').hidden = true;
    $('trfItemResults').hidden = true;
    openModal('modalTransfer');
  });
  $('trfItemSearch').addEventListener('input', () => {
    const q = $('trfItemSearch').value.trim();
    pickedItem = null;
    clearTimeout(itemSearchTimer);
    if (q.length < 2) { $('trfItemResults').hidden = true; return; }
    itemSearchTimer = setTimeout(async () => {
      const seq = ++itemSearchSeq;
      try {
        const items = await api.get(`/inventory/items?q=${encodeURIComponent(q)}`);
        if (seq !== itemSearchSeq) return;
        const box = $('trfItemResults');
        if (!items.length) { box.hidden = true; return; }
        box.innerHTML = items.map((i) => `<button type="button" data-id="${i.id}" data-name="${esc(i.name)}" data-unit="${esc(i.unit || '')}">${esc(i.name)} <small>${esc(i.unit || '')}</small></button>`).join('');
        box.hidden = false;
      } catch (e) { /* búsqueda silenciosa */ }
    }, 250);
  });
  $('trfItemResults').addEventListener('click', (e) => {
    const b = e.target.closest('button[data-id]');
    if (!b) return;
    pickedItem = { id: Number(b.dataset.id), name: b.dataset.name, unit: b.dataset.unit };
    $('trfItemSearch').value = b.dataset.name;
    $('trfItemResults').hidden = true;
    $('trfItemQty').focus();
  });
  $('btnAddTrfItem').addEventListener('click', () => {
    const qty = Number($('trfItemQty').value);
    if (!pickedItem) { utils.showToast('Elige un insumo de la lista.', 'error'); return; }
    if (!(qty > 0)) { utils.showToast('Escribe la cantidad.', 'error'); return; }
    state.transferItems = state.transferItems.filter((i) => i.id !== pickedItem.id).concat([{ ...pickedItem, qty }]);
    pickedItem = null;
    $('trfItemSearch').value = '';
    $('trfItemQty').value = '';
    renderTransferItems();
    $('trfItemSearch').focus();
  });
  function renderTransferItems() {
    $('trfItems').innerHTML = state.transferItems.map((i, idx) => `<li><span>${i.qty} ${esc(i.unit)} ${esc(i.name)}</span><button type="button" data-remove="${idx}">Quitar</button></li>`).join('');
  }
  $('trfItems').addEventListener('click', (e) => {
    const b = e.target.closest('button[data-remove]');
    if (!b) return;
    state.transferItems.splice(Number(b.dataset.remove), 1);
    renderTransferItems();
  });
  $('transferForm').addEventListener('submit', (e) => {
    e.preventDefault();
    const from = Number($('trfFrom').value), to = Number($('trfTo').value);
    const err = $('trfError');
    err.hidden = true;
    if (from === to) { err.textContent = 'Origen y destino no pueden ser la misma sucursal.'; err.hidden = false; return; }
    if (!state.transferItems.length) { err.textContent = 'Agrega al menos un insumo.'; err.hidden = false; return; }
    act(e.submitter, async () => {
      try {
        await api.post('/transfers/', {
          from_branch_id: from, to_branch_id: to, notes: $('trfNotes').value.trim() || null,
          items: state.transferItems.map((i) => ({ inventory_item_id: i.id, quantity: String(i.qty) })),
        });
        closeModal('modalTransfer');
        $('trfStatus').value = 'activos';
      } catch (ex) { err.textContent = ex.message; err.hidden = false; throw ex; }
    }, 'Traslado pedido: la sucursal de origen recibió el aviso.');
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
    const hoy = todayPanama();
    const hora = (t) => { if (!t) return ''; const [h, m] = t.split(':').map(Number); return `desde las ${(h % 12) || 12}:${String(m).padStart(2, '0')} ${h < 12 ? 'am' : 'pm'}`; };
    rows.sort((a, b) => a.expected_date.localeCompare(b.expected_date));
    box.innerHTML = `<table class="ops-table"><thead><tr><th>Cargamento</th><th>Sucursal</th><th>Fecha</th><th>Programó</th><th></th></tr></thead><tbody>${rows.map((e) => {
      const atrasado = e.expected_date < hoy, esHoy = e.expected_date === hoy;
      return `
      <tr class="${atrasado ? 'overdue' : ''}">
        <td><span class="ops-title">${esc(e.supplier_name || 'Proveedor sin nombre')}</span>${e.notes ? `<span class="ops-sub">${esc(e.notes)}</span>` : ''}</td>
        <td>${esc(e.branch_name)}</td>
        <td><span class="ops-due ${atrasado ? 'overdue' : ''}">${esc(e.expected_date)}</span> ${atrasado ? chip('overdue', 'atrasado') : (esHoy ? chip('st-pendiente', 'hoy') : '')}<span class="ops-sub">${esc(hora(e.time_from))}</span></td>
        <td>${esc(e.created_by_name || '—')}<span class="ops-sub">${esc(ago(e.created_at))}</span></td>
        <td><div class="ops-actions">
          <a class="ops-btn primary" href="/inventario?view=cargamentos">Recibir</a>
          ${canAdjust ? `<button class="ops-btn danger" data-exp="${e.id}">Cancelar</button>` : ''}
        </div></td>
      </tr>`;
    }).join('')}</tbody></table>`;
  }
  $('expList').addEventListener('click', (e) => {
    const b = e.target.closest('button[data-exp]');
    if (!b) return;
    if (!confirm('¿Cancelar este cargamento programado?')) return;
    act(b, () => api.post(`/inventory/expected-shipments/${b.dataset.exp}/cancel`, {}), 'Cargamento cancelado.');
  });

  // ==========================================================================
  // Arranque
  // ==========================================================================
  updateScope();
  setTab(state.tab);
  loadOverviewCounts();
  state.refreshTimer = setInterval(() => { if (!document.hidden) { if (state.tab === 'resumen') loadResumen(); else loadOverviewCounts(); } }, 90000);
  utils.renderIcons();
});
