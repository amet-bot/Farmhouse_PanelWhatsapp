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
    branchFilter: '',
    daily: [],
    showTable: false,
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
  const VIEWS = { ventas: 'viewVentas', sincronizacion: 'viewSincronizacion' };

  function setView(view) {
    Object.entries(VIEWS).forEach(([key, id]) => { $(id).hidden = key !== view; });
    document.querySelectorAll('#linkNav .inv-nav-item').forEach((b) => b.classList.toggle('active', b.dataset.view === view));
    if (view === 'sincronizacion') loadSyncStatus();
    if (view === 'ventas' && state.daily.length) renderDailyChart();  // el ancho pudo cambiar estando oculto
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
    loadSales();
  }));

  $('branchFilter').addEventListener('change', (e) => {
    state.branchFilter = e.target.value;
    loadSales();
  });

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
  if (new URLSearchParams(window.location.search).get('view') === 'sincronizacion') {
    setView('sincronizacion');
  }
  utils.renderIcons();
});
