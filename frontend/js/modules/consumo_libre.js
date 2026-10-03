/**
 * Farmhouse Link — Registrar consumo (tablero para la tablet)
 *
 * El operario ve todos los insumos como botones, con lo que queda de cada uno. Toca uno, pone
 * cuánto se gastó (o toca la última cantidad que anotó), lo agrega, y al guardar la existencia
 * se descuenta al momento: aquí en el tablero y en el listado de Abastecimiento.
 *
 * Datos: GET /inventory/consumption/board (catálogo + existencia + hoy + frecuencia),
 *        POST /inventory/consumption (guardar), GET/DELETE /inventory/consumption (historial).
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

  const state = {
    branchId: user.branch_id || null, branches: [],
    items: [], categories: [], hasData: false,
    chip: 'frecuentes', query: '',
    cart: new Map(),          // inventory_item_id -> { item, qty }
    sheetItem: null,
  };
  const numFmt = new Intl.NumberFormat('es-PA', { maximumFractionDigits: 3 });
  const num = (n) => numFmt.format(Number(n) || 0);
  const norm = (s) => String(s || '').normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase();

  // ---- sucursal ----
  if (isGlobal) {
    try { state.branches = (await api.get('/branches/')).filter((b) => b.active !== false && b.code !== 'CAT'); } catch (e) { state.branches = []; }
    const sel = $('branchSelect');
    sel.innerHTML = state.branches.map((b) => `<option value="${b.id}">${esc(b.name)}</option>`).join('');
    sel.hidden = false;
    const fromUrl = Number(new URLSearchParams(location.search).get('branch'));
    state.branchId = state.branches.some((b) => b.id === fromUrl) ? fromUrl : (state.branches[0] ? state.branches[0].id : null);
    sel.value = state.branchId || '';
    sel.addEventListener('change', () => {
      if (state.cart.size && !confirm('Tienes insumos sin guardar. ¿Cambiar de sucursal y descartarlos?')) { sel.value = state.branchId; return; }
      state.branchId = Number(sel.value); state.cart.clear(); renderCart(); updateScope(); loadBoard(); loadHistory();
    });
  }
  function updateScope() {
    const b = state.branches.find((x) => x.id === Number(state.branchId));
    $('conHeaderScope').textContent = b ? b.name : (user.branch ? user.branch.name : 'Sin sucursal');
  }
  updateScope();
  if (!state.branchId) {
    utils.showToast('Tu usuario no tiene una sucursal asignada.', 'error');
    $('board').innerHTML = '<div class="ops-empty">Tu usuario no tiene una sucursal asignada. Pide a un encargado que la configure.</div>';
    return;
  }

  // ---- tablero ----
  async function loadBoard() {
    $('board').innerHTML = '<div class="ops-loading">Cargando insumos…</div>';
    try {
      const data = await api.get(`/inventory/consumption/board?branch_id=${state.branchId}`);
      state.items = data.items;
      state.categories = data.categories;
      state.hasData = data.has_data;
      if (state.chip === 'frecuentes' && !state.items.some((i) => i.times_30d > 0)) state.chip = 'todos';
      renderChips();
      renderBoard();
    } catch (err) {
      $('board').innerHTML = `<div class="ops-empty">No se pudo cargar el catálogo. ${esc(err.message || '')}</div>`;
    }
  }

  function renderChips() {
    const frecuentes = state.items.filter((i) => i.times_30d > 0).length;
    const chips = [
      { key: 'frecuentes', label: 'Frecuentes', count: frecuentes },
      { key: 'todos', label: 'Todos', count: state.items.length },
      ...state.categories.map((c) => ({ key: `cat:${c}`, label: c, count: state.items.filter((i) => i.category === c).length })),
    ].filter((c) => c.key !== 'frecuentes' || c.count > 0);
    if (!chips.some((c) => c.key === state.chip)) state.chip = 'todos';
    $('chips').innerHTML = chips.map((c) => `<button type="button" class="con-chip ${c.key === state.chip ? 'active' : ''}" data-chip="${esc(c.key)}">${esc(c.label)}<small>${c.count}</small></button>`).join('');
  }
  $('chips').addEventListener('click', (e) => {
    const b = e.target.closest('button[data-chip]');
    if (!b) return;
    state.chip = b.dataset.chip;
    renderChips();
    renderBoard();
  });

  function visibleItems() {
    let rows = state.items;
    const q = norm(state.query.trim());
    if (q) {
      rows = rows.filter((i) => norm(i.name).includes(q) || norm(i.category).includes(q));
    } else if (state.chip === 'frecuentes') {
      rows = rows.filter((i) => i.times_30d > 0).slice().sort((a, b) => b.times_30d - a.times_30d || a.name.localeCompare(b.name));
    } else if (state.chip.startsWith('cat:')) {
      rows = rows.filter((i) => i.category === state.chip.slice(4));
    }
    return rows;
  }

  function stockChip(i) {
    if (!i.tracked || i.stock == null) return '<span class="con-card-stock no-data">Sin dato</span>';
    const s = Number(i.stock);
    const cls = s < 0 ? 'is-neg' : s === 0 ? 'is-zero' : i.below_min ? 'is-low' : '';
    const label = s < 0 ? `Falta ${num(-s)} ${esc(i.unit)}` : `Quedan ${num(s)} ${esc(i.unit)}`;
    return `<span class="con-card-stock ${cls}">${label}${i.below_min && s >= 0 ? ' · bajo mínimo' : ''}</span>`;
  }

  function renderBoard() {
    const rows = visibleItems();
    if (!state.items.length) { $('board').innerHTML = '<div class="ops-empty">No hay insumos en el catálogo todavía.</div>'; return; }
    if (!rows.length) { $('board').innerHTML = `<div class="ops-empty">Ningún insumo coincide con “${esc(state.query)}”.</div>`; return; }
    $('board').innerHTML = rows.map((i) => {
      const enCarrito = state.cart.get(i.inventory_item_id);
      const meta = [];
      if (Number(i.today_qty) > 0) meta.push(`<span class="today">Hoy: ${num(i.today_qty)} ${esc(i.unit)}</span>`);
      if (enCarrito) meta.push(`<span class="pending">Por guardar: ${num(enCarrito.qty)} ${esc(i.unit)}</span>`);
      return `
        <button type="button" class="con-card ${enCarrito ? 'in-cart' : ''}" data-id="${i.inventory_item_id}">
          <span class="con-card-name">${esc(i.name)}</span>
          ${i.category ? `<span class="con-card-cat">${esc(i.category)} · ${esc(i.unit)}</span>` : `<span class="con-card-cat">${esc(i.unit)}</span>`}
          ${stockChip(i)}
          ${meta.length ? `<span class="con-card-meta">${meta.join('')}</span>` : ''}
        </button>`;
    }).join('');
  }
  $('board').addEventListener('click', (e) => {
    const b = e.target.closest('button[data-id]');
    if (!b) return;
    const item = state.items.find((i) => i.inventory_item_id === Number(b.dataset.id));
    if (item) openSheet(item);
  });

  // ---- buscador ----
  let searchTimer = null;
  $('itemSearch').addEventListener('input', () => {
    state.query = $('itemSearch').value;
    $('btnClearSearch').hidden = !state.query;
    clearTimeout(searchTimer);
    searchTimer = setTimeout(renderBoard, 120);
  });
  $('itemSearch').addEventListener('keydown', (e) => {
    if (e.key !== 'Enter') return;
    e.preventDefault();
    const rows = visibleItems();
    if (rows.length === 1) openSheet(rows[0]);
  });
  $('btnClearSearch').addEventListener('click', () => {
    state.query = ''; $('itemSearch').value = ''; $('btnClearSearch').hidden = true; renderBoard(); $('itemSearch').focus();
  });

  // ---- hoja de cantidad ----
  function stepFor(item) {
    const u = norm(item.unit);
    return /kg|lb|litro|lt|galon|gal/.test(u) ? 0.5 : 1;
  }
  function openSheet(item) {
    state.sheetItem = item;
    const enCarrito = state.cart.get(item.inventory_item_id);
    $('sheetTitle').textContent = item.name;
    $('sheetUnit').textContent = item.unit;
    const p = $('sheetStock');
    p.className = '';
    if (!item.tracked || item.stock == null) p.textContent = 'Sin existencia cargada todavía: igual se registra el gasto.';
    else {
      const s = Number(item.stock);
      p.textContent = s < 0 ? `Según el sistema faltan ${num(-s)} ${item.unit} (avisa al encargado para revisar el inventario).` : `Quedan ${num(s)} ${item.unit}${item.below_min ? ` · bajo el mínimo de ${num(item.min_quantity)}` : ''}.`;
      p.className = s < 0 ? 'is-neg' : item.below_min ? 'is-low' : '';
    }
    const presets = [];
    if (item.last_qty != null) presets.push({ v: Number(item.last_qty), label: `Última: ${num(item.last_qty)}`, last: true });
    for (const v of [0.5, 1, 2, 5, 10]) if (!presets.some((x) => x.v === v)) presets.push({ v, label: num(v) });
    $('presets').innerHTML = presets.map((x) => `<button type="button" class="con-preset ${x.last ? 'last' : ''}" data-v="${x.v}">${esc(x.label)}</button>`).join('');
    $('sheetQty').value = enCarrito ? enCarrito.qty : (item.last_qty != null ? Number(item.last_qty) : '');
    $('btnSheetAdd').innerHTML = `<i data-lucide="${enCarrito ? 'check' : 'plus'}"></i> ${enCarrito ? 'Actualizar' : 'Agregar'}`;
    updateAfter();
    $('sheet').classList.add('active');
    utils.renderIcons();
    setTimeout(() => { $('sheetQty').focus(); $('sheetQty').select(); }, 50);
  }
  function closeSheet() { $('sheet').classList.remove('active'); state.sheetItem = null; }
  function updateAfter() {
    const item = state.sheetItem;
    const el = $('sheetAfter');
    el.className = 'con-sheet-after';
    const qty = Number($('sheetQty').value);
    if (!item || !(qty > 0) || !item.tracked || item.stock == null) { el.textContent = ''; return; }
    const after = Number(item.stock) - qty;
    el.textContent = after < 0 ? `Quedarían ${num(after)} ${item.unit}: más de lo que hay registrado.` : `Quedarían ${num(after)} ${item.unit}.`;
    if (after < 0) el.classList.add('is-neg');
  }
  $('sheetQty').addEventListener('input', updateAfter);
  $('sheetQty').addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); addFromSheet(); } });
  $('presets').addEventListener('click', (e) => {
    const b = e.target.closest('button[data-v]');
    if (!b) return;
    $('sheetQty').value = Number(b.dataset.v);
    updateAfter();
  });
  $('btnMinus').addEventListener('click', () => {
    const step = stepFor(state.sheetItem);
    const v = Math.max(0, Number((Number($('sheetQty').value || 0) - step).toFixed(3)));
    $('sheetQty').value = v || '';
    updateAfter();
  });
  $('btnPlus').addEventListener('click', () => {
    const step = stepFor(state.sheetItem);
    $('sheetQty').value = Number((Number($('sheetQty').value || 0) + step).toFixed(3));
    updateAfter();
  });
  function addFromSheet() {
    const item = state.sheetItem;
    const qty = Number($('sheetQty').value);
    if (!(qty > 0)) {
      // También escrito en la hoja misma: el aviso de abajo puede quedar tapado por el teclado.
      $('sheetAfter').textContent = 'Escribe cuánto se gastó (un número mayor que 0).';
      $('sheetAfter').className = 'con-sheet-after is-neg';
      utils.showToast('Escribe cuánto se gastó.', 'error');
      $('sheetQty').focus();
      return;
    }
    state.cart.set(item.inventory_item_id, { item, qty: Number(qty.toFixed(3)) });
    closeSheet();
    renderCart();
    renderBoard();
  }
  $('btnSheetAdd').addEventListener('click', addFromSheet);
  $('btnSheetCancel').addEventListener('click', closeSheet);
  $('btnSheetClose').addEventListener('click', closeSheet);
  $('sheet').addEventListener('click', (e) => { if (e.target === $('sheet')) closeSheet(); });
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && $('sheet').classList.contains('active')) closeSheet(); });

  // ---- barra de lo que está por guardar ----
  function renderCart() {
    const lines = [...state.cart.values()];
    $('cart').hidden = !lines.length;
    $('cartLines').innerHTML = lines.map((l) => `
      <span class="con-cart-line"><strong>${num(l.qty)} ${esc(l.item.unit)}</strong> ${esc(l.item.name)}
        <button type="button" data-remove="${l.item.inventory_item_id}" aria-label="Quitar ${esc(l.item.name)}">×</button></span>`).join('');
    $('btnSaveLabel').textContent = lines.length ? `Guardar (${lines.length})` : 'Guardar';
    $('btnSave').disabled = !lines.length;
    setCartError('');
  }
  function setCartError(msg) {
    $('cartError').textContent = msg;
    $('cartError').hidden = !msg;
  }
  $('cartLines').addEventListener('click', (e) => {
    const b = e.target.closest('button[data-remove]');
    if (!b) return;
    state.cart.delete(Number(b.dataset.remove));
    renderCart();
    renderBoard();
  });
  $('btnClearCart').addEventListener('click', () => {
    if (!state.cart.size || !confirm('¿Vaciar lo que está por guardar?')) return;
    state.cart.clear(); renderCart(); renderBoard();
  });

  $('btnSave').addEventListener('click', async () => {
    const btn = $('btnSave');
    if (!state.cart.size || btn.dataset.busy) return;
    btn.dataset.busy = '1';
    btn.disabled = true;
    $('btnSaveLabel').textContent = 'Guardando…';
    setCartError('');
    try {
      const res = await api.post('/inventory/consumption', {
        branch_id: Number(state.branchId), notes: $('notes').value.trim() || null,
        items: [...state.cart.values()].map((l) => ({ inventory_item_id: l.item.inventory_item_id, quantity: String(l.qty) })),
      });
      // Actualiza el tablero en el momento con la existencia que devolvió el servidor.
      for (const li of res.items) {
        const it = state.items.find((i) => i.inventory_item_id === li.inventory_item_id);
        if (!it) continue;
        it.tracked = true;
        if (li.stock_after != null) it.stock = li.stock_after;
        it.today_qty = Number(it.today_qty || 0) + Number(li.quantity);
        it.times_30d = (it.times_30d || 0) + 1;
        it.last_qty = li.quantity;
        it.below_min = it.min_quantity != null && Number(it.stock) < Number(it.min_quantity);
      }
      const negativos = res.items.filter((i) => i.stock_after != null && Number(i.stock_after) < 0).map((i) => i.item_name);
      utils.showToast(negativos.length
        ? `Guardado. Ojo: según el sistema ${negativos.join(', ')} ${negativos.length === 1 ? 'quedó' : 'quedaron'} en negativo. Avisa al encargado para revisar el inventario.`
        : `Gasto guardado: ${res.items.length} insumo${res.items.length === 1 ? '' : 's'}. Ya se descontó del inventario.`, negativos.length ? 'warning' : 'success');
      delete btn.dataset.busy;
      state.cart.clear();
      $('notes').value = '';
      renderCart();
      renderChips();
      renderBoard();
      loadHistory();
    } catch (err) {
      // Además del aviso, el error queda escrito en la barra: lo anotado sigue ahí para reintentar.
      const msg = `No se guardó. ${err.message || 'Prueba otra vez.'}`;
      delete btn.dataset.busy;
      renderCart();
      setCartError(msg);
      utils.showToast(msg, 'error');
    }
  });

  // ---- no perder lo anotado ----
  // Con insumos por guardar: la flecha de volver o recargar la página preguntan antes de borrarlos.
  let leaving = false;
  window.addEventListener('beforeunload', (e) => {
    if (leaving || !state.cart.size) return;
    e.preventDefault();
    e.returnValue = '';
  });
  document.addEventListener('click', (e) => {
    const a = e.target.closest('a[href]');
    if (!a || a.target === '_blank' || !state.cart.size) return;
    if (!confirm('Tienes insumos anotados sin guardar. Si sales ahora se pierden. ¿Salir igual?')) { e.preventDefault(); return; }
    leaving = true;
  });

  // ---- historial ----
  const ago = (iso) => {
    const mins = Math.max(0, Math.round((Date.now() - utils._parseServerDate(iso)) / 60000));
    if (mins < 60) return `hace ${mins} min`;
    if (mins < 60 * 24) return `hace ${Math.round(mins / 60)} h`;
    return utils.formatDateTime(iso);
  };
  async function loadHistory() {
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
            ${r.can_delete ? `<button type="button" class="ops-btn danger con-del-btn" data-del="${r.id}"><i data-lucide="trash-2"></i> Borrar</button>` : ''}
          </div>
          <ul class="con-record-items">${r.items.map((i) => `<li>${num(i.quantity)} ${esc(i.unit)} ${esc(i.item_name)}</li>`).join('')}</ul>
        </div>`).join('');
      utils.renderIcons();
    } catch (err) {
      box.innerHTML = '<div class="ops-empty">No se pudo cargar el historial.</div>';
    }
  }
  $('history').addEventListener('click', async (e) => {
    const b = e.target.closest('button[data-del]');
    if (!b || b.disabled || !confirm('¿Borrar este registro? Lo que se había descontado vuelve al inventario.')) return;
    b.disabled = true;
    b.textContent = 'Borrando…';
    try {
      await api.delete(`/inventory/consumption/${b.dataset.del}`);
      utils.showToast('Registro borrado. Lo descontado volvió al inventario.', 'success');
      await loadBoard();
      loadHistory();
    } catch (err) {
      utils.showToast(`No se pudo borrar. ${err.message || 'Prueba otra vez.'}`, 'error');
      b.disabled = false;
      b.textContent = 'Borrar';
    }
  });

  renderCart();
  await loadBoard();
  loadHistory();
  utils.renderIcons();
});
