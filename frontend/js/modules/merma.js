/**
 * Farmhouse Link — Merma rápida (/merma)
 *
 * Una pantalla aparte, hecha para la tablet de la cocina: tocar el insumo (con su foto), poner
 * cuánto pesa con el teclado grande, tocar el motivo y "Registrar". Nada más.
 *
 * Datos: GET /quick-waste/items (insumos, los más botados primero), POST /inventory/waste
 * (la merma de siempre), DELETE /inventory/waste/{id} (deshacer), PUT /quick-waste/items/{id}/photo.
 */
document.addEventListener('DOMContentLoaded', async () => {
  const $ = (id) => document.getElementById(id);
  const esc = (s) => utils.escapeHtml(s ?? '');
  const num = (n) => new Intl.NumberFormat('es-PA', { maximumFractionDigits: 3 }).format(Number(n) || 0);

  FarmhouseShell.initTheme();
  window.addEventListener('auth:unauthorized', () => { window.location.href = '/'; });
  const user = await auth.checkSession();
  if (!user) { window.location.href = '/'; return; }

  const BRANCH_KEY = 'fh_merma_branch';
  const leer = (k) => { try { return localStorage.getItem(k); } catch (e) { return null; } };
  const guardar = (k, v) => { try { localStorage.setItem(k, v); } catch (e) { /* sin almacenamiento */ } };

  const state = { data: null, query: '', cat: '', item: null, amount: '', unit: null, reason: null, last: null };

  // ---- íconos y colores por categoría, para los insumos que todavía no tienen foto ----
  const CAT_ICONS = [
    [/l[aá]cte|leche|queso|yogur/, 'milk'], [/congel/, 'snowflake'], [/bebida|jugo|refresco/, 'cup-soda'],
    [/vegetal|verdura|hortaliza/, 'carrot'], [/fruta/, 'apple'], [/carne|pollo|prote|pescado|at[uú]n/, 'drumstick'],
    [/pan|bakery|panader|reposter/, 'croissant'], [/aderezo|salsa|receta/, 'soup'], [/aceite|vinagre/, 'droplets'],
    [/seco|grano|harina|cereal|semilla/, 'wheat'], [/empaque|packag|desechable/, 'package'], [/especia|condimento/, 'leaf'],
  ];
  const PALETA = ['#16a34a', '#0ea5e9', '#f59e0b', '#8b5cf6', '#ef4444', '#14b8a6', '#ec4899', '#6366f1'];
  function iconoDe(cat) {
    const c = (cat || '').toLowerCase();
    return (CAT_ICONS.find(([re]) => re.test(c)) || [null, 'box'])[1];
  }
  function colorDe(cat) {
    let h = 0;
    for (const ch of (cat || '-')) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
    return PALETA[h % PALETA.length];
  }
  const sinAcentos = (s) => (s || '').normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase();

  // ---- fotos: van con la sesión y el id del dispositivo, así que se piden con fetch ----
  const fotos = new Map();   // "id:v" -> objectURL
  async function fotoUrl(item) {
    const clave = `${item.id}:${item.photo_v}`;
    if (fotos.has(clave)) return fotos.get(clave);
    const headers = { 'X-Requested-With': 'XMLHttpRequest' };
    const deviceId = api.getDeviceId();
    if (deviceId) headers['X-Device-ID'] = deviceId;
    const res = await fetch(`${api.baseUrl}/quick-waste/items/${item.id}/photo?v=${item.photo_v}`, { credentials: 'include', headers });
    if (!res.ok) throw new Error(String(res.status));
    const url = URL.createObjectURL(await res.blob());
    fotos.set(clave, url);
    return url;
  }
  function imagenHtml(item) {
    if (item.photo_v) return `<span class="mr-img" data-photo="${item.id}"></span>`;
    return `<span class="mr-img mr-img-icon" style="--c:${colorDe(item.category)}"><i data-lucide="${iconoDe(item.category)}"></i></span>`;
  }
  async function pintarFoto(el) {
    const item = state.data.items.find((i) => String(i.id) === el.dataset.photo);
    if (!item || !item.photo_v) return;
    try {
      el.style.backgroundImage = `url("${await fotoUrl(item)}")`;
      el.classList.add('is-loaded');
    } catch (e) { /* sin foto: queda el fondo */ }
  }
  const observador = 'IntersectionObserver' in window
    ? new IntersectionObserver((entradas) => entradas.forEach((e) => { if (e.isIntersecting) { observador.unobserve(e.target); pintarFoto(e.target); } }), { rootMargin: '200px' })
    : null;
  function cargarFotos(raiz) {
    raiz.querySelectorAll('[data-photo]').forEach((el) => (observador ? observador.observe(el) : pintarFoto(el)));
  }

  // ---- paso 1: la grilla ----
  async function cargar(branchId) {
    const d = await api.get(`/quick-waste/items${branchId ? `?branch_id=${branchId}` : ''}`);
    state.data = d;
    $('mrBranchName').textContent = d.branch.name;
    const sel = $('mrBranchSelect');
    if (d.branches.length > 1) {
      sel.innerHTML = d.branches.map((b) => `<option value="${b.id}" ${b.id === d.branch.id ? 'selected' : ''}>${esc(b.name)}</option>`).join('');
      sel.hidden = false;
      $('mrBranchName').hidden = true;
    }
    const cats = [...new Set(d.items.map((i) => i.category).filter(Boolean))].sort((a, b) => a.localeCompare(b, 'es'));
    const hayFrecuentes = d.items.some((i) => i.times > 0);
    $('mrCats').innerHTML = [
      hayFrecuentes ? '<button type="button" data-cat="__frec" class="active">Lo más botado</button>' : '',
      `<button type="button" data-cat="" class="${hayFrecuentes ? '' : 'active'}">Todo</button>`,
      ...cats.map((c) => `<button type="button" data-cat="${esc(c)}">${esc(c)}</button>`),
    ].join('');
    state.cat = hayFrecuentes ? '__frec' : '';
    renderGrid();
  }
  function renderGrid() {
    const q = sinAcentos(state.query.trim());
    let items = state.data.items;
    if (q) items = items.filter((i) => sinAcentos(i.name).includes(q));
    else if (state.cat === '__frec') items = items.filter((i) => i.times > 0).slice(0, 24);
    else if (state.cat) items = items.filter((i) => i.category === state.cat);
    $('mrGrid').innerHTML = items.length
      ? items.map((i) => `<button type="button" class="mr-card" data-item="${i.id}">${imagenHtml(i)}<span class="mr-card-name">${esc(i.name)}</span></button>`).join('')
      : `<p class="mr-empty">${q ? `No hay ningún insumo que se llame «${esc(state.query.trim())}».` : 'No hay insumos en esta categoría.'}</p>`;
    utils.renderIcons();
    cargarFotos($('mrGrid'));
  }
  $('mrSearch').addEventListener('input', (e) => {
    state.query = e.target.value;
    document.querySelectorAll('#mrCats button').forEach((b) => b.classList.toggle('active', !state.query && b.dataset.cat === state.cat));
    renderGrid();
  });
  $('mrCats').addEventListener('click', (e) => {
    const b = e.target.closest('[data-cat]');
    if (!b) return;
    state.cat = b.dataset.cat;
    state.query = ''; $('mrSearch').value = '';
    document.querySelectorAll('#mrCats button').forEach((x) => x.classList.toggle('active', x === b));
    renderGrid();
  });
  $('mrGrid').addEventListener('click', (e) => {
    const c = e.target.closest('[data-item]');
    if (c) abrir(state.data.items.find((i) => String(i.id) === c.dataset.item));
  });
  $('mrBranchSelect').addEventListener('change', async (e) => {
    guardar(BRANCH_KEY, e.target.value);
    try { await cargar(e.target.value); } catch (err) { utils.showToast(err.message || 'No se pudo cambiar de sucursal.', 'error'); }
  });

  // ---- paso 2: cuánto y por qué ----
  // Unidades que se pueden elegir según cómo se mide el insumo, y cuánto es 1 de cada una en
  // gramos (o ml) para pasarlo a la unidad del insumo.
  const UNIDADES = {
    peso: [{ u: 'g', base: 1 }, { u: 'kg', base: 1000 }],
    volumen: [{ u: 'ml', base: 1 }, { u: 'l', base: 1000 }],
    unidad: [{ u: 'piezas', base: null }],
  };
  function abrir(item) {
    state.item = item;
    state.amount = '';
    state.reason = null;
    const opciones = UNIDADES[item.family] || UNIDADES.unidad;
    state.unit = opciones[0];
    $('mrItemImg').innerHTML = imagenHtml(item);
    $('mrItemName').textContent = item.name;
    $('mrItemUnit').textContent = item.category ? `${item.category} · se lleva en ${item.unit}` : `Se lleva en ${item.unit}`;
    $('mrAmountLabel').textContent = item.family === 'unidad' ? '¿Cuántas piezas?' : item.family === 'volumen' ? '¿Cuánto es?' : '¿Cuánto pesa?';
    $('mrUnits').innerHTML = opciones.length > 1
      ? opciones.map((o, i) => `<button type="button" data-unit="${i}" class="${i === 0 ? 'active' : ''}">${o.u}</button>`).join('')
      : `<span class="mr-unit-fixed">${opciones[0].u}</span>`;
    $('mrReasons').innerHTML = state.data.reasons.map((r) => `<button type="button" data-reason="${r.code}">${esc(r.label)}</button>`).join('');
    $('mrPhotoBtn').hidden = !state.data.can_edit_photos;
    $('mrPhotoBtn').querySelector('span').textContent = item.photo_v ? 'Cambiar foto' : 'Poner foto';
    pintarCantidad();
    $('mrSheet').hidden = false;
    document.body.classList.add('mr-locked');
    utils.renderIcons();
    cargarFotos($('mrItemImg'));
  }
  function cerrar() {
    $('mrSheet').hidden = true;
    document.body.classList.remove('mr-locked');
    state.item = null;
  }
  function pintarCantidad() {
    $('mrAmount').textContent = state.amount ? num(state.amount) + (state.amount.endsWith('.') ? ',' : '') : '0';
    $('mrAmount').classList.toggle('is-empty', !state.amount);
    const listo = Number(state.amount) > 0 && state.reason;
    $('mrSubmit').disabled = !listo;
    $('mrHint').textContent = !(Number(state.amount) > 0) ? 'Pon cuánto con los números.' : !state.reason ? 'Elige el motivo.' : '';
  }
  $('mrKeypad').addEventListener('click', (e) => {
    const k = e.target.closest('[data-k]');
    if (!k) return;
    const v = k.dataset.k;
    if (v === 'del') state.amount = state.amount.slice(0, -1);
    else if (v === '.') { if (!state.amount.includes('.')) state.amount = (state.amount || '0') + '.'; }
    else if (state.amount.replace('.', '').length < 7) state.amount = state.amount === '0' ? v : state.amount + v;
    pintarCantidad();
  });
  $('mrUnits').addEventListener('click', (e) => {
    const b = e.target.closest('[data-unit]');
    if (!b) return;
    state.unit = UNIDADES[state.item.family][Number(b.dataset.unit)];
    document.querySelectorAll('#mrUnits button').forEach((x) => x.classList.toggle('active', x === b));
  });
  $('mrReasons').addEventListener('click', (e) => {
    const b = e.target.closest('[data-reason]');
    if (!b) return;
    state.reason = b.dataset.reason;
    document.querySelectorAll('#mrReasons button').forEach((x) => x.classList.toggle('active', x === b));
    pintarCantidad();
  });
  $('mrClose').addEventListener('click', cerrar);
  document.addEventListener('keydown', (e) => {
    if ($('mrSheet').hidden) return;
    if (e.key === 'Escape') cerrar();
    else if (/^[0-9.]$/.test(e.key)) $('mrKeypad').querySelector(`[data-k="${e.key}"]`)?.click();
    else if (e.key === 'Backspace') $('mrKeypad').querySelector('[data-k="del"]').click();
    else if (e.key === 'Enter' && !$('mrSubmit').disabled) $('mrSubmit').click();
  });

  $('mrSubmit').addEventListener('click', async () => {
    const item = state.item;
    const cantidad = Number(state.amount);
    if (!item || !(cantidad > 0) || !state.reason) return;
    const linea = { inventory_item_id: item.id };
    const cuerpo = { branch_id: state.data.branch.id, reason: state.reason, items: [linea] };
    let texto;
    if (item.family === 'unidad') {
      linea.quantity = String(cantidad);
      texto = `${num(cantidad)} ${cantidad === 1 ? 'pieza' : 'piezas'}`;
    } else {
      const base = cantidad * state.unit.base;   // en g o ml
      linea.quantity = String(Math.round((base / Number(item.unit_base)) * 1e6) / 1e6);
      if (item.family === 'peso') { cuerpo.weight_value = String(cantidad); cuerpo.weight_unit = state.unit.u; }
      texto = `${num(cantidad)} ${state.unit.u}`;
    }
    const btn = $('mrSubmit');
    btn.disabled = true;
    btn.querySelector('span').textContent = 'Guardando…';
    try {
      const r = await api.post('/inventory/waste', cuerpo);
      const motivo = (state.data.reasons.find((x) => x.code === state.reason) || {}).label || '';
      state.last = { id: r.id, texto: `${texto} de ${item.name}` };
      item.times += 1;
      cerrar();
      listo(`${texto} de ${item.name} · ${motivo}`);
    } catch (err) {
      utils.showToast(err.message || 'No se pudo registrar. Revisa la conexión e intenta de nuevo.', 'error');
      btn.disabled = false;
    } finally {
      btn.querySelector('span').textContent = 'Registrar merma';
    }
  });

  // ---- paso 3: listo (se cierra solo) ----
  let doneTimer = null;
  function listo(texto) {
    $('mrDoneText').textContent = texto;
    $('mrDone').hidden = false;
    const bar = $('mrDoneBar');
    bar.style.transition = 'none'; bar.style.width = '100%';
    requestAnimationFrame(() => requestAnimationFrame(() => { bar.style.transition = 'width 6s linear'; bar.style.width = '0%'; }));
    clearTimeout(doneTimer);
    doneTimer = setTimeout(otra, 6000);
    utils.renderIcons();
  }
  function otra() {
    clearTimeout(doneTimer);
    $('mrDone').hidden = true;
    state.query = ''; $('mrSearch').value = '';
    renderGrid();
  }
  $('mrAgain').addEventListener('click', otra);
  $('mrUndo').addEventListener('click', async () => {
    if (!state.last) return otra();
    clearTimeout(doneTimer);
    const b = $('mrUndo');
    b.disabled = true;
    try {
      await api.delete(`/inventory/waste/${state.last.id}?motivo=${encodeURIComponent('Deshecho en merma rápida')}`);
      utils.showToast(`Se deshizo: ${state.last.texto}.`, 'success');
      state.last = null;
      otra();
    } catch (err) {
      utils.showToast(err.message || 'No se pudo deshacer.', 'error');
      doneTimer = setTimeout(otra, 4000);
    } finally { b.disabled = false; }
  });

  // ---- foto del insumo (encargado o admin) ----
  async function achicar(file, lado = 900) {
    let tmp = null;
    try {
      let src;
      if (window.createImageBitmap) src = await createImageBitmap(file, { imageOrientation: 'from-image' });
      else {
        tmp = URL.createObjectURL(file);
        src = await new Promise((ok, mal) => { const img = new Image(); img.onload = () => ok(img); img.onerror = mal; img.src = tmp; });
      }
      const k = Math.min(1, lado / Math.max(src.width, src.height));
      const cv = document.createElement('canvas');
      cv.width = Math.round(src.width * k); cv.height = Math.round(src.height * k);
      cv.getContext('2d').drawImage(src, 0, 0, cv.width, cv.height);
      if (src.close) src.close();
      const blob = await new Promise((ok) => cv.toBlob(ok, 'image/jpeg', 0.8));
      if (blob) return blob;
    } catch (e) { /* va el original y el servidor decide */ } finally { if (tmp) URL.revokeObjectURL(tmp); }
    return file;
  }
  $('mrPhotoBtn').addEventListener('click', () => { $('mrPhotoInput').value = ''; $('mrPhotoInput').click(); });
  $('mrPhotoInput').addEventListener('change', async (e) => {
    const file = e.target.files && e.target.files[0];
    const item = state.item;
    if (!file || !item) return;
    const b = $('mrPhotoBtn');
    b.disabled = true;
    b.querySelector('span').textContent = 'Subiendo foto…';
    try {
      const fd = new FormData();
      fd.append('file', await achicar(file), 'insumo.jpg');
      const r = await api.request(`/quick-waste/items/${item.id}/photo`, { method: 'PUT', body: fd });
      item.photo_v = r.photo_v;
      $('mrItemImg').innerHTML = imagenHtml(item);
      cargarFotos($('mrItemImg'));
      renderGrid();
      utils.showToast('Foto guardada.', 'success');
    } catch (err) {
      utils.showToast(err.message || 'No se pudo subir la foto.', 'error');
    } finally {
      b.disabled = false;
      b.querySelector('span').textContent = item.photo_v ? 'Cambiar foto' : 'Poner foto';
    }
  });

  // ---- arranque ----
  try {
    await cargar(leer(BRANCH_KEY));
  } catch (err) {
    try { await cargar(null); } catch (err2) {
      $('mrGate').innerHTML = `<p>${esc(err2.message || 'No se pudo abrir la merma.')}</p><a class="mr-btn-main" href="/operacion">Volver</a>`;
      return;
    }
  }
  $('mrGate').hidden = true;
  $('mrApp').hidden = false;
  utils.renderIcons();
});
