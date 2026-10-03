/**
 * Farmhouse Link — Recetas
 *
 * Recetas cargadas (las del dashboard anterior), para llenar lo que Invu no tiene:
 *  - Recetas y food cost: costo de cada plato con los precios del catálogo o los cargados.
 *  - Emparejar ingredientes: cada nombre de ingrediente → un insumo del catálogo (una vez).
 *  - Platos vendidos: qué receta cargada usa cada plato que no tiene receta en Invu.
 *  - Preparaciones: lote, rendimiento y costo por gramo de las salsas y bases.
 *  - Precios de compra: por proveedor, con el más barato por kilo.
 * Datos: /recipes, /recipes/ingredients, /recipes/dishes, /recipes/prices, /recipes/import.
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
  if (!perms.includes('reports.view')) {
    $('recGate').innerHTML = '<p>Esta pantalla es para encargados y gerencia.</p><a class="inv-primary-btn" href="/hub">Volver</a>';
    return;
  }
  const canEdit = perms.includes('inventory.adjust');
  const canImport = perms.includes('users.manage');
  $('recGate').hidden = true;
  $('recMain').hidden = false;
  FarmhouseShell.fillUserHeader({ nameId: 'recAgentName', roleId: 'recAgentRole', avatarId: 'recAgentAvatar' }, user);
  $('importLabel').hidden = !canImport;

  const money = (n) => (n == null ? '—' : `$${Number(n).toFixed(2)}`);
  const num = (n) => new Intl.NumberFormat('es-PA', { maximumFractionDigits: 2 }).format(Number(n) || 0);
  const state = { tab: 'recetas', recipes: [], ingredients: [], dishes: [], expanded: new Set() };

  // ---- pestañas ----
  const VIEWS = { recetas: 'viewRecetas', ingredientes: 'viewIngredientes', platos: 'viewPlatos', unidades: 'viewUnidades', preparaciones: 'viewPreparaciones', precios: 'viewPrecios' };
  const LOADERS = { recetas: loadRecipes, ingredientes: loadIngredients, platos: loadDishes, unidades: loadUnits, preparaciones: loadPreps, precios: loadPrices };
  // En celular la fila de pestañas se desplaza de lado: que la activa quede a la vista.
  function revealActiveTab() {
    const fila = $('recTabs');
    const activa = fila.querySelector('.ops-tab.active');
    if (!activa || fila.scrollWidth <= fila.clientWidth) return;
    const x = activa.getBoundingClientRect(), f = fila.getBoundingClientRect();
    if (x.left < f.left || x.right > f.right) fila.scrollLeft += x.left - f.left - 12;
  }
  function showTab(tab) {
    if (!VIEWS[tab]) tab = 'recetas';
    state.tab = tab;
    document.querySelectorAll('#recTabs .ops-tab').forEach((b) => b.classList.toggle('active', b.dataset.tab === tab));
    Object.entries(VIEWS).forEach(([k, id]) => { $(id).hidden = k !== tab; });
    const u = new URL(location.href); u.searchParams.set('tab', tab); history.replaceState(null, '', u);
    revealActiveTab();
    LOADERS[tab]();
  }
  $('recTabs').addEventListener('click', (e) => { const b = e.target.closest('.ops-tab'); if (b) showTab(b.dataset.tab); });

  // ---- recetas y food cost ----
  function fcChip(r) {
    if (r.food_cost_pct == null) return '<span class="rec-fc none">sin costo</span>';
    const v = Number(r.food_cost_pct);
    const cls = v > 100 ? 'bad' : v > 40 ? 'high' : v > 30 ? 'mid' : 'ok';
    return `<span class="rec-fc ${cls}" title="${v > 100 ? 'Cuesta más que el precio de venta: casi seguro un dato mal cargado' : 'Costo de insumos / precio de venta'}">${num(v)}%${v > 100 ? ' · revisar' : ''}</span>`;
  }
  function mapChip(r) {
    const cls = r.total_lines && r.mapped_lines === r.total_lines ? 'full' : r.mapped_lines ? 'part' : 'zero';
    return `<span class="rec-map ${cls}">${r.mapped_lines}/${r.total_lines}</span>`;
  }
  async function loadRecipes() {
    const box = $('recBox');
    box.innerHTML = '<div class="ops-loading">Cargando…</div>';
    try {
      const d = await api.get('/recipes');
      state.recipes = d.recipes;
      const s = d.summary;
      $('recKpis').innerHTML = [
        ['Recetas cargadas', num(s.total)],
        ['Se pueden usar', `${num(s.usable)} de ${num(s.total)}`],
        ['Completas', num(s.fully_mapped)],
        ['Food cost promedio', s.avg_food_cost_pct != null ? `${num(s.avg_food_cost_pct)}%` : '—'],
        ['A revisar', num(s.to_review)],
      ].map(([l, v]) => `<div class="rec-kpi"><span>${esc(l)}</span><strong>${esc(v)}</strong></div>`).join('');
      renderRecipes();
    } catch (err) { box.innerHTML = `<div class="ops-empty">${esc(err.message || 'No se pudieron cargar las recetas.')}</div>`; }
  }
  function renderRecipes() {
    const box = $('recBox');
    if (!state.recipes.length) {
      box.innerHTML = `<div class="ops-empty">Todavía no hay recetas cargadas.${canImport ? ' Usa <b>Importar</b> (arriba a la derecha) con el archivo de recetas.' : ' Pide a un administrador que las importe.'}</div>`;
      return;
    }
    const q = $('recSearch').value.trim().toLowerCase();
    const f = $('recFilter').value;
    const rows = state.recipes.filter((r) => (!q || r.name.toLowerCase().includes(q) || (r.category || '').toLowerCase().includes(q))
      && (!f || (f === 'ok' ? !r.flag : r.flag === f)));
    if (!rows.length) { box.innerHTML = '<div class="ops-empty">Ninguna receta con ese filtro.</div>'; return; }
    box.innerHTML = `<table class="ops-table"><thead><tr><th>Plato</th><th>Ingredientes</th><th>Costo</th><th>Precio</th><th>Food cost</th><th>Platos que la usan</th></tr></thead><tbody>${rows.map((r) => `
      <tr class="rec-row" data-rec="${r.id}">
        <td><span class="ops-title">${esc(r.name)}</span><span class="ops-sub">${esc(r.category || '')}</span></td>
        <td data-label="Ingredientes">${mapChip(r)}</td>
        <td data-label="Costo"><span>${money(r.cost)}${r.cost_complete ? '' : ' <small class="ops-sub" title="Hay ingredientes sin costo">parcial</small>'}</span></td>
        <td data-label="Precio">${money(r.sale_price)}</td>
        <td data-label="Food cost">${fcChip(r)}</td>
        <td data-label="Platos"><span class="ops-sub">${r.dishes.length ? esc(r.dishes.join(', ')) : (r.mapped_lines ? 'Por nombre' : '—')}</span></td>
      </tr>
      ${state.expanded.has(r.id) ? `<tr class="rec-detail"><td colspan="6"><ul class="rec-lines">${r.lines.map((l) => `
        <li><span>${esc(l.name)} · ${num(l.quantity)} ${esc(l.unit)}${l.item ? ` → <b>${esc(l.item.name)}</b>` : ''}</span>
          <span class="st ${l.unit_issue ? 'sin_unidad' : l.status}">${l.unit_issue ? 'falta la unidad' : l.status === 'emparejado' ? 'emparejado' : l.status === 'ignorado' ? 'no se descuenta' : 'sin emparejar'}</span>
          <span>${l.cost != null ? money(l.cost) : '—'}</span></li>`).join('')}</ul>
        ${r.ref_food_cost_pct != null ? `<p class="ops-sub" style="margin:8px 0 0">En el dashboard anterior: costo ${money(r.ref_cost)}, food cost ${num(r.ref_food_cost_pct)}%.</p>` : ''}</td></tr>` : ''}`).join('')}</tbody></table>`;
  }
  $('recBox').addEventListener('click', (e) => {
    const tr = e.target.closest('tr[data-rec]');
    if (!tr) return;
    const id = Number(tr.dataset.rec);
    if (state.expanded.has(id)) state.expanded.delete(id); else state.expanded.add(id);
    renderRecipes();
  });
  $('recSearch').addEventListener('input', renderRecipes);
  $('recFilter').addEventListener('change', renderRecipes);

  // ---- emparejar ingredientes ----
  async function loadIngredients() {
    const box = $('ingBox');
    box.innerHTML = '<div class="ops-loading">Cargando…</div>';
    try {
      const d = await api.get('/recipes/ingredients');
      state.ingredients = d.ingredients;
      $('countIng').textContent = d.pending; $('countIng').hidden = !d.pending;
      $('ingNote').textContent = `${d.pending} pendientes · ${d.mapped} emparejados · ${d.ignored} no se descuentan`;
      renderIngredients();
    } catch (err) { box.innerHTML = `<div class="ops-empty">${esc(err.message || 'No se pudieron cargar.')}</div>`; }
  }
  function renderIngredients() {
    const f = $('ingFilter').value;
    const rows = state.ingredients.filter((i) => !f || i.status === f);
    const box = $('ingBox');
    if (!rows.length) { box.innerHTML = `<div class="ops-empty">${f === 'pendiente' ? '¡Todo emparejado!' : 'Nada con ese filtro.'}</div>`; return; }
    box.innerHTML = rows.map((i) => `
      <div class="rec-ing" data-ing="${i.id}">
        <div class="rec-ing-name"><strong>${esc(i.name)}</strong>${i.auto ? '<small>emparejado solo por nombre, revísalo</small>' : ''}</div>
        <div class="rec-ing-uses">en ${num(i.recipes)} receta${i.recipes === 1 ? '' : 's'}</div>
        <div class="rec-ing-actions">
          ${i.status === 'emparejado' ? `<span class="rec-mapped">→ <b>${esc(i.item.name)}</b> <small class="ops-sub">(${esc(i.item.unit)})</small></span>` : ''}
          ${i.status === 'ignorado' ? '<span class="rec-mapped ops-sub">No se descuenta del inventario</span>' : ''}
          ${canEdit ? `
          ${i.suggestions.length ? `<div class="rec-sugs">${i.suggestions.map((s) => `<button type="button" class="rec-sug" data-map="${s.id}">${esc(s.name)} <small>${esc(s.unit || '')}</small></button>`).join('')}</div>` : ''}
          <div class="rec-search-row">
            <input class="modal-input" data-search placeholder="${i.status === 'pendiente' ? 'Buscar insumo del catálogo…' : 'Cambiar por otro insumo…'}" autocomplete="off" />
            ${i.status === 'ignorado' ? '<button type="button" class="ops-btn" data-unignore>Volver a pendiente</button>' : '<button type="button" class="ops-btn" data-ignore>No se descuenta</button>'}
          </div>
          <div class="rec-results" data-results hidden></div>` : ''}
        </div>
      </div>`).join('');
    utils.renderIcons();
  }
  async function mapIngredient(id, body) {
    try {
      await api.put(`/recipes/ingredients/${id}`, body);
      const i = state.ingredients.find((x) => x.id === id);
      utils.showToast(body.ignored ? `"${i.name}" no se descontará.` : body.inventory_item_id ? `"${i.name}" emparejado.` : `"${i.name}" vuelve a pendiente.`, 'success');
      await loadIngredients();
    } catch (err) { utils.showToast(err.message || 'No se pudo guardar.', 'error'); }
  }
  let ingTimer = null;
  $('ingBox').addEventListener('input', (e) => {
    const inp = e.target.closest('input[data-search]');
    if (!inp) return;
    const row = inp.closest('[data-ing]');
    const box = row.querySelector('[data-results]');
    clearTimeout(ingTimer);
    const q = inp.value.trim();
    if (q.length < 2) { box.hidden = true; return; }
    ingTimer = setTimeout(async () => {
      try {
        const items = await api.get(`/inventory/items?q=${encodeURIComponent(q)}&limit=8`);
        box.innerHTML = items.length ? items.map((it) => `<button type="button" data-map="${it.id}">${esc(it.name)}<small>${esc(it.unit || '')}${it.category ? ` · ${esc(it.category)}` : ''}</small></button>`).join('') : '<button type="button" disabled>Nada coincide</button>';
        box.hidden = false;
      } catch (err) { box.hidden = true; }
    }, 200);
  });
  $('ingBox').addEventListener('click', (e) => {
    const row = e.target.closest('[data-ing]');
    if (!row) return;
    const id = Number(row.dataset.ing);
    const m = e.target.closest('[data-map]');
    if (m) { mapIngredient(id, { inventory_item_id: Number(m.dataset.map) }); return; }
    if (e.target.closest('[data-ignore]')) { mapIngredient(id, { ignored: true }); return; }
    if (e.target.closest('[data-unignore]')) { mapIngredient(id, { ignored: false, inventory_item_id: null }); }
  });
  $('ingFilter').addEventListener('change', renderIngredients);

  // ---- platos vendidos ----
  async function loadDishes() {
    const box = $('dishBox');
    box.innerHTML = '<div class="ops-loading">Cargando…</div>';
    try {
      const [d, r] = await Promise.all([api.get('/recipes/dishes'), state.recipes.length ? Promise.resolve({ recipes: state.recipes }) : api.get('/recipes')]);
      state.dishes = d.dishes;
      state.recipes = r.recipes;
      $('countPlatos').textContent = d.without_recipe; $('countPlatos').hidden = !d.without_recipe;
      $('dishNote').textContent = `${d.without_recipe} sin receta · ${d.with_loaded_recipe} con receta cargada`;
      renderDishes();
    } catch (err) { box.innerHTML = `<div class="ops-empty">${esc(err.message || 'No se pudieron cargar.')}</div>`; }
  }
  function renderDishes() {
    const f = $('dishFilter').value;
    const rows = state.dishes.filter((d) => !f || (f === 'sin' ? !d.recipe : !!d.recipe));
    const box = $('dishBox');
    if (!rows.length) { box.innerHTML = '<div class="ops-empty">Nada con ese filtro.</div>'; return; }
    const opciones = (sel) => '<option value="">— Sin receta (cargar en Invu) —</option>' + state.recipes.map((r) => `<option value="${r.id}" ${sel === r.id ? 'selected' : ''}>${esc(r.name)}</option>`).join('');
    box.innerHTML = `<table class="ops-table"><thead><tr><th>Plato vendido</th><th>Vendidos 30 d</th><th>Receta</th></tr></thead><tbody>${rows.map((d) => `
      <tr data-dish="${esc(d.dish_name)}">
        <td><span class="ops-title">${esc(d.dish_name)}</span>${d.auto ? '<span class="ops-sub">enlazado solo por nombre</span>' : ''}</td>
        <td data-label="Vendidos 30 d">${num(d.sold)}</td>
        <td class="rec-td-wide">${canEdit ? `<select class="inv-filter-select rec-dish-select" data-link>${opciones(d.recipe ? d.recipe.id : null)}</select>` : esc(d.recipe ? d.recipe.name : '—')}
          ${!d.recipe && d.suggestions.length && canEdit ? `<div class="rec-sugs" style="margin-top:6px">${d.suggestions.map((s) => `<button type="button" class="rec-sug" data-quick="${s.id}">${esc(s.name)}</button>`).join('')}</div>` : ''}</td>
      </tr>`).join('')}</tbody></table>`;
  }
  async function linkDish(name, recipeId) {
    try {
      await api.put('/recipes/dishes', { dish_name: name, recipe_id: recipeId });
      utils.showToast(recipeId ? `"${name}" usa la receta elegida.` : `"${name}" queda sin receta.`, 'success');
      await loadDishes();
    } catch (err) { utils.showToast(err.message || 'No se pudo guardar.', 'error'); }
  }
  $('dishBox').addEventListener('change', (e) => {
    const s = e.target.closest('select[data-link]');
    if (s) linkDish(s.closest('[data-dish]').dataset.dish, s.value ? Number(s.value) : null);
  });
  $('dishBox').addEventListener('click', (e) => {
    const b = e.target.closest('[data-quick]');
    if (b) linkDish(b.closest('[data-dish]').dataset.dish, Number(b.dataset.quick));
  });
  $('dishFilter').addEventListener('change', renderDishes);

  // ---- unidades: receta en otra unidad que el insumo ----
  const FAM = { peso: 'g', volumen: 'ml' };
  // Una pregunta por cada cruce: qué número poner y a qué dato del insumo va.
  function pregunta(it, c) {
    if (c.kind === 'densidad') {
      return { field: 'grams_per_ml', value: it.grams_per_ml, unit: 'g',
        text: `La receta lo pide en <b>${esc(c.recipe_unit)}</b> y se mide en <b>${esc(it.unit)}</b>. ¿Cuántos gramos pesa 1 ml?`,
        hint: 'Agua, leches y jugos: 1. Aceite: 0.92. Miel o siropes: 1.4.', water: true };
    }
    if (c.kind === 'pieza') {
      const fam = c.recipe_family === 'unidad' ? c.item_family : c.recipe_family;   // peso o volumen
      const u = FAM[fam] || 'g';
      const verbo = fam === 'volumen' ? 'trae' : 'pesa';
      const pieza = c.recipe_family === 'unidad' ? esc(c.recipe_unit) : esc(it.unit);
      // No todas las piezas pesan igual (hay limones más grandes): se pesan varias juntas y se
      // guarda el promedio. Con muchas ventas las diferencias se compensan y el cierre de turno
      // corrige con lo que de verdad queda.
      return { field: 'piece_size', value: it.piece_size, unit: u, average: true, verb: verbo,
        text: `La receta lo pide en <b>${esc(c.recipe_unit)}</b> y se lleva en <b>${esc(it.unit)}</b>. ¿Cuántos ${u} ${verbo} 1 ${pieza}, en promedio?`,
        hint: `Como no todas ${verbo === 'trae' ? 'traen' : 'pesan'} igual, ${verbo === 'trae' ? 'mide' : 'pesa'} varias juntas (por ejemplo 10) y el sistema saca el promedio.` };
    }
    return null;
  }
  function unitCard(f, resuelto) {
    const it = f.item;
    const vistos = new Set();
    const filas = f.conversions.map((c) => {
      if (c.kind === 'desconocida') {
        return `<p class="rec-unit-q">La receta dice <b>“${esc(c.recipe_unit)}”</b>: es una medida que no se puede convertir. Hay que cambiarla en Invu por gramos, ml o unidades.</p>`;
      }
      const q = pregunta(it, c);
      if (!q || vistos.has(q.field)) return '';
      vistos.add(q.field);
      return `<div class="rec-unit-q" data-field="${q.field}">
          <p>${q.text}${c.ok ? ' <span class="rec-unit-ok">resuelto</span>' : ''}</p>
          ${q.average && q.value != null ? `<p class="rec-unit-now">Hoy: <b>${num(q.value)} ${q.unit}</b> por pieza.</p>` : ''}
          ${canEdit && q.average ? `<div class="rec-unit-form rec-unit-avg">
            <label>${q.verb === 'trae' ? 'Medí' : 'Pesé'} <input class="modal-input" data-count type="number" inputmode="numeric" step="1" min="1" placeholder="10" aria-label="Cuántas piezas" /> juntas</label>
            <label>y ${q.verb === 'trae' ? 'trajeron' : 'pesaron'} <input class="modal-input" data-total type="number" inputmode="decimal" step="0.1" min="0.1" placeholder="${q.unit}" aria-label="Total en ${q.unit}" /> ${q.unit}</label>
            <span class="rec-unit-result" data-result></span>
            <button type="button" class="inv-primary-btn" data-save>Guardar</button>
          </div><small class="ops-sub">${esc(q.hint)}</small>` : ''}
          ${canEdit && !q.average ? `<div class="rec-unit-form">
            <input class="modal-input" data-value type="number" inputmode="decimal" step="0.01" min="0.01" value="${q.value != null ? Number(q.value) : ''}" placeholder="${q.unit}" aria-label="${q.unit}" />
            <span class="rec-unit-suffix">${q.unit}</span>
            ${q.water ? '<button type="button" class="ops-btn" data-water>Como agua (1)</button>' : ''}
            <button type="button" class="inv-primary-btn" data-save>Guardar</button>
          </div><small class="ops-sub">${esc(q.hint)}</small>` : ''}
        </div>`;
    }).join('');
    return `<div class="rec-unit${resuelto ? ' is-ok' : ''}" data-item="${it.id}">
        <div class="rec-unit-head"><strong>${esc(it.name)}</strong><span class="ops-sub">se mide en ${esc(it.unit)} · ${num(f.dishes_count)} plato${f.dishes_count === 1 ? '' : 's'}: ${esc(f.dishes.join(', '))}${f.dishes_count > f.dishes.length ? '…' : ''}</span></div>
        ${filas}
      </div>`;
  }
  function setUnitCount(n) { $('countUnits').textContent = n; $('countUnits').hidden = !n; }
  async function loadUnits() {
    const box = $('unitBox');
    box.innerHTML = '<div class="ops-loading">Cargando…</div>';
    try {
      const d = await api.get('/recipes/unit-issues');
      setUnitCount(d.pending.length);
      box.innerHTML = (d.pending.length
        ? d.pending.map((f) => unitCard(f, false)).join('')
        : '<div class="ops-empty">Todas las recetas están en unidades que se pueden descontar.</div>')
        + (d.resolved.length ? `<details class="rec-unit-done"><summary>Ya resueltos (${d.resolved.length})</summary>${d.resolved.map((f) => unitCard(f, true)).join('')}</details>` : '');
    } catch (err) { box.innerHTML = `<div class="ops-empty">${esc(err.message || 'No se pudieron cargar.')}</div>`; }
  }
  // Promedio de las piezas pesadas juntas: total / cuántas.
  function promedio(q) {
    const n = Number(q.querySelector('[data-count]').value);
    const total = Number(q.querySelector('[data-total]').value);
    return n >= 1 && total > 0 ? Math.round((total / n) * 1000) / 1000 : null;
  }
  $('unitBox').addEventListener('input', (e) => {
    const q = e.target.closest('.rec-unit-avg');
    if (!q) return;
    const v = promedio(q);
    const u = q.querySelector('[data-total]').placeholder;
    q.querySelector('[data-result]').textContent = v ? `= ${num(v)} ${u} cada una` : '';
  });
  $('unitBox').addEventListener('click', async (e) => {
    const q = e.target.closest('[data-field]');
    if (!q) return;
    const agua = e.target.closest('[data-water]');
    if (agua) q.querySelector('[data-value]').value = '1';
    if (!agua && !e.target.closest('[data-save]')) return;
    const avg = q.querySelector('.rec-unit-avg');
    const v = avg ? promedio(avg) : Number(q.querySelector('[data-value]').value);
    if (!(v > 0)) {
      utils.showToast(avg ? 'Pon cuántas pesaste y cuánto pesaron juntas.' : 'Pon un número mayor que cero.', 'error');
      (avg ? avg.querySelector('[data-count]') : q.querySelector('[data-value]')).focus();
      return;
    }
    const id = q.closest('[data-item]').dataset.item;
    const field = q.dataset.field;
    try {
      await api.patch(`/inventory/items/${id}/${field === 'grams_per_ml' ? 'density' : 'piece-size'}`, { [field]: String(v) });
      utils.showToast('Guardado: esas recetas ya se descuentan.', 'success');
      state.recipes = [];
      await loadUnits();
    } catch (err) { utils.showToast(err.message || 'No se pudo guardar.', 'error'); }
  });

  // ---- preparaciones ----
  async function loadPreps() {
    const box = $('prepBox');
    box.innerHTML = '<div class="ops-loading">Cargando…</div>';
    try {
      const d = await api.get('/recipes?kind=interna');
      if (!d.recipes.length) { box.innerHTML = '<div class="ops-empty">No hay preparaciones cargadas.</div>'; return; }
      box.innerHTML = `<table class="ops-table"><thead><tr><th>Preparación</th><th>Lote</th><th>Porciones</th><th>Porción</th><th>Costo del lote</th><th>Costo por 100 g</th></tr></thead><tbody>${d.recipes.map((r) => `
        <tr>
          <td><span class="ops-title">${esc(r.name)}</span><span class="ops-sub">${esc(r.category || '')}${r.notes ? ` · ${esc(r.notes)}` : ''}</span></td>
          <td data-label="Lote">${r.yield_weight_g != null ? `${num(r.yield_weight_g)} g` : '—'}</td>
          <td data-label="Porciones">${r.yield_portions != null ? num(r.yield_portions) : '—'}</td>
          <td data-label="Porción">${r.portion_g != null ? `${num(r.portion_g)} g` : '—'}</td>
          <td data-label="Costo del lote">${money(r.batch_cost)}</td>
          <td data-label="Costo por 100 g">${r.cost_per_g != null ? money(Number(r.cost_per_g) * 100) : '—'}</td>
        </tr>`).join('')}</tbody></table>`;
    } catch (err) { box.innerHTML = `<div class="ops-empty">${esc(err.message || 'No se pudieron cargar.')}</div>`; }
  }

  // ---- precios ----
  let priceTimer = null;
  async function loadPrices() {
    const box = $('priceBox');
    box.innerHTML = '<div class="ops-loading">Cargando…</div>';
    try {
      const q = $('priceSearch').value.trim();
      const d = await api.get(`/recipes/prices${q ? `?q=${encodeURIComponent(q)}` : ''}`);
      if (!d.ingredients.length) { box.innerHTML = '<div class="ops-empty">No hay precios cargados.</div>'; return; }
      box.innerHTML = `<table class="ops-table"><thead><tr><th>Ingrediente</th><th>Proveedores</th><th>Mejor por kg</th></tr></thead><tbody>${d.ingredients.map((g) => `
        <tr>
          <td><span class="ops-title">${esc(g.name)}</span><span class="ops-sub">${esc(g.category || '')}</span></td>
          <td class="rec-td-wide"><div class="rec-offers">${g.offers.map((o) => `<span class="rec-offer ${o.best ? 'best' : ''}">${esc(o.supplier)}: ${money(o.price)}${o.package_grams ? ` / ${num(o.package_grams)} g` : ''}${o.brand ? ` · ${esc(o.brand)}` : ''}</span>`).join('')}</div></td>
          <td data-label="Mejor por kg">${g.best_price_per_kg != null ? money(g.best_price_per_kg) : '—'}</td>
        </tr>`).join('')}</tbody></table>`;
    } catch (err) { box.innerHTML = `<div class="ops-empty">${esc(err.message || 'No se pudieron cargar.')}</div>`; }
  }
  $('priceSearch').addEventListener('input', () => { clearTimeout(priceTimer); priceTimer = setTimeout(loadPrices, 250); });

  // ---- importar (admin) ----
  $('importFile').addEventListener('change', async (e) => {
    const file = e.target.files && e.target.files[0];
    if (!file) return;
    try {
      const data = JSON.parse(await file.text());
      const r = await api.post('/recipes/import', data);
      utils.showToast(`Importado: ${r.recipes} recetas, ${r.internas} preparaciones, ${r.prices} precios. ${r.ingredients_auto} ingredientes y ${r.dishes_auto} platos se emparejaron solos.`, 'success');
      state.recipes = [];
      LOADERS[state.tab]();
    } catch (err) {
      utils.showToast(err instanceof SyntaxError ? 'El archivo no es un JSON válido.' : (err.message || 'No se pudo importar.'), 'error');
    } finally { e.target.value = ''; }
  });

  showTab(new URLSearchParams(location.search).get('tab') || 'recetas');
  setTimeout(revealActiveTab, 400);   // otra vez cuando ya están los íconos y contadores
  // Contadores de las otras pestañas.
  if (state.tab !== 'ingredientes') api.get('/recipes/ingredients').then((d) => { $('countIng').textContent = d.pending; $('countIng').hidden = !d.pending; }).catch(() => {});
  if (state.tab !== 'unidades') api.get('/recipes/unit-issues').then((d) => setUnitCount(d.pending.length)).catch(() => {});
  utils.renderIcons();
});
