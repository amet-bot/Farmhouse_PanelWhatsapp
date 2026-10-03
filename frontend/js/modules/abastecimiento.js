/**
 * Farmhouse Link — Abastecimiento
 *
 * Cinco pestañas: cuánto le queda a cada sucursal de cada insumo (una columna por sucursal),
 * mínimos y pares por sucursal, el pedido sugerido por proveedor según el ritmo de uso, las
 * órdenes de compra (cargamentos agendados con líneas) y los precios por proveedor.
 *
 * Sin datos todavía (ninguna sucursal cargó cargamentos ni conteos) la pantalla lo dice tal cual
 * y explica de dónde saldrán, en vez de mostrar ceros como si fueran reales.
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
  const canEdit = perms.includes('inventory.adjust');
  const isGlobal = user.role === 'admin' || (user.role === 'supervisor' && !user.branch_id);
  $('supGate').hidden = true;
  $('supMain').hidden = false;
  FarmhouseShell.fillUserHeader({ nameId: 'supAgentName', roleId: 'supAgentRole', avatarId: 'supAgentAvatar' }, user);

  const params = new URLSearchParams(location.search);
  const state = {
    tab: params.get('tab') || 'existencias',
    branchId: Number(params.get('branch')) || user.branch_id || null,
    branches: [], suppliers: [],
    settingsRows: [], settingsDirty: new Map(),
    suggested: null, orders: [],
  };
  const numFmt = new Intl.NumberFormat('es-PA', { maximumFractionDigits: 2 });
  const num = (n) => numFmt.format(Number(n) || 0);
  const money = (n) => `$${(Number(n) || 0).toFixed(2)}`;
  const fecha = (iso) => { const [y, m, d] = iso.split('-'); return `${d}/${m}/${y}`; };
  const hoyIso = () => { const d = new Date(); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`; };

  // ---- sucursales y proveedores ----
  try { state.branches = (await api.get('/branches/')).filter((b) => b.active !== false && b.code !== 'CAT'); } catch (e) { state.branches = []; }
  if (!isGlobal) state.branches = state.branches.filter((b) => b.id === user.branch_id);
  if (!state.branchId || !state.branches.some((b) => b.id === state.branchId)) state.branchId = state.branches[0]?.id || null;
  try { state.suppliers = await api.get('/inventory/suppliers?limit=200'); } catch (e) { state.suppliers = []; }
  $('supHeaderScope').textContent = isGlobal ? 'Todas las sucursales' : (user.branch ? user.branch.name : 'Sin sucursal');

  const branchSelects = ['minBranch', 'sugBranch', 'ordBranch'];
  branchSelects.forEach((id) => {
    const sel = $(id);
    const extra = id === 'ordBranch' && isGlobal ? '<option value="">Todas las sucursales</option>' : '';
    sel.innerHTML = extra + state.branches.map((b) => `<option value="${b.id}">${esc(b.name)}</option>`).join('');
    sel.value = id === 'ordBranch' && isGlobal ? '' : String(state.branchId || '');
    sel.disabled = !isGlobal;
    sel.addEventListener('change', () => {
      if (id !== 'ordBranch') { state.branchId = Number(sel.value); branchSelects.filter((x) => x !== 'ordBranch').forEach((x) => { $(x).value = String(state.branchId); }); }
      loadTab(state.tab);
    });
  });

  // ---- pestañas ----
  const views = { existencias: 'viewExistencias', minimos: 'viewMinimos', sugerido: 'viewSugerido', ordenes: 'viewOrdenes', precios: 'viewPrecios', arranque: 'viewArranque' };
  const loaded = new Set();
  // En celular la fila de pestañas se desplaza de lado: que la activa quede a la vista. Se repite
  // cuando aparecen los íconos y los contadores, que ensanchan las pestañas.
  function revealActiveTab() {
    const fila = $('supTabs');
    const activa = fila.querySelector('.ops-tab.active');
    if (!activa || fila.scrollWidth <= fila.clientWidth) return;
    const a = activa.getBoundingClientRect(), f = fila.getBoundingClientRect();
    if (a.left < f.left || a.right > f.right) fila.scrollLeft += a.left - f.left - 12;
  }
  function showTab(tab, { push = true } = {}) {
    if (!views[tab]) tab = 'existencias';
    state.tab = tab;
    document.querySelectorAll('#supTabs .ops-tab').forEach((b) => b.classList.toggle('active', b.dataset.tab === tab));
    revealActiveTab();
    Object.entries(views).forEach(([k, id]) => { $(id).hidden = k !== tab; });
    if (push) {
      const u = new URL(location.href);
      u.searchParams.set('tab', tab);
      if (state.branchId) u.searchParams.set('branch', state.branchId);
      history.replaceState(null, '', u);
    }
    if (!loaded.has(tab)) loadTab(tab);
  }
  $('supTabs').addEventListener('click', (e) => { const b = e.target.closest('.ops-tab'); if (b) showTab(b.dataset.tab); });
  $('btnRefresh').addEventListener('click', () => loadTab(state.tab));
  function loadTab(tab) {
    loaded.add(tab);
    ({ existencias: loadStock, minimos: loadSettings, sugerido: loadSuggested, ordenes: loadOrders, precios: loadPrices, arranque: loadSetup })[tab]();
  }

  // =========================================================================
  // Puesta en marcha: qué le falta a cada sucursal
  // =========================================================================
  const fechaCorta = (iso) => (iso ? utils._parseServerDate(iso).toLocaleDateString('es-PA', { day: 'numeric', month: 'short' }) : '');
  function setupStep(done, title, detail, actionLabel, href, warn) {
    return `
      <li class="sup-step ${done ? 'done' : ''}">
        <span class="sup-step-icon"><i data-lucide="${done ? 'check' : 'circle'}"></i></span>
        <div class="sup-step-text">
          <strong>${utils.escapeHtml(title)}</strong>
          <span>${detail}</span>
          ${warn ? `<span class="sup-step-warn">${warn}</span>` : ''}
        </div>
        <a class="${done ? 'inv-secondary-btn' : 'inv-primary-btn'} sup-step-btn" href="${href}">${utils.escapeHtml(actionLabel)}</a>
      </li>`;
  }
  async function loadSetup() {
    const box = $('setupBox');
    try {
      const data = await api.get('/supply/setup');
      const pendientes = data.branches.filter((b) => b.progress < b.total).length;
      $('countArranque').textContent = pendientes;
      $('countArranque').hidden = !pendientes;
      revealActiveTab();
      if (!data.branches.length) { box.innerHTML = '<div class="ops-empty">No hay sucursales para mostrar.</div>'; return; }
      box.innerHTML = `<div class="sup-setup-grid">${data.branches.map((b) => {
        const s = b.steps;
        const esc = utils.escapeHtml;
        const faltan = s.closing_sheet.missing_piece_size_count;
        const pasos = [
          setupStep(s.first_count.done, '1. Primer conteo completo',
            s.first_count.done ? `Hecho el ${fechaCorta(s.first_count.at)} · ${s.first_count.items} insumos contados.` : 'Contar todo lo que hay una vez. Es el punto de partida de la existencia.',
            s.first_count.done ? 'Ver conteos' : 'Hacer conteo', '/inventario?view=conteos'),
          setupStep(s.closing_sheet.done, '2. Hoja de cierre armada',
            s.closing_sheet.done ? `${s.closing_sheet.items} insumos en la hoja.` : 'Elegir los 15 a 25 insumos que se cuentan al cerrar cada turno.',
            s.closing_sheet.done ? 'Ver hoja' : 'Armar hoja', `/consumo?branch=${b.branch.id}`,
            faltan ? `Falta cuánto trae una pieza de ${faltan} insumo${faltan === 1 ? '' : 's'} (${esc(s.closing_sheet.missing_piece_size.slice(0, 4).join(', '))}${faltan > 4 ? '…' : ''}). Se carga solo la primera vez que alguien cuenta en piezas.` : ''),
          setupStep(s.minimums.done, '3. Mínimos cargados',
            s.minimums.done ? `${s.minimums.items} insumos con mínimo: avisan cuando bajan.` : 'Sin mínimos no hay aviso de stock bajo ni pedido sugerido fino.',
            s.minimums.done ? 'Ver mínimos' : 'Cargar mínimos', `/abastecimiento?tab=minimos&branch=${b.branch.id}`),
          setupStep(s.closings.done, '4. Cierres de turno esta semana',
            s.closings.done ? `${s.closings.last_week} cierre${s.closings.last_week === 1 ? '' : 's'} en ${data.recent_days} días · último ${fechaCorta(s.closings.last_at)}.` : (s.closings.last_at ? `Último cierre el ${fechaCorta(s.closings.last_at)}: nadie cerró turno esta semana.` : 'Todavía nadie ha cerrado turno con la hoja.'),
            'Ir a la hoja', `/consumo?branch=${b.branch.id}`),
        ];
        const pct = Math.round((b.progress / b.total) * 100);
        return `
          <article class="sup-setup-card ${b.progress === b.total ? 'is-done' : ''}">
            <header>
              <h3>${esc(b.branch.name)}</h3>
              <span class="sup-setup-count">${b.progress} de ${b.total} listos</span>
            </header>
            <div class="sup-setup-bar"><div style="width:${pct}%"></div></div>
            <ol class="sup-steps">${pasos.join('')}</ol>
          </article>`;
      }).join('')}</div>`;
      utils.renderIcons();
    } catch (err) {
      box.innerHTML = `<div class="ops-empty">No se pudo cargar la puesta en marcha. ${utils.escapeHtml(err.message || '')}</div>`;
    }
  }

  // =========================================================================
  // Existencias: cuánto le queda a cada sucursal
  // =========================================================================
  let stockTimer = null;
  $('stockSearch').addEventListener('input', () => { clearTimeout(stockTimer); stockTimer = setTimeout(loadStock, 250); });
  $('stockCategory').addEventListener('change', loadStock);
  $('stockOnlyLow').addEventListener('change', loadStock);

  function daysChip(cell) {
    if (cell.days_left == null) return '<span class="sup-days-chip none" title="Sin ritmo de uso todavía">sin ritmo</span>';
    const d = Number(cell.days_left);
    const cls = d < 2 ? 'bad' : d < 5 ? 'warn' : '';
    return `<span class="sup-days-chip ${cls}" title="Para cuántos días alcanza al ritmo de los últimos 14 días">${d < 1 ? '<1' : Math.round(d)} d</span>`;
  }

  async function loadStock() {
    const box = $('stockBox');
    box.innerHTML = '<div class="ops-loading">Cargando…</div>';
    const p = new URLSearchParams();
    if ($('stockSearch').value.trim()) p.set('q', $('stockSearch').value.trim());
    if ($('stockCategory').value) p.set('category', $('stockCategory').value);
    if ($('stockOnlyLow').checked) p.set('only_low', 'true');
    let data;
    try { data = await api.get(`/supply/stock?${p.toString()}`); }
    catch (err) { box.innerHTML = `<div class="ops-empty">${esc(err.message || 'No se pudieron cargar las existencias.')}</div>`; return; }

    const catSel = $('stockCategory');
    if (catSel.options.length <= 1 && data.categories.length) {
      catSel.innerHTML = '<option value="">Todas las categorías</option>' + data.categories.map((c) => `<option value="${esc(c)}">${esc(c)}</option>`).join('');
    }
    const conDatos = data.branches.filter((b) => b.has_data);
    $('stockNote').textContent = data.items_below_min ? `${data.items_below_min} insumo${data.items_below_min === 1 ? '' : 's'} bajo el mínimo en alguna sucursal.` : (conDatos.length ? 'Existencia según cargamentos, conteos, ventas y lo que el equipo anota en Registrar consumo.' : '');

    if (!data.branches.length) { box.innerHTML = '<div class="ops-empty">No hay sucursales activas.</div>'; return; }
    if (!conDatos.length) {
      box.innerHTML = `
        <div class="sup-empty">
          <i data-lucide="warehouse"></i>
          <h3>Todavía no hay datos de existencias</h3>
          <p>Cuando cada sucursal registre su primer conteo de inventario o reciba su primer cargamento, acá verás cuánto le queda de cada insumo, para cuántos días le alcanza y qué está bajo el mínimo.</p>
          <div class="sup-branch-pills">${data.branches.map((b) => `<span>${esc(b.name)} · sin datos</span>`).join('')}</div>
          <div class="sup-empty-actions">
            <a class="inv-primary-btn" href="/inventario?view=conteos"><i data-lucide="clipboard-check"></i> Hacer el conteo de arranque</a>
            <a class="inv-secondary-btn" href="/inventario?view=cargamentos"><i data-lucide="truck"></i> Registrar un cargamento</a>
          </div>
        </div>`;
      utils.renderIcons();
      return;
    }
    if (!data.rows.length) { box.innerHTML = '<div class="ops-empty">Ningún insumo coincide con el filtro.</div>'; return; }

    const head = data.branches.map((b) => `<th class="sup-col-branch">${esc(b.name)}${b.has_data ? '' : '<small>sin datos</small>'}</th>`).join('');
    let lastCat = null;
    const body = data.rows.map((r) => {
      const cat = r.category || 'Sin categoría';
      const catRow = cat !== lastCat ? `<tr class="sup-cat-row"><td colspan="${data.branches.length + 1}">${esc(cat)}</td></tr>` : '';
      lastCat = cat;
      const cells = data.branches.map((b) => {
        const c = r.cells[String(b.id)];
        if (!c) return '<td class="sup-cell no-data">sin datos</td>';
        const st = Number(c.stock);
        const cls = c.below_min ? 'is-low' : (st <= 0 ? 'is-zero' : '');
        const min = c.min_quantity != null ? `<span class="sup-min-hint">mín. ${num(c.min_quantity)}</span>` : '';
        return `<td class="sup-cell ${cls}"><span class="sup-qty">${num(st)} <small>${esc(r.unit)}</small></span>${daysChip(c)}${min}</td>`;
      }).join('');
      return `${catRow}<tr><td class="sup-col-item"><span class="sup-item-name">${esc(r.name)}</span></td>${cells}</tr>`;
    }).join('');
    box.innerHTML = `<div class="sup-matrix-wrap"><table class="sup-matrix"><thead><tr><th>Insumo</th>${head}</tr></thead><tbody>${body}</tbody></table></div>`;
  }

  // =========================================================================
  // Mínimos y pares
  // =========================================================================
  let minTimer = null;
  $('minSearch').addEventListener('input', () => { clearTimeout(minTimer); minTimer = setTimeout(renderSettings, 150); });

  async function loadSettings() {
    const box = $('minBox');
    if (!state.branchId) { box.innerHTML = '<div class="ops-empty">Tu usuario no tiene sucursal asignada.</div>'; return; }
    box.innerHTML = '<div class="ops-loading">Cargando…</div>';
    state.settingsDirty.clear();
    updateSaveBtn();
    try {
      const data = await api.get(`/supply/settings?branch_id=${state.branchId}`);
      state.settingsRows = data.rows;
      renderSettings();
    } catch (err) { box.innerHTML = `<div class="ops-empty">${esc(err.message || 'No se pudieron cargar los mínimos.')}</div>`; }
  }

  function supplierOptions(selected) {
    return '<option value="">—</option>' + state.suppliers.map((s) => `<option value="${s.id}" ${Number(selected) === s.id ? 'selected' : ''}>${esc(s.name)}</option>`).join('');
  }

  function renderSettings() {
    const box = $('minBox');
    const q = $('minSearch').value.trim().toLowerCase();
    const rows = state.settingsRows.filter((r) => !q || `${r.name} ${r.category || ''}`.toLowerCase().includes(q));
    if (!state.settingsRows.length) { box.innerHTML = '<div class="ops-empty">El catálogo de insumos está vacío.</div>'; return; }
    if (!rows.length) { box.innerHTML = '<div class="ops-empty">Ningún insumo coincide.</div>'; return; }
    const ro = canEdit ? '' : 'disabled';
    box.innerHTML = `
      <table class="ops-table">
        <thead><tr><th>Insumo</th><th class="num">Queda</th><th class="num">Uso/día</th><th class="num">Alcanza</th><th class="num">Mínimo</th><th class="num">Par</th><th>Proveedor</th><th class="num">Días entrega</th></tr></thead>
        <tbody>${rows.map((r) => {
          const d = state.settingsDirty.get(r.inventory_item_id) || {};
          const v = (k) => (k in d ? d[k] : r[k]);
          return `<tr class="${r.below_min ? 'is-low' : ''}" data-id="${r.inventory_item_id}">
            <td><strong>${esc(r.name)}</strong><br><small>${esc(r.category || '')} · ${esc(r.unit)}</small></td>
            <td class="num">${num(r.stock)}${Number(r.in_transit) > 0 ? `<br><small>+${num(r.in_transit)} en camino</small>` : ''}</td>
            <td class="num">${r.usage_per_day != null ? num(r.usage_per_day) : '—'}</td>
            <td class="num">${r.days_left != null ? `${Math.round(Number(r.days_left))} d` : '—'}</td>
            <td class="num"><input class="sup-min-input ${'min_quantity' in d ? 'changed' : ''}" data-field="min_quantity" type="number" min="0" step="0.001" inputmode="decimal" value="${v('min_quantity') ?? ''}" ${ro} /></td>
            <td class="num"><input class="sup-min-input ${'par_quantity' in d ? 'changed' : ''}" data-field="par_quantity" type="number" min="0" step="0.001" inputmode="decimal" value="${v('par_quantity') ?? ''}" ${ro} /></td>
            <td><select class="sup-min-select ${'supplier_id' in d ? 'changed' : ''}" data-field="supplier_id" ${ro}>${supplierOptions(v('supplier_id'))}</select></td>
            <td class="num"><input class="sup-min-input ${'lead_days' in d ? 'changed' : ''}" data-field="lead_days" type="number" min="0" max="60" step="1" inputmode="numeric" value="${v('lead_days') ?? ''}" ${ro} /></td>
          </tr>`;
        }).join('')}</tbody>
      </table>`;
  }
  $('minBox').addEventListener('input', (e) => {
    const inp = e.target.closest('[data-field]');
    if (!inp) return;
    const id = Number(inp.closest('tr').dataset.id);
    const d = state.settingsDirty.get(id) || {};
    d[inp.dataset.field] = inp.value === '' ? null : (inp.dataset.field === 'supplier_id' || inp.dataset.field === 'lead_days' ? Number(inp.value) : inp.value);
    state.settingsDirty.set(id, d);
    inp.classList.add('changed');
    updateSaveBtn();
  });
  function updateSaveBtn() {
    const btn = $('btnSaveSettings');
    btn.disabled = !canEdit || !state.settingsDirty.size;
    btn.innerHTML = `<i data-lucide="check"></i> Guardar cambios${state.settingsDirty.size ? ` (${state.settingsDirty.size})` : ''}`;
    utils.renderIcons();
  }
  $('btnSaveSettings').addEventListener('click', async () => {
    const payload = [];
    for (const [id, d] of state.settingsDirty) {
      const r = state.settingsRows.find((x) => x.inventory_item_id === id);
      const v = (k) => (k in d ? d[k] : r[k]);
      payload.push({ inventory_item_id: id, branch_id: state.branchId, min_quantity: v('min_quantity') ?? null, par_quantity: v('par_quantity') ?? null, supplier_id: v('supplier_id') || null, lead_days: v('lead_days') ?? null });
    }
    $('btnSaveSettings').disabled = true;
    try {
      const res = await api.put('/supply/settings', payload);
      utils.showToast(`Guardado: ${res.saved} insumo${res.saved === 1 ? '' : 's'}.`, 'success');
      loaded.delete('sugerido'); loaded.delete('existencias');
      loadSettings();
    } catch (err) { utils.showToast(err.message || 'No se pudo guardar.', 'error'); updateSaveBtn(); }
  });

  // =========================================================================
  // Pedido sugerido
  // =========================================================================
  $('sugDays').addEventListener('change', loadSuggested);

  async function loadSuggested() {
    const box = $('sugBox');
    if (!state.branchId) { box.innerHTML = '<div class="ops-empty">Tu usuario no tiene sucursal asignada.</div>'; return; }
    box.innerHTML = '<div class="ops-loading">Calculando…</div>';
    try {
      state.suggested = await api.get(`/supply/suggested?branch_id=${state.branchId}&cover_days=${$('sugDays').value}`);
    } catch (err) { box.innerHTML = `<div class="ops-empty">${esc(err.message || 'No se pudo calcular el pedido.')}</div>`; return; }
    const s = state.suggested;
    $('countSugerido').textContent = s.lines;
    $('countSugerido').hidden = !s.lines;
    $('sugNote').textContent = `Según el uso de los últimos ${s.usage_days} días, lo que ya viene en órdenes y lo que pidió el equipo.`;
    if (!s.groups.length) {
      box.innerHTML = `
        <div class="sup-empty">
          <i data-lucide="shopping-cart"></i>
          <h3>Nada que pedir por ahora</h3>
          <p>Se sugiere pedir cuando un insumo está bajo su mínimo, cuando no alcanza para cubrir los días elegidos más la entrega, o cuando el equipo lo solicitó. Si todavía no hay conteos ni mínimos cargados, la lista sale vacía.</p>
        </div>`;
      utils.renderIcons(); updateOrderBtn(); return;
    }
    box.innerHTML = `<p class="sup-totals"><span><strong>${s.lines}</strong> insumo${s.lines === 1 ? '' : 's'} para pedir</span><span>Costo estimado: <strong>${money(s.est_cost)}</strong></span></p>` +
      s.groups.map((g, gi) => `
        <div class="sup-group" data-group="${gi}">
          <div class="sup-group-head">
            <label class="sup-check"><input type="checkbox" class="sup-group-check" checked /></label>
            <strong>${esc(g.supplier_name)}</strong>
            <span class="sup-group-cost">${g.lines.length} línea${g.lines.length === 1 ? '' : 's'} · ${money(g.est_cost)}</span>
            ${canEdit ? `<button type="button" class="ops-btn primary" data-order-group="${gi}">Crear orden</button>` : ''}
          </div>
          <table class="ops-table">
            <thead><tr><th></th><th>Insumo</th><th class="num">Queda</th><th class="num">Alcanza</th><th class="num">En camino</th><th class="num">Pedir</th><th class="num">Costo est.</th></tr></thead>
            <tbody>${g.lines.map((l, li) => `
              <tr class="${l.reasons.includes('bajo el mínimo') ? 'is-low' : ''}" data-line="${li}">
                <td><input type="checkbox" class="sup-line-check" checked /></td>
                <td><strong>${esc(l.name)}</strong><br>${l.reasons.map((r) => `<span class="sup-reason ${r === 'bajo el mínimo' ? 'low' : ''}">${esc(r)}</span>`).join('')}</td>
                <td class="num">${num(l.stock)} ${esc(l.unit)}${l.min_quantity != null ? `<br><small>mín. ${num(l.min_quantity)}${l.par_quantity != null ? ` · par ${num(l.par_quantity)}` : ''}</small>` : ''}</td>
                <td class="num">${l.days_left != null ? `${Math.round(Number(l.days_left))} d` : '—'}</td>
                <td class="num">${Number(l.in_transit) > 0 ? num(l.in_transit) : '—'}</td>
                <td class="num"><input class="sup-qty-input" type="number" min="0" step="0.001" inputmode="decimal" value="${l.suggested_qty}" /> <small>${esc(l.unit)}</small></td>
                <td class="num">${l.est_cost != null ? money(l.est_cost) : '—'}</td>
              </tr>`).join('')}</tbody>
          </table>
        </div>`).join('');
    utils.renderIcons();
    updateOrderBtn();
  }
  function selectedLines(groupIdx = null) {
    const out = [];
    document.querySelectorAll('#sugBox .sup-group').forEach((gEl) => {
      const gi = Number(gEl.dataset.group);
      if (groupIdx !== null && gi !== groupIdx) return;
      if (!gEl.querySelector('.sup-group-check').checked) return;
      const g = state.suggested.groups[gi];
      gEl.querySelectorAll('tbody tr').forEach((tr) => {
        if (!tr.querySelector('.sup-line-check').checked) return;
        const l = g.lines[Number(tr.dataset.line)];
        const qty = Number(tr.querySelector('.sup-qty-input').value);
        if (qty > 0) out.push({ ...l, qty, supplier_id: g.supplier_id, supplier_name: g.supplier_name });
      });
    });
    return out;
  }
  function updateOrderBtn() {
    const n = selectedLines().length;
    $('btnCreateOrder').disabled = !canEdit || !n;
  }
  $('sugBox').addEventListener('change', (e) => {
    if (e.target.classList.contains('sup-group-check')) {
      e.target.closest('.sup-group').querySelectorAll('.sup-line-check').forEach((c) => { c.checked = e.target.checked; });
    }
    updateOrderBtn();
  });
  $('sugBox').addEventListener('click', (e) => {
    const b = e.target.closest('[data-order-group]');
    if (b) openOrderModal(selectedLines(Number(b.dataset.orderGroup)));
  });
  $('btnCreateOrder').addEventListener('click', () => openOrderModal(selectedLines()));

  // ---- modal de orden ----
  let orderDraft = [];
  function openOrderModal(lines) {
    if (!lines.length) { utils.showToast('Marca al menos un insumo con cantidad.', 'error'); return; }
    orderDraft = lines;
    const provs = [...new Set(lines.map((l) => l.supplier_id).filter(Boolean))];
    $('orderSupplier').innerHTML = '<option value="">Sin proveedor</option>' + state.suppliers.map((s) => `<option value="${s.id}">${esc(s.name)}</option>`).join('');
    $('orderSupplier').value = provs.length === 1 ? String(provs[0]) : '';
    const manana = new Date(); manana.setDate(manana.getDate() + 1);
    $('orderDate').value = `${manana.getFullYear()}-${String(manana.getMonth() + 1).padStart(2, '0')}-${String(manana.getDate()).padStart(2, '0')}`;
    $('orderDate').min = hoyIso();
    $('orderTime').value = '';
    $('orderNotes').value = '';
    $('orderError').hidden = true;
    const b = state.branches.find((x) => x.id === state.branchId);
    const total = lines.reduce((acc, l) => acc + (l.unit_cost != null ? l.qty * Number(l.unit_cost) : 0), 0);
    $('orderSummary').textContent = `${b ? b.name : ''} · ${lines.length} insumo${lines.length === 1 ? '' : 's'} · costo estimado ${money(total)}${provs.length > 1 ? ' · ojo: mezcla insumos de varios proveedores' : ''}`;
    $('orderLines').innerHTML = lines.map((l) => `<li><span>${esc(l.name)}</span><span>${num(l.qty)} ${esc(l.unit)}${l.unit_cost != null ? ` <small>· ${money(l.qty * Number(l.unit_cost))}</small>` : ''}</span></li>`).join('');
    $('modalOrder').classList.add('active');
    utils.renderIcons();
  }
  document.querySelectorAll('#modalOrder [data-close]').forEach((b) => b.addEventListener('click', () => $('modalOrder').classList.remove('active')));
  $('modalOrder').addEventListener('click', (e) => { if (e.target === $('modalOrder')) $('modalOrder').classList.remove('active'); });
  $('btnConfirmOrder').addEventListener('click', async () => {
    const btn = $('btnConfirmOrder');
    btn.disabled = true;
    $('orderError').hidden = true;
    try {
      const res = await api.post('/supply/orders', {
        branch_id: state.branchId,
        supplier_id: $('orderSupplier').value ? Number($('orderSupplier').value) : null,
        expected_date: $('orderDate').value,
        time_from: $('orderTime').value || null,
        notes: $('orderNotes').value.trim() || null,
        items: orderDraft.map((l) => ({ inventory_item_id: l.inventory_item_id, quantity: String(l.qty), unit_cost: l.unit_cost != null ? String(l.unit_cost) : null })),
      });
      $('modalOrder').classList.remove('active');
      utils.showToast(`Orden creada: ${res.items.length} insumo${res.items.length === 1 ? '' : 's'} para el ${fecha(res.expected_date)}.`, 'success');
      loaded.delete('ordenes');
      loadSuggested();
    } catch (err) {
      $('orderError').textContent = err.message || 'No se pudo crear la orden.';
      $('orderError').hidden = false;
    } finally { btn.disabled = false; }
  });

  // =========================================================================
  // Órdenes
  // =========================================================================
  $('ordStatus').addEventListener('change', loadOrders);
  async function loadOrders() {
    const box = $('ordBox');
    box.innerHTML = '<div class="ops-loading">Cargando…</div>';
    const p = new URLSearchParams({ status: $('ordStatus').value });
    if ($('ordBranch').value) p.set('branch_id', $('ordBranch').value);
    try { state.orders = await api.get(`/supply/orders?${p.toString()}`); }
    catch (err) { box.innerHTML = `<div class="ops-empty">${esc(err.message || 'No se pudieron cargar las órdenes.')}</div>`; return; }
    const pendientes = state.orders.filter((o) => o.status === 'pendiente').length;
    if ($('ordStatus').value === 'pendiente') { $('countOrdenes').textContent = pendientes; $('countOrdenes').hidden = !pendientes; }
    if (!state.orders.length) { box.innerHTML = '<div class="ops-empty">No hay órdenes con ese estado.</div>'; return; }
    const hoy = hoyIso();
    const st = { pendiente: 'Pendiente', recibido: 'Recibida', cancelado: 'Cancelada' };
    box.innerHTML = state.orders.map((o) => `
      <div class="sup-order ${o.status === 'pendiente' && o.expected_date < hoy ? 'is-late' : ''}">
        <div class="sup-order-head">
          <strong>${esc(o.supplier_name || 'Sin proveedor')}</strong>
          <span class="ops-chip st-${o.status}">${st[o.status] || o.status}</span>
          <span class="sup-order-meta">${esc(o.branch_name)} · llega ${fecha(o.expected_date)}${o.time_from ? ` desde ${o.time_from}` : ''}${o.created_by_name ? ` · por ${esc(o.created_by_name)}` : ''}${o.est_cost != null ? ` · ${money(o.est_cost)}` : ''}${o.notes ? ` · ${esc(o.notes)}` : ''}</span>
          ${o.status === 'pendiente' ? `<a class="ops-btn" href="/inventario?view=cargamentos">Ver en Inventario</a>` : ''}
          ${o.status === 'pendiente' && canEdit ? `<button type="button" class="ops-btn danger" data-cancel="${o.id}">Cancelar</button>` : ''}
        </div>
        ${o.items.length ? `<ul class="sup-order-items">${o.items.map((i) => `<li>${num(i.quantity)} ${esc(i.unit)} ${esc(i.item_name)}${i.unit_cost != null ? ` <small>· ${money(Number(i.quantity) * Number(i.unit_cost))}</small>` : ''}</li>`).join('')}</ul>` : '<p class="ops-hint">Agendado sin líneas (cargamento a mano).</p>'}
      </div>`).join('');
  }
  $('ordBox').addEventListener('click', async (e) => {
    const b = e.target.closest('[data-cancel]');
    if (!b || !confirm('¿Cancelar esta orden? Se quita de los cargamentos esperados de la sucursal.')) return;
    b.disabled = true;
    try { await api.post(`/inventory/expected-shipments/${b.dataset.cancel}/cancel`, {}); utils.showToast('Orden cancelada.', 'info'); loaded.delete('sugerido'); loadOrders(); }
    catch (err) { utils.showToast(err.message || 'No se pudo cancelar.', 'error'); b.disabled = false; }
  });

  // =========================================================================
  // Precios por proveedor
  // =========================================================================
  let priceTimer = null;
  $('priceSearch').addEventListener('input', () => { clearTimeout(priceTimer); priceTimer = setTimeout(loadPrices, 250); });
  $('priceSupplier').addEventListener('change', loadPrices);
  $('priceDays').addEventListener('change', loadPrices);
  async function loadPrices() {
    const box = $('priceBox');
    box.innerHTML = '<div class="ops-loading">Cargando…</div>';
    const p = new URLSearchParams({ days: $('priceDays').value });
    if ($('priceSearch').value.trim()) p.set('q', $('priceSearch').value.trim());
    if ($('priceSupplier').value) p.set('supplier_id', $('priceSupplier').value);
    let data;
    try { data = await api.get(`/supply/prices?${p.toString()}`); }
    catch (err) { box.innerHTML = `<div class="ops-empty">${esc(err.message || 'No se pudieron cargar los precios.')}</div>`; return; }
    const sel = $('priceSupplier');
    if (sel.options.length <= 1 && data.suppliers.length) {
      sel.innerHTML = '<option value="">Todos los proveedores</option>' + data.suppliers.map((s) => `<option value="${s.id}">${esc(s.name)}</option>`).join('');
    }
    if (!data.rows.length) {
      box.innerHTML = `
        <div class="sup-empty">
          <i data-lucide="tags"></i>
          <h3>Sin precios registrados en este período</h3>
          <p>Los precios salen de los cargamentos recibidos con costo por unidad y proveedor. A medida que se reciban con factura, acá verás qué cobra cada proveedor por cada insumo y quién tiene el mejor precio.</p>
        </div>`;
      utils.renderIcons(); return;
    }
    box.innerHTML = `
      <table class="ops-table">
        <thead><tr><th>Insumo</th><th>Proveedor</th><th class="num">Último precio</th><th class="num">Promedio</th><th class="num">Mín. / Máx.</th><th class="num">Compras</th><th class="num">Comprado</th></tr></thead>
        <tbody>${data.rows.map((r) => `
          <tr>
            <td><strong>${esc(r.name)}</strong><br><small>${esc(r.category || '')} · por ${esc(r.unit)}</small></td>
            <td>${esc(r.supplier_name)}${r.suppliers_for_item > 1 ? (r.best_price ? ' <span class="sup-best">mejor precio</span>' : ` <span class="sup-worse">+${r.vs_best_pct}% vs. el mejor</span>`) : ''}</td>
            <td class="num"><strong>$${Number(r.last_cost).toFixed(4)}</strong><br><small>${r.last_date ? utils.formatDateTime(r.last_date).split(' ')[0] : ''}</small></td>
            <td class="num">${r.avg_cost != null ? `$${Number(r.avg_cost).toFixed(4)}` : '—'}</td>
            <td class="num">$${Number(r.min_cost).toFixed(2)} / $${Number(r.max_cost).toFixed(2)}</td>
            <td class="num">${r.purchases}</td>
            <td class="num">${num(r.quantity)} ${esc(r.unit)}<br><small>${money(r.amount)}</small></td>
          </tr>`).join('')}</tbody>
      </table>`;
  }

  // ---- arranque ----
  showTab(state.tab, { push: false });
  // El contador de "Puesta en marcha" se ve desde cualquier pestaña: cuántas sucursales tienen pasos pendientes.
  if (state.tab !== 'arranque') loadSetup();
  setTimeout(revealActiveTab, 400);
  if (state.tab !== 'sugerido' && state.branchId) { loaded.add('sugerido'); loadSuggested(); }   // el contador de la pestaña
  if (state.tab !== 'ordenes') { loaded.add('ordenes'); loadOrders(); }
  utils.renderIcons();
});
