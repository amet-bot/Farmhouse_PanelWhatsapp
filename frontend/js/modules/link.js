/**
 * Farmhouse - Link (ventas de la caja de Invu, por sucursal)
 *
 * Primera pantalla de Farmhouse Link: solo ventas, que es lo que ya hay. Recetas, consumo y
 * reposición llegan cuando exista con qué calcularlos; mientras tanto figuran en el rail como
 * "Próximamente".
 *
 * Todo sale del servidor ya sumado (/link/sales/*): acá no se recalcula nada, así la pantalla y
 * cualquier reporte futuro dicen lo mismo. La única cuenta local es el período anterior, que es
 * la misma consulta corrida sobre otras fechas.
 *
 * Color por sucursal: fijo, por el orden de config.py (CLY, CDE, VP, SF, OBR), nunca por
 * ranking. Filtrar una sucursal no le cambia el color. La paleta está validada para daltonismo
 * en claro y oscuro (skill de dataviz); en claro tres tonos quedan bajo 3:1 contra el fondo, y
 * por eso el gráfico diario tiene leyenda siempre y una vista de tabla.
 */

document.addEventListener('DOMContentLoaded', async () => {

  const $ = (id) => document.getElementById(id);
  const esc = (s) => utils.escapeHtml(s == null ? '' : String(s));

  const state = {
    user: null,
    isGlobal: false,
    branches: [],          // las de Link, en orden fijo: define el color de cada una
    range: '7',
    customFrom: null,      // rango personalizado (ISO) cuando range === 'custom'
    customTo: null,
    branchFilter: '',
    daily: [],
    showTable: false,
    view: 'ventas',
    month: null,           // AAAA-MM del cierre de mes
  };

  const moneyFmt = new Intl.NumberFormat('es-PA', { style: 'currency', currency: 'USD' });
  const moneyShortFmt = new Intl.NumberFormat('es-PA', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 });
  const numFmt = new Intl.NumberFormat('es-PA', { maximumFractionDigits: 0 });
  const money = (n) => moneyFmt.format(Number(n) || 0);
  const moneyShort = (n) => moneyShortFmt.format(Number(n) || 0);
  const num = (n) => numFmt.format(Number(n) || 0);

  // ==========================================================================
  // Fechas (el negocio vive en hora de Panamá; el navegador de la casa también)
  // ==========================================================================
  const iso = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
  const parseIso = (s) => { const [y, m, d] = s.split('-').map(Number); return new Date(y, m - 1, d); };
  const addDays = (d, n) => { const r = new Date(d); r.setDate(r.getDate() + n); return r; };
  const dayLabel = (s) => parseIso(s).toLocaleDateString('es-PA', { day: 'numeric', month: 'short' });
  const dayLabelLong = (s) => parseIso(s).toLocaleDateString('es-PA', { weekday: 'short', day: 'numeric', month: 'short' });

  function rangeDates(range) {
    const today = new Date();
    today.setHours(0, 0, 0, 0);
    if (range === 'custom' && state.customFrom && state.customTo) return [parseIso(state.customFrom), parseIso(state.customTo)];
    if (range === 'today') return [today, today];
    if (range === 'yesterday') { const y = addDays(today, -1); return [y, y]; }
    if (range === 'month') return [new Date(today.getFullYear(), today.getMonth(), 1), today];
    if (range === 'prev-month') {
      return [new Date(today.getFullYear(), today.getMonth() - 1, 1), new Date(today.getFullYear(), today.getMonth(), 0)];
    }
    const n = Number(range);
    return [addDays(today, -(n - 1)), today];
  }

  function daysBetween(from, to) {
    const out = [];
    for (let d = new Date(from); d <= to; d = addDays(d, 1)) out.push(iso(d));
    return out;
  }

  // ==========================================================================
  // Tema y sesión (utilidad compartida, ver js/shared/shell.js)
  // ==========================================================================
  FarmhouseShell.initTheme({
    // El gráfico lee los colores del tema y necesita repintarse cuando cambia.
    onThemeChange: () => { if (state.daily.length) renderDailyChart(); },
  });
  FarmhouseShell.initLogout({ redirectTo: '/' });
  window.addEventListener('auth:unauthorized', () => { window.location.href = '/'; });

  // ==========================================================================
  // Vistas
  // ==========================================================================
  const VIEWS = { ventas: 'viewVentas', analisis: 'viewAnalisis', compras: 'viewCompras', cierre: 'viewCierre', inventario: 'viewInventario', sincronizacion: 'viewSincronizacion' };

  function setView(view) {
    if (!VIEWS[view]) view = 'ventas';
    state.view = view;
    Object.entries(VIEWS).forEach(([key, id]) => { $(id).hidden = key !== view; });
    document.querySelectorAll('#linkNav .inv-nav-item').forEach((b) => b.classList.toggle('active', b.dataset.view === view));
    // Los filtros de período mandan en Ventas, Análisis y Compras; en Cierre el período es el mes.
    $('linkFilters').hidden = view === 'sincronizacion';
    $('rangeGroup').hidden = view === 'cierre';
    if (view === 'sincronizacion') loadSyncStatus();
    if (view === 'ventas' && state.daily.length) renderDailyChart();  // el ancho pudo cambiar estando oculto
    if (view === 'analisis') loadAnalisis();
    if (view === 'compras') loadCompras();
    if (view === 'cierre') loadCierre();
    if (view === 'inventario') loadInventario();
  }

  /** Recarga lo que esté a la vista (los filtros cambiaron). */
  function reloadCurrent() {
    if (state.view === 'ventas') loadSales();
    else if (state.view === 'analisis') loadAnalisis();
    else if (state.view === 'compras') loadCompras();
    else if (state.view === 'cierre') loadCierre();
    else if (state.view === 'inventario') loadInventario();
  }
  document.querySelectorAll('#linkNav .inv-nav-item').forEach((b) => b.addEventListener('click', () => setView(b.dataset.view)));

  // ==========================================================================
  // Colores por sucursal
  // ==========================================================================
  // Se busca por código y no por posición en la lista que llegó: un supervisor local recibe una
  // sola sucursal, y igual tiene que verla con su color de siempre. Cada código usa la familia de
  // color que la sucursal tiene en el resto de la suite (ver link.css).
  const BRANCH_ORDER = ['CLY', 'CDE', 'VP', 'SF', 'OBR', 'CAT'];
  const branchCodes = new Map();   // branch_id → código, llenado con lo que manda el servidor

  function branchIndex(branchId) {
    const i = BRANCH_ORDER.indexOf(branchCodes.get(branchId));
    return i < 0 ? BRANCH_ORDER.length : i;
  }
  function branchColorVar(branchId) {
    return `var(--link-series-${(branchIndex(branchId) % BRANCH_ORDER.length) + 1})`;
  }

  // ==========================================================================
  // Carga
  // ==========================================================================
  function query(from, to) {
    const p = new URLSearchParams({ date_from: iso(from), date_to: iso(to) });
    if (state.branchFilter) p.set('branch_id', state.branchFilter);
    return p.toString();
  }

  // Tocar rápido entre períodos o sucursales dejaba que una respuesta vieja llegara última y
  // pisara los KPIs y gráficos del filtro ya elegido.
  let loadSeq = 0;

  async function loadSales({ silent = false } = {}) {
    const seq = ++loadSeq;
    const [from, to] = rangeDates(state.range);
    const len = daysBetween(from, to).length;
    // Un solo día se compara con el mismo día de la semana pasada (un lunes con otro lunes:
    // contra el día anterior, un domingo siempre parecía "peor" que el sábado).
    const prevTo = len === 1 ? addDays(from, -7) : addDays(from, -1);
    const prevFrom = addDays(prevTo, -(len - 1));
    state.prevFrom = prevFrom;

    if (!silent) setLoading();
    try {
      const [daily, prevDaily, items, channels] = await Promise.all([
        api.get(`/link/sales/daily?${query(from, to)}`),
        api.get(`/link/sales/daily?${query(prevFrom, prevTo)}`),
        api.get(`/link/sales/items?${query(from, to)}&limit=15`),
        api.get(`/link/sales/channels?${query(from, to)}`),
      ]);
      if (seq !== loadSeq) return;
      state.daily = daily;
      daily.concat(prevDaily).forEach((r) => branchCodes.set(r.branch_id, r.branch_code));
      state.days = daysBetween(from, to);
      state.from = from; state.to = to;

      $('ventasSubtitle').textContent = subtitleFor(from, to, daily);
      renderKpis(daily, prevDaily);
      renderCuadre(daily);
      renderDailyChart();
      renderDailyTable();
      renderBranchBars(daily);
      renderChannelBars(channels);
      renderItems(items);
      utils.renderIcons();
      maybeRefreshToday();
    } catch (err) {
      if (seq !== loadSeq) return;
      utils.showToast(err.message || 'No se pudieron cargar las ventas.', 'error');
      // Antes los esqueletos de carga quedaban para siempre: se reemplazan por un aviso claro.
      ['dailyChart', 'branchBars', 'channelBars', 'itemsTable'].forEach((id) => {
        $(id).innerHTML = emptyHtml('No se pudieron cargar las ventas', 'Probá de nuevo en unos segundos.');
      });
      utils.renderIcons();
    }
  }

  /** "Del 22 sept al 28 sept · hoy va parcial" o, para Hoy, "Hoy, lun. 28 sept · actualizado a las 1:40 p. m.". */
  function subtitleFor(from, to, daily) {
    const hoy = iso(new Date());
    if (iso(from) === iso(to)) {
      const dia = dayLabelLong(iso(from));
      if (iso(to) !== hoy) return `${state.range === 'yesterday' ? 'Ayer, ' : ''}${dia}.`;
      // La hora de la sucursal que se actualizó hace más: "al menos hasta esta hora, todas".
      const horas = daily.filter((r) => r.synced_at).map((r) => utils._parseServerDate(r.synced_at)).filter(Boolean);
      // En hora de Panamá aunque el navegador esté en otra zona.
      const hora = horas.length
        ? new Date(Math.min(...horas.map((d) => d.getTime())))
          .toLocaleTimeString('es-PA', { hour: 'numeric', minute: '2-digit', timeZone: 'America/Panama' })
        : null;
      return `Hoy, ${dia} · va parcial${hora ? ` · actualizado a las ${hora}` : ''} · se actualiza cada media hora`;
    }
    return `Del ${dayLabel(iso(from))} al ${dayLabel(iso(to))}${iso(to) === hoy ? ' · hoy va parcial' : ''}.`;
  }

  // ==========================================================================
  // Cuadre con Invu: por día y sucursal, ¿coincide lo del sistema con el cierre de Invu?
  // ==========================================================================
  const horaPanama = (s) => {
    const d = s ? utils._parseServerDate(s) : null;
    return d ? d.toLocaleTimeString('es-PA', { hour: 'numeric', minute: '2-digit', timeZone: 'America/Panama' }) : '';
  };

  function renderCuadre(daily) {
    const hoy = iso(new Date());
    const dias = (state.days || []).slice().reverse();   // el más reciente arriba
    const filtro = state.branchFilter ? Number(state.branchFilter) : null;
    // Las sucursales que se sincronizan (orden fijo), o la elegida en el filtro.
    const sucursales = state.branches
      .filter((b) => b.configured && (!filtro || b.branch_id === filtro))
      .sort((a, b) => branchIndex(a.branch_id) - branchIndex(b.branch_id));
    const porCelda = new Map(daily.map((r) => [`${r.branch_id}|${r.business_date}`, r]));

    let ok = 0;
    const problemas = [];
    const celda = (b, d) => {
      const r = porCelda.get(`${b.branch_id}|${d}`);
      if (!r) {
        problemas.push(`${b.branch_name} ${dayLabel(d)}: sin datos`);
        return '<td class="num link-cuadre-miss" title="Ese día no se trajo de Invu">sin datos</td>';
      }
      const invu = r.invu_total != null ? Number(r.invu_total) : null;
      const nuestro = r.calculated_total != null ? Number(r.calculated_total) : null;
      if (r.has_error) {
        problemas.push(`${b.branch_name} ${dayLabel(d)}: la última actualización falló`);
        return `<td class="num link-cuadre-bad" title="La última vez que se pidió a Invu falló; se muestra lo que había">⚠ ${esc(money(r.net_total))}</td>`;
      }
      if (r.matches === false) {
        problemas.push(`${b.branch_name} ${dayLabel(d)}: Invu ${money(invu)}, sistema ${money(nuestro)}`);
        return `<td class="num link-cuadre-bad" title="Sistema: ${esc(money(nuestro))}">⚠ ${esc(money(invu))}</td>`;
      }
      ok += 1;
      const cuando = d === hoy && r.synced_at ? ` · ${horaPanama(r.synced_at)}` : '';
      return `<td class="num" title="Cuadra con Invu${esc(cuando)}">${esc(money(r.net_total))} <span class="link-cuadre-ok">✓</span></td>`;
    };

    const filas = dias.map((d) => {
      const celdas = sucursales.map((b) => celda(b, d)).join('');
      const total = sucursales.reduce((s, b) => s + (Number(porCelda.get(`${b.branch_id}|${d}`)?.net_total) || 0), 0);
      return `<tr><td>${esc(dayLabelLong(d))}${d === hoy ? ' <small>(parcial)</small>' : ''}</td>${celdas}<td class="num"><strong>${esc(money(total))}</strong></td></tr>`;
    }).join('');

    $('cuadreTable').innerHTML = sucursales.length ? `
      <table class="link-table link-cuadre-table">
        <thead><tr><th>Día</th>${sucursales.map((b) => `<th class="num">${esc(b.branch_name)}</th>`).join('')}<th class="num">Total</th></tr></thead>
        <tbody>${filas}</tbody>
      </table>` : '';

    const total = dias.length * sucursales.length;
    const hoyRows = daily.filter((r) => r.business_date === hoy && r.synced_at);
    const horaHoy = hoyRows.length
      ? horaPanama(hoyRows.map((r) => r.synced_at).sort()[0])   // la sucursal que se actualizó hace más
      : '';
    $('cuadreNote').textContent = horaHoy ? `Hoy, actualizado a las ${horaHoy}` : '';

    const resumen = $('cuadreSummary');
    if (!total) {
      resumen.textContent = 'No hay sucursales conectadas a Invu.';
    } else if (!problemas.length) {
      const quien = sucursales.length === 1 ? esc(sucursales[0].branch_name) : `las ${sucursales.length} sucursales`;
      const cuando = dias.length === 1 ? 'El día' : `Los ${dias.length} días`;
      resumen.innerHTML = `<span class="link-cuadre-ok">✓</span> ${cuando} de ${quien} <strong>cuadran con Invu</strong>. La venta que ves es la del reporte de Invu.`;
    } else {
      resumen.innerHTML = `<span class="link-cuadre-warn">⚠</span> ${problemas.length === 1 ? 'Hay 1 día que no cuadra' : `Hay ${problemas.length} días que no cuadran`} (cuadran ${ok} de ${total}): ${problemas.slice(0, 3).map(esc).join(' · ')}${problemas.length > 3 ? '…' : ''}.${state.user && state.user.role === 'admin' ? ' Tocá <strong>Actualizar ahora</strong> para volver a traer hoy y ayer.' : ''}`;
      $('cuadreDetails').open = true;
    }
  }

  // ---- Hoy al día sin tocar nada ----
  // Si el período incluye hoy, se le pide al servidor que traiga lo de hoy de Invu (él solo lo
  // hace si lo guardado tiene más de 5 minutos) y se vuelve a pintar a los pocos segundos, sin
  // esqueletos de carga. Con la pantalla abierta en un período que incluye hoy, cada 5 minutos.
  let ultimoPedidoHoy = 0;
  let recargasHoy = [];
  async function maybeRefreshToday() {
    if (!state.to || iso(state.to) !== iso(new Date())) return;
    if (Date.now() - ultimoPedidoHoy < 60 * 1000) return;   // a lo sumo un pedido por minuto desde acá
    ultimoPedidoHoy = Date.now();
    try {
      const r = await api.post('/link/sales/refresh-today', {});
      if (!r.started && !r.running) return;
      $('cuadreNote').textContent = 'Actualizando hoy con Invu…';
      recargasHoy.forEach(clearTimeout);
      // Una tanda son unos segundos por sucursal: se repinta dos veces para agarrar el final.
      recargasHoy = [15000, 35000].map((ms) => setTimeout(() => loadSales({ silent: true }), ms));
    } catch (err) {
      /* sin permiso o sin conexión: queda lo que había */
    }
  }
  setInterval(() => {
    if (!document.hidden && !$('viewVentas').hidden && state.to && iso(state.to) === iso(new Date())) {
      loadSales({ silent: true });
    }
  }, 5 * 60 * 1000);

  function setLoading() {
    ['dailyChart', 'branchBars', 'channelBars', 'itemsTable'].forEach((id) => {
      $(id).innerHTML = '<div class="link-skeleton"></div>';
    });
  }

  function emptyHtml(title, text) {
    return `<div class="inv-empty"><span class="inv-empty-icon"><i data-lucide="line-chart"></i></span><strong>${esc(title)}</strong><p>${esc(text)}</p></div>`;
  }

  // ==========================================================================
  // Fichas
  // ==========================================================================
  function totals(rows) {
    const t = { net: 0, orders: 0, items: 0, gross: 0, discount: 0 };
    rows.forEach((r) => {
      t.net += Number(r.net_total) || 0;   // la venta del día según el reporte de Invu
      t.orders += r.orders_count;
      t.items += Number(r.items_sold) || 0;
      t.gross += Number(r.gross_total) || 0;
      t.discount += Number(r.discount_total) || 0;
    });
    t.ticket = t.orders ? t.net / t.orders : 0;
    return t;
  }

  function delta(now, before) {
    if (!before) return '';
    const pct = ((now - before) / before) * 100;
    const sign = pct > 0 ? '+' : '';
    const cls = pct >= 0 ? 'up' : 'down';
    return `<span class="link-delta ${cls}"><i data-lucide="${pct >= 0 ? 'arrow-up-right' : 'arrow-down-right'}"></i>${sign}${pct.toFixed(1)}%</span>`;
  }

  function renderKpis(daily, prevDaily) {
    const t = totals(daily);
    // Hoy va a medio día: compararlo con un día entero siempre daría "−70%". Se muestra sin
    // comparación y la comparación aparece en "Ayer".
    const p = state.range === 'today' ? totals([]) : totals(prevDaily);
    const vs = state.days && state.days.length === 1 && state.prevFrom
      ? `vs. el ${dayLabelLong(iso(state.prevFrom))}`
      : 'vs. período anterior';
    const kpis = [
      { icon: 'dollar-sign', label: 'Venta neta', value: money(t.net),
        sub: (p.net ? `${delta(t.net, p.net)} ${vs}` : (state.range === 'today' ? 'Va parcial: la comparación sale en "Ayer"' : 'Sin período anterior para comparar'))
          // La misma cifra que el reporte de Invu. Hay pantallas de Invu que muestran la venta
          // ANTES de descuentos: esa va debajo, para poder comparar contra cualquiera de las dos.
          + (t.gross ? `<br>Antes de descuentos: ${esc(money(t.gross))}${t.discount ? ` (descuentos ${esc(money(t.discount))})` : ''}` : '') },
      { icon: 'receipt', label: 'Órdenes', value: num(t.orders), sub: p.orders ? `${delta(t.orders, p.orders)} ${vs}` : '&nbsp;' },
      { icon: 'wallet', label: 'Ticket promedio', value: money(t.ticket), sub: p.ticket ? `${delta(t.ticket, p.ticket)} ${vs}` : '&nbsp;' },
      { icon: 'utensils', label: 'Platos vendidos', value: num(t.items), sub: p.items ? `${delta(t.items, p.items)} ${vs}` : '&nbsp;' },
    ];
    $('kpiRow').innerHTML = kpis.map((k) => `
      <div class="inv-kpi">
        <span class="inv-kpi-label"><i data-lucide="${k.icon}"></i> ${esc(k.label)}</span>
        <span class="inv-kpi-value">${esc(k.value)}</span>
        <span class="inv-kpi-sub">${k.sub}</span>
      </div>`).join('');
  }

  // ==========================================================================
  // Venta diaria: una línea por sucursal
  // ==========================================================================
  function seriesFromDaily() {
    const byBranch = new Map();
    state.daily.forEach((r) => {
      if (!byBranch.has(r.branch_id)) byBranch.set(r.branch_id, { id: r.branch_id, name: r.branch_name, values: new Map() });
      byBranch.get(r.branch_id).values.set(r.business_date, Number(r.net_total) || 0);
    });
    // Orden fijo de Link, no por venta: la leyenda no baila al cambiar de período.
    return Array.from(byBranch.values()).sort((a, b) => branchIndex(a.id) - branchIndex(b.id));
  }

  function niceMax(v) {
    if (v <= 0) return 100;
    const pow = Math.pow(10, Math.floor(Math.log10(v)));
    const n = v / pow;
    const step = n <= 1 ? 1 : n <= 2 ? 2 : n <= 2.5 ? 2.5 : n <= 5 ? 5 : 10;
    return step * pow;
  }

  function renderDailyChart() {
    const box = $('dailyChart');
    const series = seriesFromDaily();
    const days = state.days || [];
    const single = series.length === 1;
    // Un solo día (Hoy, Ayer) no dibuja línea: el panel se esconde y queda "por sucursal".
    const panel = box.closest('.inv-panel');
    if (panel) panel.hidden = days.length === 1;
    if (days.length === 1) return;

    $('dailyTitle').textContent = single ? `Venta diaria · ${series[0].name}` : 'Venta diaria por sucursal';
    $('dailyLegend').innerHTML = single ? '' : series.map((s) => `
      <span class="link-legend-item"><span class="link-legend-swatch" style="background:${branchColorVar(s.id)}"></span>${esc(s.name)}</span>`).join('');

    if (!series.length) {
      box.innerHTML = emptyHtml('Sin ventas en este período', 'Todavía no hay días sincronizados en estas fechas.');
      utils.renderIcons();
      return;
    }

    const W = Math.max(box.clientWidth, 280);
    const H = W < 560 ? 220 : 280;
    const m = { top: 12, right: 16, bottom: 28, left: 56 };
    const iw = W - m.left - m.right;
    const ih = H - m.top - m.bottom;
    const maxV = niceMax(Math.max(...series.flatMap((s) => Array.from(s.values.values()))) * 1.05);
    const x = (i) => m.left + (days.length === 1 ? iw / 2 : (i / (days.length - 1)) * iw);
    const y = (v) => m.top + ih - (v / maxV) * ih;

    const ticks = [0, .25, .5, .75, 1].map((f) => f * maxV);
    const labelEvery = Math.ceil(days.length / Math.max(2, Math.floor(iw / 64)));

    let svg = `<svg viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" role="img" aria-label="Venta diaria">`;
    ticks.forEach((t) => {
      svg += `<line class="link-grid" x1="${m.left}" x2="${W - m.right}" y1="${y(t)}" y2="${y(t)}"/>`;
      svg += `<text class="link-axis" x="${m.left - 8}" y="${y(t) + 4}" text-anchor="end">${moneyShort(t)}</text>`;
    });
    days.forEach((d, i) => {
      if (i % labelEvery === 0 || i === days.length - 1) {
        svg += `<text class="link-axis" x="${x(i)}" y="${H - 8}" text-anchor="middle">${esc(dayLabel(d))}</text>`;
      }
    });

    // Una línea por sucursal; un día sin sincronizar corta la línea en vez de inventar un cero.
    series.forEach((s) => {
      let path = '';
      let pen = false;
      days.forEach((d, i) => {
        if (s.values.has(d)) {
          path += `${pen ? 'L' : 'M'}${x(i).toFixed(1)},${y(s.values.get(d)).toFixed(1)}`;
          pen = true;
        } else {
          pen = false;
        }
      });
      svg += `<path class="link-line" d="${path}" style="stroke:${branchColorVar(s.id)}"/>`;
    });

    svg += `<line class="link-crosshair" id="crosshair" y1="${m.top}" y2="${m.top + ih}" x1="0" x2="0" visibility="hidden"/>`;
    series.forEach((s) => {
      svg += `<circle class="link-dot" data-branch="${s.id}" r="4.5" cx="0" cy="0" visibility="hidden" style="fill:${branchColorVar(s.id)}"/>`;
    });
    svg += `<rect class="link-hit" x="${m.left}" y="${m.top}" width="${iw}" height="${ih}" tabindex="0" aria-label="Recorrer días con el mouse o las flechas"/>`;
    svg += '</svg>';
    box.innerHTML = svg;

    // ---- Capa de hover: cruz + un globo con todas las sucursales de ese día ----
    const hit = box.querySelector('.link-hit');
    const cross = box.querySelector('#crosshair');
    const dots = box.querySelectorAll('.link-dot');
    const tip = $('chartTooltip');
    let current = -1;

    function show(i, clientX, clientY) {
      current = i;
      const d = days[i];
      cross.setAttribute('x1', x(i)); cross.setAttribute('x2', x(i));
      cross.setAttribute('visibility', 'visible');
      dots.forEach((dot) => {
        const s = series.find((ss) => String(ss.id) === dot.dataset.branch);
        if (s && s.values.has(d)) {
          dot.setAttribute('cx', x(i)); dot.setAttribute('cy', y(s.values.get(d)));
          dot.setAttribute('visibility', 'visible');
        } else {
          dot.setAttribute('visibility', 'hidden');
        }
      });

      const rows = series.filter((s) => s.values.has(d)).sort((a, b) => b.values.get(d) - a.values.get(d));
      const total = rows.reduce((acc, s) => acc + s.values.get(d), 0);
      tip.replaceChildren();
      const head = document.createElement('div');
      head.className = 'link-tip-head';
      head.textContent = dayLabelLong(d) + (d === iso(new Date()) ? ' (parcial)' : '');
      tip.appendChild(head);
      if (!rows.length) {
        const none = document.createElement('div');
        none.className = 'link-tip-row';
        none.textContent = 'Día sin sincronizar';
        tip.appendChild(none);
      }
      rows.forEach((s) => {
        const row = document.createElement('div');
        row.className = 'link-tip-row';
        const sw = document.createElement('span');
        sw.className = 'link-legend-swatch';
        sw.style.background = branchColorVar(s.id);
        const val = document.createElement('strong');
        val.textContent = money(s.values.get(d));
        const name = document.createElement('span');
        name.textContent = s.name;
        row.append(sw, val, name);
        tip.appendChild(row);
      });
      if (rows.length > 1) {
        const foot = document.createElement('div');
        foot.className = 'link-tip-row link-tip-total';
        const val = document.createElement('strong');
        val.textContent = money(total);
        const name = document.createElement('span');
        name.textContent = 'Total';
        foot.append(document.createElement('span'), val, name);
        tip.appendChild(foot);
      }
      tip.hidden = false;
      const r = tip.getBoundingClientRect();
      let left = clientX + 14;
      if (left + r.width > window.innerWidth - 8) left = clientX - r.width - 14;
      tip.style.left = `${Math.max(8, left)}px`;
      tip.style.top = `${Math.max(8, clientY - r.height / 2)}px`;
    }

    function hide() {
      current = -1;
      tip.hidden = true;
      cross.setAttribute('visibility', 'hidden');
      dots.forEach((dot) => dot.setAttribute('visibility', 'hidden'));
    }

    function indexAt(clientX) {
      const rect = box.querySelector('svg').getBoundingClientRect();
      const px = (clientX - rect.left) * (W / rect.width);
      const i = days.length === 1 ? 0 : Math.round(((px - m.left) / iw) * (days.length - 1));
      return Math.min(days.length - 1, Math.max(0, i));
    }

    hit.addEventListener('pointermove', (e) => show(indexAt(e.clientX), e.clientX, e.clientY));
    hit.addEventListener('pointerleave', hide);
    hit.addEventListener('blur', hide);
    hit.addEventListener('keydown', (e) => {
      if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return;
      e.preventDefault();
      const i = current < 0 ? days.length - 1 : Math.min(days.length - 1, Math.max(0, current + (e.key === 'ArrowRight' ? 1 : -1)));
      const rect = box.querySelector('svg').getBoundingClientRect();
      show(i, rect.left + (x(i) / W) * rect.width, rect.top + rect.height / 3);
    });
  }

  // La tabla: el mismo dato del gráfico, leíble sin mouse y sin depender del color.
  function renderDailyTable() {
    const series = seriesFromDaily();
    const days = (state.days || []).slice().reverse();
    if (!series.length) { $('dailyTableWrap').innerHTML = ''; return; }
    const head = series.map((s) => `<th class="num">${esc(s.name)}</th>`).join('');
    const body = days.map((d) => {
      const cells = series.map((s) => `<td class="num">${s.values.has(d) ? money(s.values.get(d)) : '—'}</td>`).join('');
      const tot = series.reduce((acc, s) => acc + (s.values.get(d) || 0), 0);
      return `<tr><td>${esc(dayLabelLong(d))}</td>${cells}${series.length > 1 ? `<td class="num strong">${money(tot)}</td>` : ''}</tr>`;
    }).join('');
    $('dailyTableWrap').innerHTML = `
      <table class="link-table">
        <thead><tr><th>Día</th>${head}${series.length > 1 ? '<th class="num">Total</th>' : ''}</tr></thead>
        <tbody>${body}</tbody>
      </table>`;
  }

  $('btnToggleTable').addEventListener('click', () => {
    state.showTable = !state.showTable;
    $('dailyTableWrap').hidden = !state.showTable;
    $('dailyChart').hidden = state.showTable;
    $('btnToggleTable').setAttribute('aria-pressed', String(state.showTable));
    $('btnToggleTable').querySelector('span').textContent = state.showTable ? 'Ver gráfico' : 'Ver tabla';
    if (!state.showTable) renderDailyChart();
  });

  // ==========================================================================
  // Barras: por sucursal y por tipo de orden
  // ==========================================================================
  function renderBranchBars(daily) {
    const panel = $('branchPanel');
    const byBranch = new Map();
    daily.forEach((r) => {
      const e = byBranch.get(r.branch_id) || { id: r.branch_id, name: r.branch_name, net: 0, orders: 0 };
      e.net += Number(r.net_total) || 0;
      e.orders += r.orders_count;
      byBranch.set(r.branch_id, e);
    });
    const rows = Array.from(byBranch.values()).sort((a, b) => b.net - a.net);
    panel.hidden = rows.length < 2;
    if (rows.length < 2) return;
    const total = rows.reduce((acc, r) => acc + r.net, 0);
    const max = rows[0].net || 1;
    $('branchNote').textContent = `Total ${money(total)}`;
    $('branchBars').innerHTML = rows.map((r) => `
      <div class="inv-bar-row" title="${esc(r.name)}: ${money(r.net)} en ${num(r.orders)} órdenes">
        <span class="inv-bar-name"><span class="link-legend-swatch" style="background:${branchColorVar(r.id)}"></span>${esc(r.name)}</span>
        <span class="inv-bar-value">${money(r.net)} <small>${total ? ((r.net / total) * 100).toFixed(0) : 0}%</small></span>
        <span class="inv-bar-track"><span class="inv-bar-fill" style="width:${Math.max(2, (r.net / max) * 100)}%; background:${branchColorVar(r.id)}"></span></span>
      </div>`).join('');
  }

  function renderChannelBars(channels) {
    const rows = channels.filter((c) => Number(c.net_total) !== 0);
    if (!rows.length) { $('channelBars').innerHTML = emptyHtml('Sin ventas', 'Nada vendido en este período.'); return; }
    const total = rows.reduce((acc, r) => acc + Number(r.net_total), 0);
    const max = Math.max(...rows.map((r) => Number(r.net_total)), 1);
    $('channelBars').innerHTML = rows.map((r) => `
      <div class="inv-bar-row" title="${esc(r.order_type)}: ${num(r.orders)} órdenes">
        <span class="inv-bar-name">${esc(r.order_type)}</span>
        <span class="inv-bar-value">${money(r.net_total)} <small>${total ? ((Number(r.net_total) / total) * 100).toFixed(0) : 0}%</small></span>
        <span class="inv-bar-track"><span class="inv-bar-fill" style="width:${Math.max(2, (Number(r.net_total) / max) * 100)}%"></span></span>
      </div>`).join('');
  }

  // ==========================================================================
  // Platos más vendidos
  // ==========================================================================
  function renderItems(items) {
    if (!items.length) { $('itemsTable').innerHTML = emptyHtml('Sin platos vendidos', 'Nada vendido en este período.'); return; }
    const max = Number(items[0].quantity) || 1;
    const multi = state.branches.length > 1 && !state.branchFilter;
    $('itemsTable').innerHTML = `
      <table class="link-table">
        <thead><tr>
          <th class="rank">#</th><th>Plato</th><th class="hide-sm">Categoría</th>
          <th class="num">Cantidad</th><th class="num">Venta</th>${multi ? '<th class="num hide-sm">Sucursales</th>' : ''}
        </tr></thead>
        <tbody>${items.map((it, i) => `
          <tr>
            <td class="rank">${i + 1}</td>
            <td>
              <span class="link-item-name">${esc(it.name)}</span>
              <span class="link-item-bar"><span style="width:${Math.max(2, (Number(it.quantity) / max) * 100)}%"></span></span>
            </td>
            <td class="hide-sm muted">${esc(it.category || '')}</td>
            <td class="num strong">${num(it.quantity)}</td>
            <td class="num">${money(it.revenue)}</td>
            ${multi ? `<td class="num hide-sm muted">${it.branches}</td>` : ''}
          </tr>`).join('')}
        </tbody>
      </table>`;
    $('itemsNote').textContent = 'Por cantidad · agrupado por código de Invu';
  }

  // ==========================================================================
  // Sincronización
  // ==========================================================================
  function fmtDateTime(value) {
    if (!value) return '—';
    // Mismo parser que el resto del panel (utils._parseServerDate): el de acá no reconocía un
    // offset negativo ("-05:00"), le agregaba una Z y daba "Invalid Date".
    const d = utils._parseServerDate(value);
    return d.toLocaleString('es-PA', { day: 'numeric', month: 'short', hour: 'numeric', minute: '2-digit' });
  }

  async function loadSyncStatus() {
    $('syncTable').innerHTML = '<div class="link-skeleton"></div>';
    try {
      const status = await api.get('/link/invu/status');
      renderSyncTable(status);
    } catch (err) {
      $('syncTable').innerHTML = emptyHtml('No se pudo cargar', err.message || '');
      utils.renderIcons();
    }
  }

  function renderSyncTable(status) {
    const rows = status.branches;
    if (!rows.length) { $('syncTable').innerHTML = emptyHtml('Sin sucursales', 'Ninguna sucursal configurada para Link.'); utils.renderIcons(); return; }
    $('syncTable').innerHTML = `
      <table class="link-table">
        <thead><tr>
          <th>Sucursal</th><th>Estado</th><th class="num">Días</th><th class="hide-sm">Desde</th>
          <th class="hide-sm">Última actualización</th><th class="num">Menú</th>
        </tr></thead>
        <tbody>${rows.map((b) => {
          let estado;
          if (!b.configured) estado = '<span class="link-status warn"><i data-lucide="key-round"></i> Sin usuario de API</span>';
          else if (b.error_days) estado = `<span class="link-status bad" title="${esc(b.last_error || '')}"><i data-lucide="alert-triangle"></i> ${b.error_days} día(s) con error</span>`;
          else if (b.mismatched_days) estado = `<span class="link-status warn"><i data-lucide="scale"></i> ${b.mismatched_days} día(s) no cuadran</span>`;
          else if (b.days_synced) estado = '<span class="link-status ok"><i data-lucide="check-circle-2"></i> Cuadra con Invu</span>';
          else estado = '<span class="link-status warn"><i data-lucide="clock"></i> Esperando primera carga</span>';
          return `
            <tr>
              <td><span class="link-legend-swatch" style="background:${branchColorVar(b.branch_id)}"></span> <strong>${esc(b.branch_name)}</strong></td>
              <td>${estado}</td>
              <td class="num">${num(b.days_synced)}</td>
              <td class="hide-sm">${b.first_day ? esc(dayLabel(b.first_day)) : '—'}</td>
              <td class="hide-sm">${esc(fmtDateTime(b.last_synced_at))}</td>
              <td class="num">${num(b.menu_items)}</td>
            </tr>`;
        }).join('')}</tbody>
      </table>`;
    utils.renderIcons();
  }

  $('btnRefreshSales').addEventListener('click', async () => {
    const btn = $('btnRefreshSales');
    btn.disabled = true;
    btn.classList.add('loading');
    try {
      const res = await api.post('/link/invu/sync', {});
      const errores = res.flatMap((b) => b.days).filter((d) => d.error).length;
      utils.showToast(errores ? `Actualizado, pero ${errores} día(s) dieron error en Invu.` : 'Ventas de hoy y ayer actualizadas con Invu.', errores ? 'warning' : 'success');
      await loadSales();
    } catch (err) {
      utils.showToast(err.message || 'No se pudo actualizar.', 'error');
    } finally {
      btn.disabled = false;
      btn.classList.remove('loading');
    }
  });

  $('btnSyncNow').addEventListener('click', async () => {
    const btn = $('btnSyncNow');
    btn.disabled = true;
    btn.classList.add('loading');
    try {
      const res = await api.post('/link/invu/sync', {});
      const dias = res.flatMap((b) => b.days);
      const errores = dias.filter((d) => d.error).length;
      utils.showToast(errores ? `Sincronizado con ${errores} día(s) con error.` : 'Ventas de hoy y ayer al día.', errores ? 'warning' : 'success');
      await Promise.all([loadSyncStatus(), loadSales()]);
    } catch (err) {
      utils.showToast(err.message || 'No se pudo sincronizar.', 'error');
    } finally {
      btn.disabled = false;
      btn.classList.remove('loading');
    }
  });

  // ==========================================================================
  // Filtros
  // ==========================================================================
  document.querySelectorAll('#rangeSegmented button').forEach((b) => b.addEventListener('click', () => {
    document.querySelectorAll('#rangeSegmented button').forEach((x) => x.classList.toggle('active', x === b));
    state.range = b.dataset.range;
    reloadCurrent();
  }));

  $('btnApplyRange').addEventListener('click', () => {
    const from = $('dateFrom').value, to = $('dateTo').value;
    if (!from || !to) { utils.showToast('Elige las dos fechas.', 'info'); return; }
    if (from > to) { utils.showToast('La fecha inicial es posterior a la final.', 'error'); return; }
    state.customFrom = from; state.customTo = to; state.range = 'custom';
    document.querySelectorAll('#rangeSegmented button').forEach((x) => x.classList.remove('active'));
    reloadCurrent();
  });

  $('branchFilter').addEventListener('change', (e) => {
    state.branchFilter = e.target.value;
    reloadCurrent();
  });

  $('monthInput').addEventListener('change', (e) => { state.month = e.target.value || null; loadCierre(); });

  // Exportar a Excel: la sesión va en la cookie, así que se baja con fetch y se guarda el archivo.
  document.querySelectorAll('#exportMenu [data-export]').forEach((b) => b.addEventListener('click', async () => {
    const [from, to] = rangeDates(state.range);
    b.disabled = true;
    try {
      const res = await fetch(`/api/reports/export/${b.dataset.export}.xlsx?${query(from, to)}`, { credentials: 'include', headers: { 'X-Requested-With': 'XMLHttpRequest' } });
      if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || 'No se pudo exportar.');
      const blob = await res.blob();
      const disp = res.headers.get('Content-Disposition') || '';
      const name = (disp.match(/filename="([^"]+)"/) || [])[1] || `farmhouse-${b.dataset.export}.xlsx`;
      const url = URL.createObjectURL(blob);
      const a = Object.assign(document.createElement('a'), { href: url, download: name });
      document.body.appendChild(a); a.click(); a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 2000);
      $('exportMenu').open = false;
    } catch (err) {
      utils.showToast(err.message || 'No se pudo exportar.', 'error');
    } finally {
      b.disabled = false;
    }
  }));

  // ==========================================================================
  // Análisis: hora, día de la semana, categoría, plato por sucursal
  // ==========================================================================
  function barRows(containerId, rows, { name, value, sub, tone } = {}) {
    const box = $(containerId);
    if (!rows.length) { box.innerHTML = emptyHtml('Sin datos', 'Nada en este período.'); return; }
    const max = Math.max(...rows.map((r) => Number(value(r))), 0.01);
    box.innerHTML = rows.map((r) => `
      <div class="inv-bar-row" title="${esc(name(r))}: ${esc(money(value(r)))}">
        <span class="inv-bar-name">${esc(name(r))}</span>
        <span class="inv-bar-value">${esc(money(value(r)))}${sub ? ` <small>${esc(sub(r))}</small>` : ''}</span>
        <span class="inv-bar-track"><span class="inv-bar-fill" style="width:${Math.max(2, (Number(value(r)) / max) * 100)}%${tone ? `; background:${tone(r)}` : ''}"></span></span>
      </div>`).join('');
  }

  function renderHourChart(byHour) {
    const max = Math.max(...byHour.map((h) => Number(h.net)), 0.01);
    const total = byHour.reduce((a, h) => a + Number(h.net), 0);
    const pico = byHour.reduce((best, h) => (Number(h.net) > Number(best.net) ? h : best), byHour[0]);
    $('hourNote').textContent = total ? `Hora pico: ${pico.hour}:00 (${money(pico.net)})` : 'Sin ventas';
    $('hourChart').innerHTML = byHour.map((h) => `
      <div class="link-hour-col ${Number(h.net) ? '' : 'zero'}" title="${h.hour}:00 · ${num(h.orders)} órdenes · ${money(h.net)}">
        <span class="link-hour-bar" style="height:${Math.max(2, (Number(h.net) / max) * 100)}%"></span>
        <small>${h.hour}h</small>
      </div>`).join('');
  }

  function renderMatrix(m) {
    if (!m.rows.length) { $('matrixTable').innerHTML = emptyHtml('Sin platos vendidos', 'Nada vendido en este período.'); return; }
    const maxQty = Math.max(...m.rows.flatMap((r) => m.branches.map((b) => Number((r.by_branch[String(b.id)] || {}).qty || 0))), 1);
    $('matrixTable').innerHTML = `
      <table class="link-table link-matrix">
        <thead><tr><th>Plato</th><th class="hide-sm">Categoría</th><th class="num">Total</th>${m.branches.map((b) => `<th class="num"><span class="link-legend-swatch" style="background:${branchColorVar(b.id)}"></span>${esc(b.code)}</th>`).join('')}</tr></thead>
        <tbody>${m.rows.map((r) => `
          <tr>
            <td><span class="link-item-name">${esc(r.name)}</span></td>
            <td class="hide-sm muted">${esc(r.category || '')}</td>
            <td class="num strong">${num(r.total_qty)}</td>
            ${m.branches.map((b) => { const c = r.by_branch[String(b.id)]; const q = Number((c || {}).qty || 0); return `<td class="num heat" style="--heat:${(q / maxQty * 0.55).toFixed(2)}" title="${esc(b.name)}: ${num(q)} · ${money((c || {}).revenue || 0)}"><span>${q ? num(q) : '·'}</span></td>`; }).join('')}
          </tr>`).join('')}
        </tbody>
      </table>`;
  }

  let analisisSeq = 0;
  async function loadAnalisis() {
    const seq = ++analisisSeq;
    const [from, to] = rangeDates(state.range);
    ['hourChart', 'weekdayBars', 'categoryBars', 'matrixTable'].forEach((id) => { $(id).innerHTML = '<div class="inv-skeleton-row"></div>'; });
    try {
      const [time, cats, matrix] = await Promise.all([
        api.get(`/reports/sales/time?${query(from, to)}`),
        api.get(`/reports/sales/categories?${query(from, to)}`),
        api.get(`/reports/sales/dish-matrix?${query(from, to)}&limit=25`),
      ]);
      if (seq !== analisisSeq) return;
      matrix.branches.forEach((b) => branchCodes.set(b.id, b.code));
      renderHourChart(time.by_hour);
      barRows('weekdayBars', time.by_weekday, { name: (r) => r.label, value: (r) => r.avg_net_per_day, sub: (r) => `${num(r.orders)} órdenes en ${r.days} día${r.days === 1 ? '' : 's'}` });
      $('categoryNote').textContent = `Total ${money(cats.total_revenue)}`;
      barRows('categoryBars', cats.rows, { name: (r) => r.category, value: (r) => r.revenue, sub: (r) => `${r.share_pct == null ? 0 : r.share_pct}% · ${num(r.quantity)} uds` });
      renderMatrix(matrix);
      utils.renderIcons();
    } catch (err) {
      if (seq !== analisisSeq) return;
      utils.showToast(err.message || 'No se pudo cargar el análisis.', 'error');
      ['hourChart', 'weekdayBars', 'categoryBars', 'matrixTable'].forEach((id) => { $(id).innerHTML = emptyHtml('No se pudo cargar', 'Probá de nuevo en unos segundos.'); });
    }
  }

  // ==========================================================================
  // Compras y merma
  // ==========================================================================
  let comprasSeq = 0;
  async function loadCompras() {
    const seq = ++comprasSeq;
    const [from, to] = rangeDates(state.range);
    $('comprasKpis').innerHTML = '';
    ['supplierBars', 'purchaseCategoryBars', 'wasteReasonBars', 'wasteCategoryBars'].forEach((id) => { $(id).innerHTML = '<div class="inv-skeleton-row"></div>'; });
    try {
      const [compras, merma, daily] = await Promise.all([
        api.get(`/reports/purchases?${query(from, to)}`),
        api.get(`/reports/waste?${query(from, to)}`),
        api.get(`/link/sales/daily?${query(from, to)}`),
      ]);
      if (seq !== comprasSeq) return;
      const venta = totals(daily).net;
      const pct = (v) => (venta ? `${((Number(v) / venta) * 100).toFixed(1)}% de la venta` : 'Sin venta para comparar');
      const kpis = [
        { icon: 'shopping-cart', label: 'Compras', value: money(compras.total), sub: `${pct(compras.total)}${compras.lines_without_cost ? ` · ${num(compras.lines_without_cost)} línea${compras.lines_without_cost === 1 ? '' : 's'} sin costo` : ''}` },
        { icon: 'truck', label: 'Líneas recibidas', value: num(compras.lines), sub: `${num(compras.by_supplier.length)} proveedor${compras.by_supplier.length === 1 ? '' : 'es'}` },
        { icon: 'trash-2', label: 'Merma', value: money(merma.total), sub: `${pct(merma.total)}${merma.lines_without_cost ? ` · ${num(merma.lines_without_cost)} sin costo` : ''}` },
        { icon: 'dollar-sign', label: 'Venta neta', value: money(venta), sub: 'Mismo período (Invu)' },
      ];
      $('comprasKpis').innerHTML = kpis.map((k) => `
        <div class="inv-kpi">
          <span class="inv-kpi-label"><i data-lucide="${k.icon}"></i> ${esc(k.label)}</span>
          <span class="inv-kpi-value">${esc(k.value)}</span>
          <span class="inv-kpi-sub">${esc(k.sub)}</span>
        </div>`).join('');
      $('supplierNote').textContent = `Total ${money(compras.total)}`;
      barRows('supplierBars', compras.by_supplier, { name: (r) => r.supplier, value: (r) => r.amount, sub: (r) => `${r.share_pct == null ? 0 : r.share_pct}% · ${num(r.shipments)} cargamento${r.shipments === 1 ? '' : 's'}${r.issues ? ` · ${num(r.issues)} con diferencias` : ''}` });
      barRows('purchaseCategoryBars', compras.by_category, { name: (r) => r.category, value: (r) => r.amount, sub: (r) => `${r.share_pct == null ? 0 : r.share_pct}%` });
      $('wasteNote').textContent = `Total ${money(merma.total)}`;
      barRows('wasteReasonBars', merma.by_reason, { name: (r) => r.label, value: (r) => r.cost, sub: (r) => `${r.share_pct == null ? 0 : r.share_pct}% · ${num(r.lines)} registro${r.lines === 1 ? '' : 's'}`, tone: () => 'var(--inv-amber, #b45309)' });
      barRows('wasteCategoryBars', merma.by_category, { name: (r) => r.category, value: (r) => r.cost, sub: (r) => `${r.share_pct == null ? 0 : r.share_pct}%`, tone: () => 'var(--inv-amber, #b45309)' });
      utils.renderIcons();
    } catch (err) {
      if (seq !== comprasSeq) return;
      utils.showToast(err.message || 'No se pudieron cargar compras y merma.', 'error');
      ['supplierBars', 'purchaseCategoryBars', 'wasteReasonBars', 'wasteCategoryBars'].forEach((id) => { $(id).innerHTML = emptyHtml('No se pudo cargar', 'Probá de nuevo en unos segundos.'); });
    }
  }

  // ==========================================================================
  // Administración de inventario: ¿cuánto hay? ¿cuánto se gastó? ¿cuánto llegó? ¿llegó todo?
  // ==========================================================================
  let invSeq = 0;
  let invData = null;

  function renderStockTable() {
    if (!invData) return;
    const q = $('stockSearch').value.trim().toLowerCase();
    const soloValor = $('stockOnlyValue').checked;
    const branches = invData.stock.branches;
    let rows = invData.stock.items;
    if (q) rows = rows.filter((r) => `${r.name} ${r.category || ''}`.toLowerCase().includes(q));
    if (soloValor) rows = rows.filter((r) => Number(r.total_value) > 0);
    const visibles = rows.slice(0, 300);
    if (!visibles.length) { $('stockTable').innerHTML = emptyHtml('Sin existencias', q ? 'Ningún insumo coincide con la búsqueda.' : 'No hay registros de inventario todavía.'); return; }
    $('stockTable').innerHTML = `
      <table class="link-table">
        <thead><tr><th>Insumo</th><th class="hide-sm">Categoría</th>${branches.map((b) => `<th class="num"><span class="link-legend-swatch" style="background:${branchColorVar(b.id)}"></span>${esc(b.code)}</th>`).join('')}<th class="num">Total</th><th class="num">Valor</th></tr></thead>
        <tbody>${visibles.map((r) => `
          <tr>
            <td><span class="link-item-name">${esc(r.name)}</span> <small class="muted">${esc(r.unit)}</small></td>
            <td class="hide-sm muted">${esc(r.category || '')}</td>
            ${branches.map((b) => { const c = r.by_branch[String(b.id)]; const qty = c ? Number(c.qty) : 0; return `<td class="num ${qty < 0 ? 'neg' : ''}" title="${esc(b.name)}${c && c.value != null ? `: ${money(c.value)}` : ''}">${c ? num(qty) : '·'}</td>`; }).join('')}
            <td class="num strong">${num(r.total_qty)}</td>
            <td class="num">${Number(r.total_value) ? money(r.total_value) : '<span class="muted">sin costo</span>'}</td>
          </tr>`).join('')}
        </tbody>
      </table>${rows.length > 300 ? `<p class="muted" style="font-size:12px;margin:8px 0 0">Se muestran 300 de ${num(rows.length)} insumos; afina la búsqueda para ver el resto.</p>` : ''}`;
  }
  $('stockSearch').addEventListener('input', renderStockTable);
  $('stockOnlyValue').addEventListener('change', renderStockTable);

  // ¿Dónde se pierde? Faltante sin explicar por sucursal (hojas de cierre vs. ventas × recetas).
  const VAR_STATUS = { faltante: 'Faltante', sobra: 'Sobra', cuadra: 'Cuadra', manual: 'Consumo a mano', unidad: 'Unidad sin convertir', sin_receta: 'Sin receta' };
  async function loadVariance(from, to, seq) {
    const box = $('varianceBox');
    box.innerHTML = '<div class="inv-skeleton-row"></div>';
    try {
      const d = await api.get(`/reports/inventory/variance?${query(from, to)}`);
      if (seq !== invSeq) return;
      const conCierres = d.branches.filter((b) => b.closings);
      if (!conCierres.length) {
        box.innerHTML = emptyHtml('Todavía no hay cierres de turno', 'Este reporte sale de las hojas de cierre. Cuando las sucursales cierren turno dos veces o más, aquí se ve dónde se pierde.');
        return;
      }
      const sinCierre = d.branches.filter((b) => !b.closings).map((b) => b.branch.name);
      $('varianceNote').textContent = `Tolerancia ±${d.tolerance_pct}%${sinCierre.length ? ` · sin cierres: ${sinCierre.join(', ')}` : ''}`;
      box.innerHTML = conCierres.map((b) => {
        const comparables = b.rows.filter((r) => ['faltante', 'sobra', 'cuadra'].includes(r.status));
        const otros = b.rows.length - comparables.length;
        return `
          <details class="link-variance" ${Number(b.missing_cost) > 0 ? 'open' : ''}>
            <summary>
              <span class="link-item-name">${esc(b.branch.name)}</span>
              <span class="muted">${num(b.closings)} cierre${b.closings === 1 ? '' : 's'} · ${num(b.compared)} insumo${b.compared === 1 ? '' : 's'} comparado${b.compared === 1 ? '' : 's'}</span>
              <span class="link-variance-total ${Number(b.missing_cost) > 0 ? 'bad' : 'ok'}">${Number(b.missing_cost) > 0 ? `Faltante ${money(b.missing_cost)}` : 'Sin faltante'}</span>
            </summary>
            ${comparables.length ? `
            <table class="link-table">
              <thead><tr><th>Insumo</th><th class="num">Se fue</th><th class="num">Justifican las ventas</th><th class="num">Diferencia</th><th class="num hide-sm">Valor</th><th>Estado</th></tr></thead>
              <tbody>${comparables.map((r) => `
                <tr>
                  <td><span class="link-item-name">${esc(r.name)}</span></td>
                  <td class="num">${num(r.real)} ${esc(r.unit)}</td>
                  <td class="num">${num(r.expected)} ${esc(r.unit)}</td>
                  <td class="num strong">${Number(r.diff) > 0 ? '+' : ''}${num(r.diff)}${r.diff_pct != null ? ` <small class="muted">(${r.diff_pct > 0 ? '+' : ''}${r.diff_pct}%)</small>` : ''}</td>
                  <td class="num hide-sm">${r.diff_cost != null ? money(r.diff_cost) : '<span class="muted">sin costo</span>'}</td>
                  <td><span class="link-variance-chip ${r.status}">${esc(VAR_STATUS[r.status])}</span></td>
                </tr>`).join('')}</tbody>
            </table>` : '<p class="muted" style="font-size:13px;margin:8px 0">Ningún insumo de la hoja tiene receta en Invu para comparar todavía.</p>'}
            ${otros ? `<p class="muted" style="font-size:12px;margin:6px 0 0">${num(otros)} insumo${otros === 1 ? '' : 's'} sin comparar: ${b.rows.filter((r) => !['faltante', 'sobra', 'cuadra'].includes(r.status)).slice(0, 6).map((r) => `${esc(r.name)} (${esc(VAR_STATUS[r.status].toLowerCase())})`).join(', ')}${otros > 6 ? '…' : ''}.</p>` : ''}
          </details>`;
      }).join('');
    } catch (err) {
      if (seq !== invSeq) return;
      box.innerHTML = emptyHtml('No se pudo cargar', err.message || 'Probá de nuevo en unos segundos.');
    }
  }

  // Recetas: qué parte de lo vendido tiene receta y qué platos faltan (los más vendidos primero).
  const COV_LABEL = { invu: 'De Invu', misma_sucursal: 'Otra versión del plato', otra_sucursal: 'De otra sucursal', reventa: 'Reventa 1:1', sin_receta: 'Sin receta' };
  async function loadCoverage(from, to, seq) {
    const box = $('coverageBox');
    box.innerHTML = '<div class="inv-skeleton-row"></div>';
    try {
      const d = await api.get(`/reports/recipes/coverage?${query(from, to)}`);
      if (seq !== invSeq) return;
      if (!d.branches.length) { box.innerHTML = emptyHtml('Sin sucursales', ''); return; }
      box.innerHTML = d.branches.map((b) => {
        const pct = b.covered_pct == null ? 0 : b.covered_pct;
        const tono = pct >= 80 ? 'ok' : pct >= 50 ? 'mid' : 'bad';
        const partes = ['invu', 'misma_sucursal', 'otra_sucursal', 'reventa', 'sin_receta'].filter((k) => b.by_source[k]).map((k) => `<span class="link-cov-chip ${k}">${esc(COV_LABEL[k])}: ${num(b.by_source[k].dishes)}</span>`).join('');
        return `
          <details class="link-variance link-coverage" ${pct < 80 ? 'open' : ''}>
            <summary>
              <span class="link-item-name">${esc(b.branch.name)}</span>
              <span class="muted">${num(b.dishes)} platos vendidos</span>
              <span class="link-variance-total ${tono === 'bad' ? 'bad' : tono === 'ok' ? 'ok' : 'mid'}">${b.covered_pct == null ? 'Sin ventas' : `${pct}% de lo vendido con receta`}</span>
            </summary>
            <div class="link-cov-bar"><div class="${tono}" style="width:${pct}%"></div></div>
            <div class="link-cov-chips">${partes}</div>
            ${b.missing.length ? `
            <table class="link-table">
              <thead><tr><th>Plato sin receta</th><th class="num">Vendidos</th></tr></thead>
              <tbody>${b.missing.slice(0, 15).map((m) => `<tr><td><span class="link-item-name">${esc(m.name)}</span>${m.versions > 1 ? ` <small class="muted">· ${m.versions} versiones en Invu</small>` : ''}</td><td class="num strong">${num(m.sold)}</td></tr>`).join('')}</tbody>
            </table>${b.missing.length > 15 ? `<p class="muted" style="font-size:12px;margin:6px 0 0">Y ${num(b.missing.length - 15)} platos más sin receta, que se venden menos.</p>` : ''}` : '<div class="link-ok-banner"><i data-lucide="check-circle-2"></i> Todos los platos vendidos tienen receta.</div>'}
          </details>`;
      }).join('');
      utils.renderIcons();
    } catch (err) {
      if (seq !== invSeq) return;
      box.innerHTML = emptyHtml('No se pudo cargar', err.message || 'Probá de nuevo en unos segundos.');
    }
  }

  async function loadInventario() {
    const seq = ++invSeq;
    const [from, to] = rangeDates(state.range);
    $('invKpis').innerHTML = '';
    ['stockTable', 'spentTable', 'arrivedTable', 'issuesTable'].forEach((id) => { $(id).innerHTML = '<div class="inv-skeleton-row"></div>'; });
    try {
      const d = await api.get(`/reports/inventory/overview?${query(from, to)}`);
      if (seq !== invSeq) return;
      invData = d;
      d.stock.branches.forEach((b) => branchCodes.set(b.id, b.code));
      const sinConteo = d.stock.branches.filter((b) => b.days_since_count == null || b.days_since_count > 7);
      const kpis = [
        { icon: 'warehouse', label: 'Valor en existencia', value: money(d.stock.total_value), sub: sinConteo.length ? `${sinConteo.map((b) => b.name).join(', ')}: conteo de hace más de 7 días o sin conteo` : 'Todas las sucursales con conteo reciente' },
        { icon: 'flame', label: 'Consumo del período', value: money(Number(d.consumption.manual_cost) + Number(d.consumption.theoretical_cost)), sub: `${num(d.consumption.manual_records)} registro${d.consumption.manual_records === 1 ? '' : 's'} del equipo · ${num(d.consumption.items)} insumo${d.consumption.items === 1 ? '' : 's'}` },
        { icon: 'truck', label: 'Cargamentos recibidos', value: num(d.arrived.shipments), sub: `${money(d.purchases.total)} en compras ${delta(Number(d.purchases.total), Number(d.purchases.previous_total)) || ''}${d.purchases.lines_without_cost ? ` · ${num(d.purchases.lines_without_cost)} línea${d.purchases.lines_without_cost === 1 ? '' : 's'} sin costo` : ''}` },
        { icon: d.discrepancies.with_issues ? 'alert-triangle' : 'check-circle-2', label: '¿Llegó todo?', value: d.discrepancies.with_issues ? `${num(d.discrepancies.with_issues)} con diferencias` : 'Sí', sub: d.discrepancies.with_issues ? `Reclamo a proveedores: ${money(d.discrepancies.claim_total)}` : `${num(d.discrepancies.shipments)} cargamento${d.discrepancies.shipments === 1 ? '' : 's'} completos` },
      ];
      $('invKpis').innerHTML = kpis.map((k) => `
        <div class="inv-kpi">
          <span class="inv-kpi-label"><i data-lucide="${k.icon}"></i> ${esc(k.label)}</span>
          <span class="inv-kpi-value">${esc(k.value)}</span>
          <span class="inv-kpi-sub">${k.sub}</span>
        </div>`).join('');

      renderStockTable();

      const cons = d.consumption;
      $('spentNote').textContent = cons.rows.length ? `Registrado ${money(cons.manual_cost)} · estimado por ventas ${money(cons.theoretical_cost)}` : 'Sin consumo en el período';
      $('spentTable').innerHTML = cons.rows.length ? `
        <table class="link-table">
          <thead><tr><th>Insumo</th><th class="num">Registrado</th><th class="num hide-sm">Estimado por ventas</th><th class="num">Valor</th></tr></thead>
          <tbody>${cons.rows.map((r) => `
            <tr>
              <td><span class="link-item-name">${esc(r.name)}</span> <small class="muted">${esc(r.category || '')}</small></td>
              <td class="num ${Number(r.manual_qty) ? 'strong' : 'muted'}">${Number(r.manual_qty) ? `${num(r.manual_qty)} ${esc(r.unit)}` : '—'}</td>
              <td class="num hide-sm ${Number(r.theoretical_qty) ? '' : 'muted'}">${Number(r.theoretical_qty) ? `${num(r.theoretical_qty)} ${esc(r.unit)}` : '—'}</td>
              <td class="num">${Number(r.total_cost) ? money(r.total_cost) : '<span class="muted">sin costo</span>'}</td>
            </tr>`).join('')}</tbody>
        </table>` : emptyHtml('Sin consumo', 'Nadie registró consumo y no hay ventas con receta en este período.');

      $('arrivedNote').textContent = d.arrived.top_items.length ? 'Insumos que más entraron, por valor' : '';
      $('arrivedTable').innerHTML = d.arrived.top_items.length ? `
        <table class="link-table">
          <thead><tr><th>Insumo</th><th class="num">Cantidad</th><th class="num">Valor</th><th class="num hide-sm">Cargamentos</th></tr></thead>
          <tbody>${d.arrived.top_items.map((r) => `<tr><td><span class="link-item-name">${esc(r.name)}</span> <small class="muted">${esc(r.category || '')}</small></td><td class="num strong">${num(r.qty)} ${esc(r.unit)}</td><td class="num">${Number(r.amount) ? money(r.amount) : '<span class="muted">sin costo</span>'}</td><td class="num hide-sm">${num(r.shipments)}</td></tr>`).join('')}</tbody>
        </table>` : emptyHtml('Nada recibido', 'No hubo cargamentos en este período.');

      const disc = d.discrepancies;
      $('issuesNote').textContent = disc.shipments ? `${num(disc.with_issues)} de ${num(disc.shipments)} cargamentos con diferencias` : '';
      if (!disc.shipments) {
        $('issuesTable').innerHTML = emptyHtml('Sin cargamentos', 'No hubo recepciones en este período.');
      } else if (!disc.with_issues) {
        $('issuesTable').innerHTML = `<div class="link-ok-banner"><i data-lucide="check-circle-2"></i> Todo llegó completo: ${num(disc.shipments)} cargamento${disc.shipments === 1 ? '' : 's'} sin diferencias contra factura.</div>`;
      } else {
        const chips = (st) => `<span class="link-status-chips">${Object.entries(st).map(([k, v]) => `<span class="link-status-chip ${k}">${esc(disc.status_labels[k] || k)} ×${v}</span>`).join('')}</span>`;
        $('issuesTable').innerHTML = `
          <table class="link-table">
            <thead><tr><th>Cargamento</th><th>Sucursal</th><th>Proveedor</th><th>Qué pasó</th><th class="num">Reclamo</th><th></th></tr></thead>
            <tbody>${disc.rows.map((r) => `
              <tr>
                <td><span class="link-item-name">#${r.id}${r.invoice_number ? ` · Fact. ${esc(r.invoice_number)}` : ''}</span><br><small class="muted">${esc(fmtDateTime(r.received_at))} · ${esc(r.received_by)}</small></td>
                <td>${esc(r.branch_name)}</td>
                <td>${esc(r.supplier)}</td>
                <td>${chips(r.statuses)}</td>
                <td class="num ${Number(r.claim) ? 'strong' : 'muted'}">${Number(r.claim) ? money(r.claim) : '—'}</td>
                <td class="num"><a class="link-text-btn" href="/inventario?view=cargamentos&shipment=${r.id}">Ver</a></td>
              </tr>`).join('')}</tbody>
          </table>
          ${disc.by_supplier.filter((p) => p.with_issues).length ? `<p class="muted" style="font-size:12.5px;margin:10px 0 0">Por proveedor: ${disc.by_supplier.filter((p) => p.with_issues).map((p) => `${esc(p.supplier)} ${num(p.with_issues)}/${num(p.shipments)}${Number(p.claim) ? ` (${money(p.claim)})` : ''}`).join(' · ')}</p>` : ''}`;
      }
      loadVariance(from, to, seq);
      loadCoverage(from, to, seq);
      utils.renderIcons();
    } catch (err) {
      if (seq !== invSeq) return;
      utils.showToast(err.message || 'No se pudo cargar el inventario.', 'error');
      ['stockTable', 'spentBars', 'arrivedTable', 'issuesTable'].forEach((id) => { $(id).innerHTML = emptyHtml('No se pudo cargar', 'Probá de nuevo en unos segundos.'); });
    }
  }

  // ==========================================================================
  // Cierre de mes
  // ==========================================================================
  let cierreSeq = 0;
  async function loadCierre() {
    const seq = ++cierreSeq;
    if (!state.month) { const t = new Date(); state.month = `${t.getFullYear()}-${String(t.getMonth() + 1).padStart(2, '0')}`; $('monthInput').value = state.month; }
    $('cierreTable').innerHTML = '<div class="inv-skeleton-row"></div>';
    try {
      const p = new URLSearchParams({ month: state.month });
      if (state.branchFilter) p.set('branch_id', state.branchFilter);
      const d = await api.get(`/reports/month-close?${p}`);
      if (seq !== cierreSeq) return;
      const t = d.current.total, pv = d.previous.total;
      const pctTxt = (v) => (v == null ? '—' : `${v}%`);
      const kpis = [
        { icon: 'dollar-sign', label: 'Venta neta', value: money(t.sales_net), sub: `${delta(Number(t.sales_net), Number(pv.sales_net))} vs. mes anterior (${money(pv.sales_net)})` },
        { icon: 'receipt', label: 'Órdenes', value: num(t.orders), sub: `Ticket promedio ${money(t.avg_ticket)}` },
        { icon: 'shopping-cart', label: 'Compras', value: money(t.purchases), sub: `${pctTxt(t.purchases_pct_sales)} de la venta · antes ${pctTxt(pv.purchases_pct_sales)}` },
        { icon: 'trash-2', label: 'Merma', value: money(t.waste_cost), sub: `${pctTxt(t.waste_pct_sales)} de la venta · antes ${pctTxt(pv.waste_pct_sales)}` },
      ];
      $('cierreKpis').innerHTML = kpis.map((k) => `
        <div class="inv-kpi">
          <span class="inv-kpi-label"><i data-lucide="${k.icon}"></i> ${esc(k.label)}</span>
          <span class="inv-kpi-value">${esc(k.value)}</span>
          <span class="inv-kpi-sub">${k.sub}</span>
        </div>`).join('');
      $('cierreNote').textContent = `${dayLabel(d.current.date_from)} al ${dayLabel(d.current.date_to)} · ${num(t.days_synced)} días-sucursal con ventas`;
      const rows = d.current.branches;
      const fila = (b, cls = '') => `
        <tr class="${cls}">
          <td>${cls ? '' : `<span class="link-legend-swatch" style="background:${branchColorVar(b.branch_id)}"></span> `}<strong>${esc(b.branch_name)}</strong></td>
          <td class="num">${money(b.sales_net)}</td>
          <td class="num hide-sm">${num(b.orders)}</td>
          <td class="num hide-sm">${money(b.avg_ticket)}</td>
          <td class="num">${money(b.purchases)} <small class="muted">${pctTxt(b.purchases_pct_sales)}</small>${b.purchase_lines_without_cost ? ` <small class="warn" title="líneas sin costo">+${num(b.purchase_lines_without_cost)} s/c</small>` : ''}</td>
          <td class="num ${b.waste_pct_sales != null && b.waste_pct_sales > 3 ? 'bad' : ''}">${money(b.waste_cost)} <small class="muted">${pctTxt(b.waste_pct_sales)}</small></td>
          <td class="num hide-sm">${money(b.count_missing_cost)}</td>
          <td class="num hide-sm">${num(b.counts)}</td>
          <td class="num hide-sm">${num(b.incidents)}</td>
        </tr>`;
      $('cierreTable').innerHTML = rows.length ? `
        <table class="link-table">
          <thead><tr><th>Sucursal</th><th class="num">Venta</th><th class="num hide-sm">Órdenes</th><th class="num hide-sm">Ticket</th><th class="num">Compras</th><th class="num">Merma</th><th class="num hide-sm">Faltantes conteo</th><th class="num hide-sm">Conteos</th><th class="num hide-sm">Incidencias</th></tr></thead>
          <tbody>${rows.map((b) => fila(b)).join('')}${rows.length > 1 ? fila(t, 'total') : ''}</tbody>
        </table>` : emptyHtml('Sin datos', 'No hay ventas sincronizadas para ese mes.');
      utils.renderIcons();
    } catch (err) {
      if (seq !== cierreSeq) return;
      utils.showToast(err.message || 'No se pudo cargar el cierre.', 'error');
      $('cierreTable').innerHTML = emptyHtml('No se pudo cargar', 'Probá de nuevo en unos segundos.');
    }
  }

  let resizeTimer;
  window.addEventListener('resize', () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => { if (!$('viewVentas').hidden && !state.showTable) renderDailyChart(); }, 150);
  });

  // ==========================================================================
  // Arranque
  // ==========================================================================
  const user = await auth.checkSession();
  if (!user) { window.location.href = '/'; return; }
  state.user = user;
  $('linkGate').hidden = true;

  if (user.role !== 'admin' && user.role !== 'supervisor') {
    $('linkDenied').hidden = false;
    utils.renderIcons();
    return;
  }

  $('linkMain').hidden = false;
  FarmhouseShell.fillUserHeader({ nameId: 'linkAgentName', roleId: 'linkAgentRole', avatarId: 'linkAgentAvatar' }, user);
  state.isGlobal = user.role === 'admin' || !user.branch_id;
  $('btnSyncNow').hidden = user.role !== 'admin';
  $('btnRefreshSales').hidden = user.role !== 'admin';

  try {
    const status = await api.get('/link/invu/status');
    state.branches = status.branches;
    state.branches.forEach((b) => branchCodes.set(b.branch_id, b.branch_code));
  } catch (err) {
    utils.showToast('No se pudo leer el estado de Link.', 'error');
  }

  const urlParams = new URLSearchParams(window.location.search);
  if (urlParams.get('date_from') && urlParams.get('date_to')) {
    state.customFrom = urlParams.get('date_from'); state.customTo = urlParams.get('date_to'); state.range = 'custom';
    $('dateFrom').value = state.customFrom; $('dateTo').value = state.customTo;
    document.querySelectorAll('#rangeSegmented button').forEach((x) => x.classList.remove('active'));
  }
  if (urlParams.get('view') && VIEWS[urlParams.get('view')]) state.view = urlParams.get('view');

  if (state.isGlobal && state.branches.length > 1) {
    $('branchFilter').innerHTML = `<option value="">Todas las sucursales</option>${state.branches.map((b) => `<option value="${b.branch_id}">${esc(b.branch_name)}</option>`).join('')}`;
    $('branchFilter').hidden = false;
    $('linkScopeValue').textContent = 'Todas las sucursales';
  } else {
    $('linkScopeValue').textContent = user.branch ? user.branch.name : (state.branches[0]?.branch_name || '—');
  }

  await loadSales();

  // Fase 5: "Administración → Integraciones" del hub linkea acá con ?view=sincronizacion en vez
  // de reconstruir esta pantalla — el estado de la sincronización con Invu ya vivía en esta
  // pestaña, solo hacía falta un acceso más directo que "Reportes → Ventas Invu".
  if (state.view !== 'ventas') setView(state.view);
  utils.renderIcons();
});
