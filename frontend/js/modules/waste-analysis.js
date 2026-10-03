/**
 * Farmhouse - Análisis de merma (Reportes → Merma)
 *
 * Antes vivía en Inventario → Merma → Análisis; se mudó a Reportes porque es un reporte (se
 * mira, no se carga nada). Registrar merma sigue en Operación / Inventario.
 *
 * Todos los números salen del servidor ya calculados:
 *   GET  /inventory/waste/analytics?date_from&date_to[&branch_id]     → KPIs, tendencia y barras
 *   GET  /inventory/waste/recipe-usage?date_from&date_to[&branch_id]  → cruce con recetas de Invu
 *   POST /inventory/invu/sync-recipes                                 → traer recetas (integrations.manage)
 *
 * Módulo autocontenido: usa solo los globales `api`, `utils` y `lucide` (vía utils.renderIcons).
 * La página anfitriona trae el marcado (ids wasteKpis, wasteDayChart, etc.) y llama:
 *
 *   WasteAnalysis.init({ isGlobalScope, branches: [{ id, name }], canSyncRecipes, scopeName })
 *   WasteAnalysis.load()   // cada vez que la vista se muestra
 */
(function () {
  'use strict';

  const $ = (id) => document.getElementById(id);
  const esc = (v) => utils.escapeHtml(v);

  // ==========================================================================
  // Formato (copiado de inventory.js para no depender de su closure)
  // ==========================================================================
  const moneyFormatter = new Intl.NumberFormat('es-PA', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const money = (n) => `$${moneyFormatter.format(Number(n || 0))}`;

  /** Cantidades con hasta 3 decimales pero sin ceros de relleno: 2.500 → "2.5", 3.000 → "3". */
  const qty = (n) => {
    const num = Number(n || 0);
    return String(Number(num.toFixed(3)));
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

  const pluralize = (n, one, many) => `${n} ${n === 1 ? one : many}`;

  const emptyStateHtml = (icon, title, text) => `
    <div class="inv-empty">
      <span class="inv-empty-icon"><i data-lucide="${icon}"></i></span>
      <strong>${esc(title)}</strong>
      <p>${esc(text)}</p>
    </div>`;

  // ==========================================================================
  // Estado
  // ==========================================================================
  const wasteAnalysis = {
    period: '30', branch: '', metric: 'cost', data: null, recipes: null, seq: 0,
    // Lo que manda la página anfitriona (init):
    isGlobalScope: false, branches: [], canSyncRecipes: false, scopeName: '',
    branchesFetched: false, wired: false,
  };

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

  /** La vista está a la vista (no oculta con `hidden` ni por un ancestro). */
  function isShown() {
    const box = $('wasteAnalysis');
    return !!box && box.getClientRects().length > 0;
  }

  // ==========================================================================
  // Controles (período, sucursal, medida)
  // ==========================================================================
  function segmentado(id, attr, onPick) {
    document.querySelectorAll(`#${id} [data-${attr}]`).forEach((b) => b.addEventListener('click', () => {
      document.querySelectorAll(`#${id} [data-${attr}]`).forEach((x) => x.classList.toggle('active', x === b));
      onPick(b.dataset[attr]);
    }));
  }

  function wireControls() {
    if (wasteAnalysis.wired) return;
    wasteAnalysis.wired = true;
    segmentado('wastePeriod', 'period', (p) => { wasteAnalysis.period = p; loadWasteAnalysis(); });
    segmentado('wasteMetric', 'metric', (m) => { wasteAnalysis.metric = m; renderWasteAnalysis(); });
    $('wasteAnalysisBranch')?.addEventListener('change', (e) => { wasteAnalysis.branch = e.target.value; loadWasteAnalysis(); });

    // El gráfico se dibuja al ancho real de su caja: al cambiar el tamaño de la ventana se redibuja.
    let trendResizeTimer = null;
    window.addEventListener('resize', () => {
      clearTimeout(trendResizeTimer);
      trendResizeTimer = setTimeout(() => { if (isShown()) renderWasteTrend(); }, 150);
    });
  }

  /**
   * Quien ve todas las sucursales elige una. La lista que manda la página puede ser parcial
   * (Reportes solo conoce las que venden por Invu), así que se pide la completa una vez, igual
   * que Inventario; si falla, queda la que mandó la página.
   */
  async function ensureBranches() {
    if (!wasteAnalysis.isGlobalScope || wasteAnalysis.branchesFetched) return;
    wasteAnalysis.branchesFetched = true;
    try {
      const list = await api.get('/branches/');
      if (Array.isArray(list) && list.length) {
        wasteAnalysis.branches = list.map((b) => ({ id: b.id, name: b.name }));
      }
    } catch (err) {
      // Sin la lista completa se usa la que mandó la página: no vale la pena un aviso.
    }
  }

  function fillBranchSelect() {
    const sel = $('wasteAnalysisBranch');
    if (!sel) return;
    if (wasteAnalysis.isGlobalScope && !sel.options.length) {
      sel.innerHTML = '<option value="">Todas las sucursales</option>' +
        wasteAnalysis.branches.map((b) => `<option value="${esc(b.id)}">${esc(b.name)}</option>`).join('');
      sel.value = wasteAnalysis.branch;
    }
    sel.hidden = !wasteAnalysis.isGlobalScope;
  }

  // ==========================================================================
  // Carga
  // ==========================================================================
  async function loadWasteAnalysis() {
    const seq = ++wasteAnalysis.seq;
    $('wasteKpis').innerHTML = '<div class="inv-kpi inv-kpi-skeleton"></div>'.repeat(4);

    await ensureBranches();
    if (seq !== wasteAnalysis.seq) return;
    fillBranchSelect();

    const { from, to } = periodoMerma(wasteAnalysis.period);
    const params = new URLSearchParams({ date_from: from, date_to: to });
    if (wasteAnalysis.branch) params.set('branch_id', wasteAnalysis.branch);
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

  // ==========================================================================
  // Tendencia
  // ==========================================================================
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
      box.innerHTML = emptyStateHtml('bar-chart-3', 'Sin mermas en el período', 'Cuando se registren, aquí se ve cuánto se perdió en cada tramo.');
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
    // Ancho real de la caja (en el celular, 375 px menos márgenes): el SVG nunca se sale.
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

  // ==========================================================================
  // Merma × recetas de Invu
  // ==========================================================================
  /** Merma contra el uso real en platos (recetas de Invu) y los platos más afectados. */
  function renderWasteRecipes() {
    const r = wasteAnalysis.recipes;
    const body = $('wasteRecipeBody');
    if (!body) return;
    const puedeTraer = wasteAnalysis.canSyncRecipes;
    const botonTraer = puedeTraer
      ? `<button type="button" class="inv-secondary-btn" id="btnSyncRecipes"${r?.recipes_running ? ' disabled' : ''}>
           <i data-lucide="refresh-cw"></i> <span>${r?.recipes_running ? 'Trayendo recetas…' : 'Actualizar recetas de Invu'}</span>
         </button>`
      : '';

    if (!r) {
      body.innerHTML = emptyStateHtml('chef-hat', 'No se pudo cruzar con las recetas', 'Prueba de nuevo en un momento.');
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
          r.recipes_running ? 'Son cientos de platos y modificadores: tarda unos 12 minutos por sucursal. Vuelve a abrir esta vista más tarde.'
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
      : `${emptyStateHtml('bar-chart-3', 'Sin mermas en el período', 'Cuando se registren, aquí se ve qué parte de cada insumo se botó frente a lo que se usó.')}
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
          ? 'Se están trayendo las recetas de Invu. Tarda unos 12 minutos por sucursal; puedes seguir usando el sistema.'
          : 'Ya se estaban trayendo las recetas.', 'success');
        if (wasteAnalysis.recipes) wasteAnalysis.recipes.recipes_running = true;
        renderWasteRecipes();
      } catch (err) {
        utils.showToast(err.message || 'No se pudieron pedir las recetas.', 'error');
      }
    });
  }

  // ==========================================================================
  // KPIs y barras
  // ==========================================================================
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
    const sucursal = a.branch_id
      ? (wasteAnalysis.branches.find((b) => String(b.id) === String(a.branch_id))?.name || wasteAnalysis.scopeName || '')
      : 'todas las sucursales';

    $('wasteAnalysisRange').textContent = `Del ${fechaCorta(a.date_from)} al ${fechaCorta(a.date_to)}${sucursal ? ` · ${sucursal}` : ''}`;

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
    const verSucursales = wasteAnalysis.isGlobalScope && !a.branch_id;
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

  // ==========================================================================
  // API pública
  // ==========================================================================
  /**
   * Una vez, antes del primer load(). Repetirlo solo actualiza las opciones.
   *   isGlobalScope   ve todas las sucursales (admin, o supervisor sin sucursal): muestra el
   *                   selector de sucursal y el panel "Por sucursal".
   *   branches        [{ id, name }] para el selector (se completa con /branches/ si se puede).
   *   canSyncRecipes  tiene el permiso integrations.manage: muestra "Actualizar recetas de Invu".
   *   scopeName       nombre de su sucursal (para el rótulo del rango si no está en branches).
   */
  function init(opts = {}) {
    wasteAnalysis.isGlobalScope = !!opts.isGlobalScope;
    wasteAnalysis.branches = Array.isArray(opts.branches) ? opts.branches.slice() : [];
    wasteAnalysis.canSyncRecipes = !!opts.canSyncRecipes;
    wasteAnalysis.scopeName = opts.scopeName || '';
    wireControls();
  }

  /** Pide y pinta el análisis con el período, sucursal y medida elegidos. */
  function load() {
    if (!wasteAnalysis.wired) wireControls();
    return loadWasteAnalysis();
  }

  window.WasteAnalysis = { init, load };
})();
