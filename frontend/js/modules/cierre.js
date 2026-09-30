/**
 * Farmhouse Link — Cierre de turno
 *
 * La forma simple de llevar el gasto: siempre la misma lista de insumos (la arma el encargado
 * una vez), el operario escribe cuánto QUEDA de cada uno mirando el estante, y el sistema
 * calcula solo lo que se gastó. Un número por renglón, un botón.
 *
 * Datos: GET /inventory/closing-sheet (la hoja), POST /inventory/closing-sheet (cerrar),
 *        PUT /inventory/closing-sheet/config (encargados), GET /inventory/closing-sheet/history.
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
  $('cieGate').hidden = true;
  $('cieMain').hidden = false;
  FarmhouseShell.fillUserHeader({ nameId: 'cieAgentName', roleId: 'cieAgentRole', avatarId: 'cieAgentAvatar' }, user);

  const state = { branchId: user.branch_id || null, branches: [], sheet: null, values: new Map(), catalog: [], chosen: new Set() };
  const numFmt = new Intl.NumberFormat('es-PA', { maximumFractionDigits: 3 });
  const num = (n) => numFmt.format(Number(n) || 0);
  const SHORT_UNITS = { gramos: 'g', gramo: 'g', kilogramo: 'kg', kilogramos: 'kg', kilo: 'kg', kilos: 'kg', mililitros: 'ml', mililitro: 'ml', litro: 'L', litros: 'L', unidad: 'u', unidades: 'u', libra: 'lb', libras: 'lb' };
  const shortUnit = (u) => SHORT_UNITS[norm(u)] || String(u || '').slice(0, 5);
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
      if (state.values.size && !confirm('Tienes cantidades sin guardar. ¿Cambiar de sucursal y descartarlas?')) { sel.value = state.branchId; return; }
      state.branchId = Number(sel.value); state.values.clear(); updateScope(); showSheet(); loadSheet(); loadHistory();
    });
  }
  function updateScope() {
    const b = state.branches.find((x) => x.id === Number(state.branchId));
    $('cieHeaderScope').textContent = b ? b.name : (user.branch ? user.branch.name : 'Sin sucursal');
  }
  updateScope();
  if (!state.branchId) {
    $('sheetBox').innerHTML = '<div class="ops-empty">Tu usuario no tiene una sucursal asignada. Pide a un encargado que la configure.</div>';
    return;
  }

  const whenLabel = (iso) => {
    if (!iso) return '';
    const d = utils._parseServerDate(iso);
    const hoy = new Date(); hoy.setHours(0, 0, 0, 0);
    const dia = new Date(d); dia.setHours(0, 0, 0, 0);
    const diff = Math.round((hoy - dia) / 86400000);
    const hora = d.toLocaleTimeString('es-PA', { hour: '2-digit', minute: '2-digit' });
    if (diff === 0) return `hoy ${hora}`;
    if (diff === 1) return `ayer ${hora}`;
    return `hace ${diff} días`;
  };

  // ---- la hoja ----
  async function loadSheet() {
    $('sheetBox').innerHTML = '<div class="ops-loading">Cargando la hoja…</div>';
    $('bar').hidden = true;
    try {
      state.sheet = await api.get(`/inventory/closing-sheet?branch_id=${state.branchId}`);
    } catch (err) {
      $('sheetBox').innerHTML = `<div class="ops-empty">No se pudo cargar la hoja. ${esc(err.message || '')}</div>`;
      return;
    }
    const s = state.sheet;
    $('btnConfig').hidden = !s.can_configure;
    $('cieLast').textContent = s.last_closing ? `Último cierre: ${whenLabel(s.last_closing.at)} por ${s.last_closing.by} (${s.last_closing.items} insumo${s.last_closing.items === 1 ? '' : 's'}).` : 'Todavía no hay cierres en esta sucursal: el primero deja la existencia de arranque.';
    $('cieFoot').hidden = !s.configured;
    if (!s.configured) {
      $('sheetBox').innerHTML = `
        <div class="cie-empty">
          <h3>Esta sucursal todavía no tiene su hoja</h3>
          <p>${s.can_configure ? 'Elige los 15 a 25 insumos que se cuentan al cerrar cada turno. Después el equipo solo escribe cuánto queda.' : 'Pide a un encargado que arme la hoja de cierre desde esta misma pantalla.'}</p>
          ${s.can_configure ? '<button type="button" class="inv-primary-btn" id="btnConfigEmpty"><i data-lucide="list-checks"></i> Armar la hoja</button>' : ''}
        </div>`;
      if (s.can_configure) $('btnConfigEmpty').addEventListener('click', openConfig);
      utils.renderIcons();
      return;
    }
    const groups = [];
    for (const it of s.items) {
      const key = it.category || 'Otros';
      let g = groups.find((x) => x.key === key);
      if (!g) { g = { key, items: [] }; groups.push(g); }
      g.items.push(it);
    }
    $('sheetBox').innerHTML = groups.map((g) => `
      <div class="cie-group">
        <p class="cie-group-title">${esc(g.key)}</p>
        <div class="cie-rows">
          ${g.items.map((it) => {
            const prev = it.last_counted_qty != null
              ? `Última vez: ${num(it.last_counted_qty)} ${esc(it.unit)} (${esc(whenLabel(it.last_counted_at))})${Number(it.received_since) > 0 ? ` · <span class="in">llegaron ${num(it.received_since)}</span>` : ''}${it.last_used != null && Number(it.last_used) > 0 ? ` · <span class="used">se usaron ${num(it.last_used)}</span>` : ''}`
              : 'Primera vez que se cuenta';
            const v = state.values.get(it.inventory_item_id) || {};
            const fmt = (n) => (n == null ? '' : Number(Number(n).toFixed(3)));
            return `
              <div class="cie-row ${rowClass(v)}" data-id="${it.inventory_item_id}">
                <span><span class="cie-row-name">${esc(it.name)}</span><span class="cie-row-prev">${prev}</span><span class="cie-row-now" data-now="${it.inventory_item_id}">${rowNote(it, v)}</span></span>
                <span class="cie-fields">
                  <span class="cie-field">
                    <span class="cie-field-label">Queda</span>
                    <input type="number" min="0" step="0.001" inputmode="decimal" value="${fmt(v.left)}" data-id="${it.inventory_item_id}" data-kind="left" class="${v.source === 'pieces' ? 'derived' : ''}" aria-label="Cuánto queda de ${esc(it.name)}" />
                    <span class="cie-unit">${esc(shortUnit(it.unit))}</span>
                  </span>
                  ${it.unit_family !== 'unidad' ? `
                  <span class="cie-field">
                    <span class="cie-field-label">Piezas <small>(opcional)</small></span>
                    <input type="number" min="0" step="0.5" inputmode="decimal" value="${fmt(v.pieces)}" data-id="${it.inventory_item_id}" data-kind="pieces" aria-label="Cuántas piezas enteras quedan de ${esc(it.name)}" />
                    <span class="cie-unit">${it.piece_size ? `×${num(it.piece_size)}${pieceUnit(it)}` : 'pzas'}</span>
                  </span>` : ''}
                </span>
                ${it.unit_family !== 'unidad' && !it.piece_size ? `
                <span class="cie-psize" data-psize="${it.inventory_item_id}" ${v.source === 'pieces' ? '' : 'hidden'}>
                  <span>¿Cuánto trae <strong>1 pieza</strong>? (bolsa, bandeja, botella)</span>
                  <span class="cie-field cie-psize-field">
                    <input type="number" min="0" step="1" inputmode="decimal" value="${fmt(v.pieceSize)}" data-id="${it.inventory_item_id}" data-kind="psize" aria-label="Cuánto trae una pieza de ${esc(it.name)}" />
                    <span class="cie-unit">${pieceUnit(it)}</span>
                  </span>
                </span>` : ''}
              </div>`;
          }).join('')}
        </div>
      </div>`).join('');
    $('bar').hidden = false;
    updateBar();
    utils.renderIcons();
  }

  // Lo que había antes de este cierre: la última vez más lo que llegó. Null si nunca se contó.
  const baseOf = (it) => (it.last_counted_qty == null ? null : Number(it.last_counted_qty) + Number(it.received_since || 0));
  const rowClass = (v) => (v.left != null ? 'filled' : '');
  const pieceUnit = (it) => (it.unit_family === 'volumen' ? 'ml' : 'g');
  // Piezas enteras → unidad del insumo: piezas × (g o ml de una pieza) / (g o ml de la unidad).
  // El tamaño sale del insumo; si no lo tiene, del que escribió el operario en el renglón.
  function fromPieces(it, pieces, learned) {
    const size = Number(it.piece_size) || learned || null;
    return {
      pieces, source: 'pieces', pieceSize: it.piece_size ? null : (learned || null),
      left: size ? Number((pieces * size / Number(it.unit_base || 1)).toFixed(3)) : null,
    };
  }
  function resolve(it, kind, n, prev) {
    if (kind === 'psize') return prev && prev.pieces != null ? fromPieces(it, prev.pieces, n) : null;
    if (n == null) return null;
    if (kind === 'pieces') return fromPieces(it, n, prev && prev.pieceSize);
    return { left: n, pieces: null, source: 'left' };
  }
  function rowNote(it, v) {
    if (v.source === 'pieces' && v.left == null) return `Escribe cuántos ${pieceUnit(it)} trae una pieza para calcular cuánto queda.`;
    if (v.left == null) return '';
    const partes = [];
    if (v.source === 'pieces') partes.push(`${num(v.pieces)} pieza${v.pieces === 1 ? '' : 's'} = <strong>${num(v.left)} ${esc(it.unit)}</strong>`);
    const base = baseOf(it);
    if (base != null) {
      const used = Number((base - v.left).toFixed(3));
      if (used > 0) partes.push(`Se usaron <strong>${num(used)} ${esc(it.unit)}</strong>`);
      else if (used < 0) partes.push(`Hay <strong>${num(-used)} ${esc(it.unit)}</strong> más que la última vez`);
      else partes.push('Sin cambio desde la última vez');
    }
    return partes.join(' · ');
  }

  function updateBar() {
    const total = state.sheet ? state.sheet.items.length : 0;
    const done = [...state.values.values()].filter((v) => v.left != null).length;
    $('barCount').textContent = `${done} de ${total}`;
    $('barFill').style.width = total ? `${Math.round((done / total) * 100)}%` : '0%';
    $('btnSubmit').disabled = !done;
  }
  $('sheetBox').addEventListener('input', (e) => {
    const inp = e.target.closest('input[data-id]');
    if (!inp) return;
    const id = Number(inp.dataset.id);
    const it = state.sheet.items.find((i) => i.inventory_item_id === id);
    const raw = inp.value.trim();
    const n = raw === '' || Number.isNaN(Number(raw)) || Number(raw) < 0 ? null : Number(raw);
    const kind = inp.dataset.kind;
    const v = resolve(it, kind, n, state.values.get(id));
    if (v) state.values.set(id, v); else state.values.delete(id);
    const row = inp.closest('.cie-row');
    const left = row.querySelector('input[data-kind="left"]');
    const pieces = row.querySelector('input[data-kind="pieces"]');
    const psize = row.querySelector('[data-psize]');
    if (kind === 'pieces' || kind === 'psize') {
      left.value = v && v.left != null ? v.left : '';
      left.classList.toggle('derived', !!(v && v.left != null));
    } else {
      left.classList.remove('derived');
      if (pieces) pieces.value = '';
    }
    if (psize) {
      const mostrar = !!(v && v.source === 'pieces');
      psize.hidden = !mostrar;
      if (!mostrar) psize.querySelector('input').value = '';
    }
    row.className = `cie-row ${rowClass(v || {})}`;
    row.querySelector('[data-now]').innerHTML = rowNote(it, v || {});
    updateBar();
  });
  // Enter salta al siguiente renglón: se llena de arriba a abajo sin tocar la pantalla.
  $('sheetBox').addEventListener('keydown', (e) => {
    if (e.key !== 'Enter') return;
    const inp = e.target.closest('input[data-id]');
    if (!inp) return;
    e.preventDefault();
    // Enter baja al mismo campo (Queda o Se usó) del siguiente renglón.
    const all = [...$('sheetBox').querySelectorAll(`input[data-kind="${inp.dataset.kind}"]`)];
    const next = all[all.indexOf(inp) + 1];
    if (next) { next.focus(); next.select(); } else inp.blur();
  });
  $('sheetBox').addEventListener('focusin', (e) => { const inp = e.target.closest('input[data-id]'); if (inp) inp.select(); });

  // ---- cerrar turno ----
  $('btnSubmit').addEventListener('click', async () => {
    const total = state.sheet.items.length;
    const sinTamano = [...state.values.entries()].filter(([, v]) => v.source === 'pieces' && v.left == null);
    if (sinTamano.length) {
      const nombre = (state.sheet.items.find((i) => i.inventory_item_id === sinTamano[0][0]) || {}).name || 'un insumo';
      utils.showToast(`Falta cuánto trae una pieza de ${nombre}.`, 'error');
      const inp = $('sheetBox').querySelector(`input[data-kind="psize"][data-id="${sinTamano[0][0]}"]`);
      if (inp) inp.focus();
      return;
    }
    const listos = [...state.values.entries()].filter(([, v]) => v.left != null);
    const faltan = total - listos.length;
    if (faltan > 0 && !confirm(`Faltan ${faltan} insumo${faltan === 1 ? '' : 's'} sin anotar. Los que quedan vacíos no se tocan. ¿Cerrar igual?`)) return;
    const btn = $('btnSubmit');
    btn.disabled = true;
    try {
      const res = await api.post('/inventory/closing-sheet', {
        branch_id: Number(state.branchId), notes: $('notes').value.trim() || null,
        lines: listos.map(([id, v]) => ({ inventory_item_id: id, counted_quantity: String(v.left), ...(v.pieceSize ? { piece_size: String(v.pieceSize) } : {}) })),
      });
      state.values.clear();
      $('notes').value = '';
      showDone(res);
      loadHistory();
    } catch (err) {
      utils.showToast(err.message || 'No se pudo guardar el cierre.', 'error');
      btn.disabled = false;
    }
  });

  function showDone(res) {
    $('viewSheet').hidden = true;
    $('bar').hidden = true;
    $('viewDone').hidden = false;
    // Un insumo que se cuenta por primera vez no "sobró": lo que se anotó es su existencia de arranque.
    const primeraVez = new Set((state.sheet && state.sheet.items || []).filter((i) => i.last_counted_qty == null).map((i) => i.inventory_item_id));
    const arranque = res.lines.filter((l) => primeraVez.has(l.inventory_item_id));
    const resto = res.lines.filter((l) => !primeraVez.has(l.inventory_item_id));
    const gastadas = resto.filter((l) => Number(l.used) > 0);
    const sobraron = resto.filter((l) => Number(l.used) < 0);
    const iguales = resto.length - gastadas.length - sobraron.length;
    $('doneSummary').textContent = res.is_first_count
      ? `Primer cierre de la sucursal: ${res.lines.length} insumo${res.lines.length === 1 ? '' : 's'} con existencia de arranque. Desde ahora el sistema calcula lo gastado.`
      : `${res.lines.length} insumo${res.lines.length === 1 ? '' : 's'} anotado${res.lines.length === 1 ? '' : 's'}.${gastadas.length ? ` Se gastó en ${gastadas.length}.` : ''}${res.used_cost != null ? ` Costo del gasto: $${Number(res.used_cost).toFixed(2)}.` : ''}`;
    $('doneList').innerHTML = res.is_first_count ? '' : [
      ...gastadas.map((l) => `<div class="cie-done-line"><span>${esc(l.item_name)}</span><strong>−${num(l.used)} ${esc(l.unit)}${l.used_cost != null ? ` <small>· $${Number(l.used_cost).toFixed(2)}</small>` : ''}</strong></div>`),
      ...sobraron.map((l) => `<div class="cie-done-line more"><span>${esc(l.item_name)} <small>· hay más de lo anotado</small></span><strong>+${num(-l.used)} ${esc(l.unit)}</strong></div>`),
      ...arranque.map((l) => `<div class="cie-done-line none"><span>${esc(l.item_name)} <small>· primera vez: existencia de arranque</small></span><strong>${num(l.counted_quantity)} ${esc(l.unit)}</strong></div>`),
      iguales ? `<div class="cie-done-line none"><span>${iguales} sin cambio</span><strong>—</strong></div>` : '',
    ].join('');
    utils.renderIcons();
    window.scrollTo(0, 0);
  }
  function showSheet() {
    $('viewDone').hidden = true;
    $('viewSheet').hidden = false;
  }
  $('btnBackToSheet').addEventListener('click', () => { showSheet(); loadSheet(); });

  // ---- historial ----
  async function loadHistory() {
    try {
      const rows = await api.get(`/inventory/closing-sheet/history?branch_id=${state.branchId}&limit=5`);
      $('historyWrap').hidden = !rows.length;
      $('history').innerHTML = rows.map((r) => {
        const gastadas = r.lines.filter((l) => Number(l.used) > 0);
        return `
          <div class="cie-hist">
            <div class="cie-hist-head"><strong>${esc(whenLabel(r.at))}</strong> · ${esc(r.by)} · ${r.lines.length} insumo${r.lines.length === 1 ? '' : 's'}${r.notes ? ` · ${esc(r.notes)}` : ''}</div>
            ${gastadas.length ? `<ul class="cie-hist-items">${gastadas.slice(0, 8).map((l) => `<li>−${num(l.used)} ${esc(l.unit)} ${esc(l.item_name)}</li>`).join('')}${gastadas.length > 8 ? `<li>+${gastadas.length - 8} más</li>` : ''}</ul>` : ''}
          </div>`;
      }).join('');
    } catch (e) { $('historyWrap').hidden = true; }
  }

  // ---- elegir insumos (encargados) ----
  async function openConfig() {
    $('configError').hidden = true;
    $('configSearch').value = '';
    state.chosen = new Set((state.sheet && state.sheet.items || []).map((i) => i.inventory_item_id));
    if (!state.catalog.length) {
      try { state.catalog = await api.get('/inventory/items?limit=500'); } catch (e) { utils.showToast('No se pudo cargar el catálogo.', 'error'); return; }
    }
    renderConfigList();
    $('modalConfig').classList.add('active');
    utils.renderIcons();
    setTimeout(() => $('configSearch').focus(), 50);
  }
  function renderConfigList() {
    const q = norm($('configSearch').value.trim());
    const rows = state.catalog.filter((i) => !q || norm(i.name).includes(q) || norm(i.category).includes(q));
    const groups = new Map();
    for (const i of rows) { const k = i.category || 'Otros'; if (!groups.has(k)) groups.set(k, []); groups.get(k).push(i); }
    $('configList').innerHTML = [...groups.entries()].sort((a, b) => a[0].localeCompare(b[0])).map(([cat, items]) => `
      <div class="cie-config-cat">${esc(cat)}</div>
      ${items.map((i) => `
        <label class="cie-config-item">
          <input type="checkbox" data-id="${i.id}" ${state.chosen.has(i.id) ? 'checked' : ''} />
          <span>${esc(i.name)}</span><small>${esc(i.unit || '')}</small>
        </label>`).join('')}`).join('') || '<div class="ops-empty">Nada coincide.</div>';
    $('configCount').textContent = `${state.chosen.size} marcado${state.chosen.size === 1 ? '' : 's'}`;
  }
  $('configSearch').addEventListener('input', renderConfigList);
  $('configList').addEventListener('change', (e) => {
    const cb = e.target.closest('input[data-id]');
    if (!cb) return;
    if (cb.checked) state.chosen.add(Number(cb.dataset.id)); else state.chosen.delete(Number(cb.dataset.id));
    $('configCount').textContent = `${state.chosen.size} marcado${state.chosen.size === 1 ? '' : 's'}`;
  });
  function closeConfig() { $('modalConfig').classList.remove('active'); }
  $('btnConfig').addEventListener('click', openConfig);
  $('btnConfigClose').addEventListener('click', closeConfig);
  $('btnConfigCancel').addEventListener('click', closeConfig);
  $('modalConfig').addEventListener('click', (e) => { if (e.target === $('modalConfig')) closeConfig(); });
  $('btnConfigSave').addEventListener('click', async () => {
    const btn = $('btnConfigSave');
    btn.disabled = true;
    $('configError').hidden = true;
    try {
      // Orden de la hoja: por categoría y nombre, que es como se ve en el estante.
      const ids = state.catalog.filter((i) => state.chosen.has(i.id))
        .sort((a, b) => (a.category || 'Otros').localeCompare(b.category || 'Otros') || a.name.localeCompare(b.name))
        .map((i) => i.id);
      await api.put('/inventory/closing-sheet/config', { branch_id: Number(state.branchId), item_ids: ids });
      utils.showToast(ids.length ? `Hoja guardada: ${ids.length} insumo${ids.length === 1 ? '' : 's'}.` : 'Hoja vaciada.', 'success');
      closeConfig();
      state.values.clear();
      loadSheet();
    } catch (err) {
      $('configError').textContent = err.message || 'No se pudo guardar la hoja.';
      $('configError').hidden = false;
    } finally { btn.disabled = false; }
  });

  await loadSheet();
  loadHistory();
  utils.renderIcons();
});
