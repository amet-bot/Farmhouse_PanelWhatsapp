/**
 * Farmhouse Link — Registrar consumo
 *
 * El equipo anota cuánto se usó de cada insumo (5 kg de pollo, 2 rollos de papel). Al guardar
 * descuenta la existencia de la sucursal. Pensado para la tablet de cocina: buscar, cantidad,
 * agregar, guardar. Debajo, lo registrado hoy y ayer, con la existencia que quedó de cada cosa.
 */
document.addEventListener('DOMContentLoaded', async () => {
  const $ = (id) => document.getElementById(id);
  const esc = (s) => utils.escapeHtml(s ?? '');

  FarmhouseShell.initTheme();
  FarmhouseShell.initLogout({ redirectTo: '/' });
  window.addEventListener('auth:unauthorized', () => { window.location.href = '/'; });

  const user = await auth.checkSession();
  if (!user) { window.location.href = '/'; return; }
  const isGlobal = user.role === 'admin' || (user.role === 'supervisor' && !user.branch_id);
  $('conGate').hidden = true;
  $('conMain').hidden = false;
  FarmhouseShell.fillUserHeader({ nameId: 'conAgentName', roleId: 'conAgentRole', avatarId: 'conAgentAvatar' }, user);

  const state = { branchId: user.branch_id || null, branches: [], lines: [], picked: null };
  const numFmt = new Intl.NumberFormat('es-PA', { maximumFractionDigits: 3 });
  const num = (n) => numFmt.format(Number(n) || 0);

  // ---- sucursal ----
  if (isGlobal) {
    try { state.branches = (await api.get('/branches/')).filter((b) => b.active !== false && b.code !== 'CAT'); } catch (e) { state.branches = []; }
    const sel = $('branchSelect');
    sel.innerHTML = state.branches.map((b) => `<option value="${b.id}">${esc(b.name)}</option>`).join('');
    sel.hidden = false;
    state.branchId = state.branches[0] ? state.branches[0].id : null;
    sel.value = state.branchId || '';
    sel.addEventListener('change', () => { state.branchId = Number(sel.value); updateScope(); loadHistory(); });
  }
  function updateScope() {
    const b = state.branches.find((x) => x.id === Number(state.branchId));
    $('conHeaderScope').textContent = b ? b.name : (user.branch ? user.branch.name : 'Sin sucursal');
  }
  updateScope();
  if (!state.branchId) {
    utils.showToast('Tu usuario no tiene una sucursal asignada.', 'error');
    $('btnAddItem').disabled = true;
  }

  // ---- buscar e ir agregando ----
  let searchTimer = null, searchSeq = 0;
  $('itemSearch').addEventListener('input', () => {
    const q = $('itemSearch').value.trim();
    state.picked = null;
    $('pickedHint').hidden = true;
    clearTimeout(searchTimer);
    if (q.length < 2) { $('itemResults').hidden = true; return; }
    searchTimer = setTimeout(async () => {
      const seq = ++searchSeq;
      try {
        const items = await api.get(`/inventory/items?q=${encodeURIComponent(q)}&limit=10`);
        if (seq !== searchSeq) return;
        const box = $('itemResults');
        if (!items.length) { box.hidden = true; return; }
        box.innerHTML = items.map((i) => `<button type="button" data-id="${i.id}" data-name="${esc(i.name)}" data-unit="${esc(i.unit || '')}">${esc(i.name)} <small>${esc(i.unit || '')}${i.category ? ` · ${esc(i.category)}` : ''}</small></button>`).join('');
        box.hidden = false;
      } catch (e) { /* búsqueda silenciosa */ }
    }, 200);
  });
  $('itemResults').addEventListener('click', (e) => {
    const b = e.target.closest('button[data-id]');
    if (!b) return;
    state.picked = { id: Number(b.dataset.id), name: b.dataset.name, unit: b.dataset.unit };
    $('itemSearch').value = b.dataset.name;
    $('itemResults').hidden = true;
    $('pickedHint').textContent = `Cantidad en ${b.dataset.unit || 'su unidad'}.`;
    $('pickedHint').hidden = false;
    $('itemQty').focus();
  });
  document.addEventListener('click', (e) => { if (!e.target.closest('.con-picker')) $('itemResults').hidden = true; });

  function addLine() {
    const qty = Number($('itemQty').value);
    if (!state.picked) { utils.showToast('Elige un insumo de la lista.', 'error'); $('itemSearch').focus(); return; }
    if (!(qty > 0)) { utils.showToast('Escribe la cantidad.', 'error'); $('itemQty').focus(); return; }
    const existing = state.lines.find((l) => l.id === state.picked.id);
    if (existing) existing.qty = Number((existing.qty + qty).toFixed(3));
    else state.lines.push({ ...state.picked, qty });
    state.picked = null;
    $('itemSearch').value = '';
    $('itemQty').value = '';
    $('pickedHint').hidden = true;
    renderLines();
    $('itemSearch').focus();
  }
  $('btnAddItem').addEventListener('click', addLine);
  $('itemQty').addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); addLine(); } });

  function renderLines() {
    $('lines').innerHTML = state.lines.map((l, i) => `
      <li>
        <span><strong>${esc(l.name)}</strong><br><small>${esc(l.unit)}</small></span>
        <input type="number" min="0.001" step="0.001" inputmode="decimal" value="${l.qty}" data-idx="${i}" aria-label="Cantidad de ${esc(l.name)}" />
        <button type="button" data-remove="${i}">Quitar</button>
      </li>`).join('');
    $('linesEmpty').hidden = state.lines.length > 0;
    $('btnSave').disabled = !state.lines.length || !state.branchId;
  }
  $('lines').addEventListener('click', (e) => {
    const b = e.target.closest('button[data-remove]');
    if (!b) return;
    state.lines.splice(Number(b.dataset.remove), 1);
    renderLines();
  });
  $('lines').addEventListener('change', (e) => {
    const inp = e.target.closest('input[data-idx]');
    if (!inp) return;
    const v = Number(inp.value);
    if (v > 0) state.lines[Number(inp.dataset.idx)].qty = v; else renderLines();
  });

  // ---- guardar ----
  $('btnSave').addEventListener('click', async () => {
    const btn = $('btnSave');
    btn.disabled = true;
    try {
      const res = await api.post('/inventory/consumption', {
        branch_id: Number(state.branchId), notes: $('notes').value.trim() || null,
        items: state.lines.map((l) => ({ inventory_item_id: l.id, quantity: String(l.qty) })),
      });
      const negativos = res.items.filter((i) => i.stock_after != null && Number(i.stock_after) < 0).map((i) => i.item_name);
      utils.showToast(negativos.length ? `Guardado. Ojo: ${negativos.join(', ')} queda en negativo (falta cargar el arranque de inventario).` : `Consumo guardado: ${res.items.length} insumo${res.items.length === 1 ? '' : 's'}.`, negativos.length ? 'warning' : 'success');
      state.lines = [];
      $('notes').value = '';
      renderLines();
      loadHistory();
    } catch (err) {
      utils.showToast(err.message || 'No se pudo guardar el consumo.', 'error');
      btn.disabled = false;
    }
  });

  // ---- historial ----
  const ago = (iso) => {
    const mins = Math.max(0, Math.round((Date.now() - utils._parseServerDate(iso)) / 60000));
    if (mins < 60) return `hace ${mins} min`;
    if (mins < 60 * 24) return `hace ${Math.round(mins / 60)} h`;
    return utils.formatDateTime(iso);
  };
  async function loadHistory() {
    if (!state.branchId) return;
    const box = $('history');
    box.innerHTML = '<div class="ops-loading">Cargando…</div>';
    const hoy = new Date();
    const ayer = new Date(hoy); ayer.setDate(ayer.getDate() - 1);
    const iso = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
    try {
      const rows = await api.get(`/inventory/consumption?branch_id=${state.branchId}&date_from=${iso(ayer)}&date_to=${iso(hoy)}&limit=50`);
      if (!rows.length) { box.innerHTML = '<div class="ops-empty">Nada registrado hoy ni ayer.</div>'; return; }
      box.innerHTML = rows.map((r) => `
        <div class="con-record">
          <div class="con-record-head">
            <span><strong>${esc(r.recorded_by_name)}</strong> · ${esc(ago(r.occurred_at))}${r.notes ? ` · ${esc(r.notes)}` : ''}${r.total_cost != null ? ` · $${Number(r.total_cost).toFixed(2)}` : ''}</span>
            ${r.can_delete ? `<button type="button" class="ops-btn danger" data-del="${r.id}">Borrar</button>` : ''}
          </div>
          <ul class="con-record-items">${r.items.map((i) => `<li class="${i.stock_after != null && Number(i.stock_after) < 0 ? 'neg' : ''}">${num(i.quantity)} ${esc(i.unit)} ${esc(i.item_name)}${i.stock_after != null ? ` <small>· quedan ${num(i.stock_after)}</small>` : ''}</li>`).join('')}</ul>
        </div>`).join('');
      utils.renderIcons();
    } catch (err) {
      box.innerHTML = '<div class="ops-empty">No se pudo cargar el historial.</div>';
    }
  }
  $('history').addEventListener('click', async (e) => {
    const b = e.target.closest('button[data-del]');
    if (!b || !confirm('¿Borrar este registro de consumo? La existencia vuelve a subir.')) return;
    b.disabled = true;
    try { await api.delete(`/inventory/consumption/${b.dataset.del}`); utils.showToast('Registro borrado.', 'info'); loadHistory(); }
    catch (err) { utils.showToast(err.message || 'No se pudo borrar.', 'error'); b.disabled = false; }
  });

  renderLines();
  loadHistory();
  utils.renderIcons();
});
