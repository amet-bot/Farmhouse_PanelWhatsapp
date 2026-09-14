/**
 * Farmhouse WhatsApp Center - Editor Visual de Flujo del Bot
 * Pestaña "Flujo visual" (solo admin): arma, conecta y prueba un diagrama de referencia del
 * bot con nodos arrastrables, y lo guarda de verdad en el backend (tabla bot_flows).
 *
 * IMPORTANTE — alcance deliberado de esta versión: el bot real de WhatsApp
 * (routers/webhooks.py::_process_auto_flow_background) es mucho más complejo que lo que este
 * editor puede modelar hoy — sucursal dinámica entre 5 sucursales, intenciones universales que
 * interrumpen desde cualquier punto (reiniciar/cancelar/cambiar sucursal/atrás), y un
 * sub-flujo anidado de 4 preguntas para pedidos corporativos. Este editor es un diagrama de
 * referencia + un simulador de "Probar flujo" que corre enteramente en el navegador — no
 * controla al bot real. Conectar una ejecución real a este diagrama es un paso aparte,
 * intencionalmente no incluido todavía.
 */

const flowEditorModule = (function () {
  "use strict";

  const ICONS = {
    trigger: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5"><circle cx="8" cy="8" r="6.3"/><path d="M6.6 5.3l4 2.7-4 2.7z" fill="currentColor" stroke="none"/></svg>',
    message: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"><path d="M2 3.2h12v7.4H6.6L3.4 13V10.6H2z"/></svg>',
    question: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round" stroke-linecap="round"><path d="M2 3.2h12v7.4H8l-2 2.6v-2.6H2z"/><path d="M6.4 6.1c.2-.7.8-1.1 1.6-1.1.9 0 1.6.5 1.6 1.2 0 .6-.4.9-.9 1.2-.4.3-.7.5-.7 1"/><circle cx="8" cy="9.7" r=".15" fill="currentColor"/></svg>',
    capture: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><rect x="1.6" y="3.6" width="12.8" height="8.8" rx="1.4"/><path d="M4.4 8h7.2M4.4 10.2h4.4"/></svg>',
    delay: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5"><circle cx="8" cy="8.4" r="5.6"/><path d="M8 5.4V8.4l2.2 1.3"/><path d="M6 2h4" stroke-linecap="round"/></svg>',
    action: '<svg viewBox="0 0 16 16" fill="currentColor" stroke="none"><path d="M8.6 1.4 3.2 9.2h3.4l-.9 5.4 6-8.4H8.2z"/></svg>',
    end: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5"><circle cx="8" cy="8" r="6.2"/><circle cx="8" cy="8" r="2.3" fill="currentColor" stroke="none"/></svg>'
  };
  // Nombres pensados para que cualquiera entienda el diagrama sin explicación previa —
  // "Pregunta (botones)" y "Pregunta abierta" distinguen a simple vista si el cliente elige
  // una opción o escribe su propia respuesta, que es la duda más común al ver el flujo.
  const TYPE_LABEL = {
    trigger: 'Disparador', message: 'Mensaje', question: 'Pregunta (botones)',
    capture: 'Pregunta abierta', delay: 'Espera', action: 'Acción', end: 'Fin',
  };
  // Una frase breve por tipo, en el orden en que de verdad aparecen en una conversación.
  // Se usa tanto en los tooltips de la paleta como en la leyenda de "Cómo leer esto".
  const TYPE_HELP = {
    trigger: 'Dónde arranca el flujo. Solo puede haber uno.',
    message: 'El bot envía un texto y sigue solo, sin esperar respuesta.',
    question: 'El bot muestra hasta 3 botones; cada uno lleva por un camino distinto.',
    capture: 'El bot pregunta y espera que el cliente escriba lo que sea (sin botones).',
    delay: 'Una pausa antes de seguir, para que no se sienta como una respuesta robótica.',
    action: 'El sistema hace algo por su cuenta (pausar el bot, avisar al equipo, etc.).',
    end: 'Aquí termina ese camino de la conversación.',
  };
  const TYPE_ORDER = ['trigger', 'message', 'question', 'capture', 'delay', 'action', 'end'];
  // Estos "kind" son solo ilustrativos en esta versión: no existe todavía un motor real
  // conectado que los interprete (a diferencia del panel de catering).
  const ACTION_KINDS = ['Pausar automatización y notificar al equipo', 'Agregar nota interna'];
  const PALETTE_TYPES = ['message', 'question', 'capture', 'delay', 'action', 'end'];

  function newNodeDefaults(type) {
    if (type === 'message') return { name: 'Nuevo mensaje', text: 'escribe aquí lo que dirá el bot…' };
    if (type === 'question') return { name: 'Nueva pregunta', text: '¿qué le preguntamos al cliente?', options: ['Opción 1', 'Opción 2'] };
    if (type === 'capture') return { name: 'Nueva pregunta abierta', text: '¿qué le preguntamos?', field: 'Dato' };
    if (type === 'delay') return { name: 'Nueva espera', seconds: 1 };
    if (type === 'action') return { name: 'Nueva acción', kind: ACTION_KINDS[0], note: '' };
    if (type === 'end') return { name: 'Nuevo fin' };
  }

  // ---------------------------------------------------------------- estado
  let flowKey = null;
  let graph = { nodes: [], conns: [] };
  let originalGraphJson = '';
  let selectedNodeId = null;
  let selectedConnId = null;
  let activeTab = 'test';
  let idCounter = 1000;
  let mounted = false;

  // Zoom: el lienzo entero (nodos + SVG) se escala con un solo transform CSS, así que todo
  // — el tamaño de las tarjetas, el grosor de los puertos, las curvas — se agranda o
  // achica junto, sin tener que recalcular cada valor a mano. Lo único que hay que corregir
  // en el código es la geometría que se MIDE o se ARRASTRA en píxeles de pantalla
  // (portPoint, el arrastre de nodos, el arrastre de una conexión): esos sí necesitan
  // dividir por zoomScale para seguir hablando en las coordenadas "reales" del diagrama.
  // ZOOM_STEP es para los botones +/- (un clic, un salto perceptible está bien).
  // ZOOM_WHEEL_STEP es más chico porque la rueda dispara muchos eventos seguidos en un
  // solo gesto de scroll — con el mismo paso que los botones, cada "tick" se sentía brusco.
  const ZOOM_MIN = 0.4, ZOOM_MAX = 1.75, ZOOM_STEP = 0.15, ZOOM_WHEEL_STEP = 0.05;
  let zoomScale = 1;

  let root, toolbar, workspace, canvasViewport, canvas, svg, connGroup, inspBody, saveBtn, dirtyFlag, statNodesEl, statConnsEl, zoomLabelEl;

  function nodeById(id) { return graph.nodes.find(function (n) { return n.id === id; }) || null; }
  function connsFrom(id, port) { return graph.conns.filter(function (c) { return c.from === id && (port === undefined || c.fromPort === port); }); }

  // ---------------------------------------------------------------- montaje del shell (una sola vez)
  function ensureShell() {
    if (mounted) return;
    root = document.getElementById('flowEditorRoot');
    root.innerHTML =
      '<div class="flow-toolbar">' +
        '<span class="flow-stats"><span><b id="flowStatNodes">0</b> nodos</span><span><b id="flowStatConns">0</b> conexiones</span></span>' +
        '<span class="flow-dirty-flag" id="flowDirtyFlag"><span class="dot"></span>Sin guardar</span>' +
        '<span class="flow-layout-group" role="group" aria-label="Organizar el diagrama">' +
          '<button type="button" class="btn-flow-layout" id="btnLayoutVertical" title="Organizar en vertical: de arriba hacia abajo">' +
            '<svg viewBox="0 0 16 16" fill="currentColor"><rect x="3" y="2.3" width="10" height="2.6" rx="0.8"/><rect x="3" y="6.7" width="10" height="2.6" rx="0.8"/><rect x="3" y="11.1" width="10" height="2.6" rx="0.8"/></svg>' +
            'Vertical' +
          '</button>' +
          '<button type="button" class="btn-flow-layout" id="btnLayoutHorizontal" title="Organizar en horizontal: de izquierda a derecha">' +
            '<svg viewBox="0 0 16 16" fill="currentColor"><rect x="2.3" y="3" width="2.6" height="10" rx="0.8"/><rect x="6.7" y="3" width="2.6" height="10" rx="0.8"/><rect x="11.1" y="3" width="2.6" height="10" rx="0.8"/></svg>' +
            'Horizontal' +
          '</button>' +
        '</span>' +
        '<span class="spacer"></span>' +
        '<button type="button" class="btn-flow-discard" id="btnFlowDiscard">' +
          '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"><path d="M13.5 8a5.5 5.5 0 1 1-1.6-3.9"/><path d="M13.5 2.5v3.5H10"/></svg>' +
          'Descartar cambios' +
        '</button>' +
        '<button type="button" class="btn-flow-save" id="btnFlowSave">' +
          '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M2.5 2.5h9l2 2v9h-11z"/><path d="M5 2.5V6h5V2.5M4.5 9.5h7v4h-7z"/></svg>' +
          'Guardar cambios' +
        '</button>' +
      '</div>' +
      '<div class="flow-workspace">' +
        '<aside class="flow-palette">' +
          '<div class="flow-palette-title">Agregar nodo</div><div id="flowPaletteList"></div>' +
          '<p class="flow-palette-hint">Arrastra las tarjetas por su cabecera. Une un punto de salida con la entrada de otro nodo para conectarlos.</p>' +
          '<div class="flow-legend"><div class="flow-palette-title">Cómo leer esto</div>' +
            '<p class="flow-legend-intro">Se lee de arriba hacia abajo, siguiendo las flechas. Cada color es un tipo de paso distinto:</p>' +
            '<div id="flowLegendList"></div>' +
          '</div>' +
        '</aside>' +
        // El control de zoom vive FUERA del contenedor que hace scroll (flowCanvasOuter, que
        // no se mueve) y no adentro de él (flowViewport, el que sí se mueve) — si viviera
        // adentro, se iría de la pantalla apenas alguien recorriera un diagrama grande.
        '<div class="flow-canvas-outer" id="flowCanvasOuter">' +
          '<div class="flow-canvas-viewport" id="flowViewport"><div class="flow-canvas" id="flowCanvas"><svg class="flow-conn-svg" id="flowConnSvg"><g id="flowConnGroup"></g></svg></div></div>' +
          '<div class="flow-zoom-controls" role="group" aria-label="Zoom del diagrama">' +
            '<button type="button" id="btnZoomOut" title="Alejar" aria-label="Alejar">' +
              '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"><circle cx="6.8" cy="6.8" r="5"/><path d="M13.8 13.8l-2.9-2.9M4.4 6.8h4.8"/></svg>' +
            '</button>' +
            '<button type="button" id="btnZoomReset" class="flow-zoom-pct" title="Restablecer zoom">100%</button>' +
            '<button type="button" id="btnZoomIn" title="Acercar" aria-label="Acercar">' +
              '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"><circle cx="6.8" cy="6.8" r="5"/><path d="M13.8 13.8l-2.9-2.9M6.8 4.4v4.8M4.4 6.8h4.8"/></svg>' +
            '</button>' +
          '</div>' +
        '</div>' +
        '<aside class="flow-inspector">' +
          '<div class="flow-insp-tabs"><button class="flow-insp-tab" id="flowTabEdit" data-tab="edit">Editar nodo</button><button class="flow-insp-tab active" id="flowTabTest" data-tab="test">Probar flujo</button></div>' +
          '<div class="flow-insp-body" id="flowInspBody"></div>' +
        '</aside>' +
      '</div>';

    toolbar = root.querySelector('.flow-toolbar');
    workspace = root.querySelector('.flow-workspace');
    canvasViewport = document.getElementById('flowViewport');
    canvas = document.getElementById('flowCanvas');
    svg = document.getElementById('flowConnSvg');
    connGroup = document.getElementById('flowConnGroup');
    inspBody = document.getElementById('flowInspBody');
    saveBtn = document.getElementById('btnFlowSave');
    dirtyFlag = document.getElementById('flowDirtyFlag');
    statNodesEl = document.getElementById('flowStatNodes');
    statConnsEl = document.getElementById('flowStatConns');
    zoomLabelEl = document.getElementById('btnZoomReset');

    renderPalette();
    renderLegend();

    document.getElementById('flowTabEdit').addEventListener('click', function () { setTab('edit'); });
    document.getElementById('flowTabTest').addEventListener('click', function () { setTab('test'); });
    document.getElementById('btnFlowDiscard').addEventListener('click', discardChanges);
    saveBtn.addEventListener('click', saveGraph);

    document.getElementById('btnLayoutVertical').addEventListener('click', function () { autoLayout('vertical'); });
    document.getElementById('btnLayoutHorizontal').addEventListener('click', function () { autoLayout('horizontal'); });

    document.getElementById('btnZoomOut').addEventListener('click', function () { setZoom(zoomScale - ZOOM_STEP); });
    document.getElementById('btnZoomIn').addEventListener('click', function () { setZoom(zoomScale + ZOOM_STEP); });
    document.getElementById('btnZoomReset').addEventListener('click', function () { setZoom(1); });
    // Ctrl/Cmd + rueda del mouse para acercar/alejar sin salir del teclado — el mismo gesto
    // que ya usan Figma, Miro, Google Maps, etc. La rueda sola sigue haciendo scroll normal.
    canvasViewport.addEventListener('wheel', function (e) {
      if (!e.ctrlKey && !e.metaKey) return;
      e.preventDefault();
      setZoom(zoomScale + (e.deltaY < 0 ? ZOOM_WHEEL_STEP : -ZOOM_WHEEL_STEP));
    }, { passive: false });

    // Arrastrar el fondo vacío del lienzo lo desplaza libremente (como Figma/Miro), en vez de
    // depender solo de las barras de scroll del navegador (que además ahora están ocultas por CSS).
    canvas.addEventListener('pointerdown', function (e) {
      if (e.target !== canvas && e.target !== svg) return;
      const startX = e.clientX, startY = e.clientY;
      const startScrollLeft = canvasViewport.scrollLeft, startScrollTop = canvasViewport.scrollTop;
      let moved = false;
      canvasViewport.classList.add('panning');

      function move(ev) {
        const dx = ev.clientX - startX, dy = ev.clientY - startY;
        if (!moved && (Math.abs(dx) > 3 || Math.abs(dy) > 3)) moved = true;
        canvasViewport.scrollLeft = startScrollLeft - dx;
        canvasViewport.scrollTop = startScrollTop - dy;
      }
      function up() {
        document.removeEventListener('pointermove', move);
        document.removeEventListener('pointerup', up);
        canvasViewport.classList.remove('panning');
        // Si el mouse casi no se movió, fue un clic (deseleccionar), no un intento de arrastrar el lienzo.
        if (!moved) {
          selectedNodeId = null; selectedConnId = null;
          renderNodes(); redrawConnections(); renderInspector();
        }
      }
      document.addEventListener('pointermove', move);
      document.addEventListener('pointerup', up);
    });

    mounted = true;
  }

  // ---------------------------------------------------------------- abrir / cargar
  async function open(key) {
    ensureShell();
    flowKey = key;
    selectedNodeId = null; selectedConnId = null; activeTab = 'test';
    setZoom(1); // cada vez que se abre un flujo arranca al 100%, no en el zoom que quedó de la vez anterior
    inspBody.innerHTML = '<div class="flow-empty-insp">Cargando…</div>';
    canvas.innerHTML = '<svg class="flow-conn-svg" id="flowConnSvg"><g id="flowConnGroup"></g></svg>';
    svg = document.getElementById('flowConnSvg');
    connGroup = document.getElementById('flowConnGroup');

    try {
      const data = await api.get(`/bot-flows/${key}`);
      graph = data.graph && Array.isArray(data.graph.nodes) ? data.graph : { nodes: [], conns: [] };
      originalGraphJson = JSON.stringify(graph);
      const subtitle = document.getElementById('flowEditorSubtitle');
      if (subtitle) subtitle.textContent = data.name + (data.updated_by ? ` · última edición: ${data.updated_by}` : '');
      renderNodes();
      requestAnimationFrame(redrawConnections);
      setTab('test');
      updateDirtyUI();
    } catch (err) {
      inspBody.innerHTML = '';
      canvas.innerHTML = `<div class="flow-editor-error" style="padding:24px">No se pudo cargar el flujo. ${utils.escapeHtml((err && err.message) || '')}</div>`;
      console.warn('[FlowEditor] Error cargando flujo:', err);
    }
  }

  function hasUnsavedChanges() {
    return mounted && flowKey && JSON.stringify(graph) !== originalGraphJson;
  }

  function markDirty() { updateDirtyUI(); }

  function updateDirtyUI() {
    const dirty = hasUnsavedChanges();
    if (dirtyFlag) dirtyFlag.classList.toggle('show', dirty);
    if (saveBtn) saveBtn.disabled = !dirty;
  }

  function discardChanges() {
    if (!hasUnsavedChanges()) return;
    if (!window.confirm('¿Descartar los cambios y volver a la última versión guardada?')) return;
    graph = JSON.parse(originalGraphJson);
    selectedNodeId = null; selectedConnId = null;
    renderNodes(); redrawConnections(); renderInspector(); updateDirtyUI();
    utils.showToast('Cambios descartados', 'info');
  }

  async function saveGraph() {
    if (!hasUnsavedChanges() || !flowKey) return;
    saveBtn.disabled = true;
    try {
      const data = await api.put(`/bot-flows/${flowKey}`, { graph });
      originalGraphJson = JSON.stringify(data.graph);
      updateDirtyUI();
      utils.showToast('Flujo guardado', 'success');
    } catch (err) {
      utils.showToast((err && err.message) || 'No se pudo guardar el flujo', 'error');
      saveBtn.disabled = false;
    }
  }

  // ---------------------------------------------------------------- render nodos
  function renderNodes() {
    canvas.querySelectorAll('.fnode').forEach(function (el) { el.remove(); });
    graph.nodes.forEach(function (node) { canvas.appendChild(buildNodeEl(node)); });
    sizeCanvas();
    if (statNodesEl) statNodesEl.textContent = graph.nodes.length;
    if (statConnsEl) statConnsEl.textContent = graph.conns.length;
  }

  function buildNodeEl(node) {
    const el = document.createElement('div');
    el.className = 'fnode' + (node.id === selectedNodeId ? ' selected' : '');
    el.dataset.id = node.id;
    el.style.left = node.x + 'px';
    el.style.top = node.y + 'px';
    el.style.width = node.w + 'px';
    el.style.setProperty('--type-color', 'var(--flow-' + node.type + ')');

    const head = document.createElement('div');
    head.className = 'fnode-head';
    head.innerHTML =
      '<span class="fnode-icon">' + ICONS[node.type] + '</span>' +
      '<span class="fnode-title"></span>' +
      '<span class="fnode-tag">' + TYPE_LABEL[node.type] + '</span>' +
      (node.type === 'trigger' ? '' :
        '<button type="button" class="fnode-del" title="Eliminar nodo" aria-label="Eliminar nodo">' +
        '<svg viewBox="0 0 16 16" width="11" height="11" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"><path d="M3.5 3.5l9 9M12.5 3.5l-9 9"/></svg></button>');
    head.querySelector('.fnode-title').textContent = node.name;
    el.appendChild(head);

    const body = document.createElement('div');
    body.className = 'fnode-body';

    if (node.type === 'question') {
      if (node.text) { const p = document.createElement('p'); p.className = 'muted-line'; p.textContent = node.text; body.appendChild(p); }
      node.options.forEach(function (opt, i) {
        const row = document.createElement('div'); row.className = 'fopt-row';
        const chip = document.createElement('span'); chip.className = 'fopt-chip'; chip.textContent = opt; row.appendChild(chip);
        const port = document.createElement('span'); port.className = 'fport fport-out'; port.dataset.node = node.id; port.dataset.port = i; row.appendChild(port);
        body.appendChild(row);
      });
    } else if (node.type === 'message') {
      const p = document.createElement('p'); p.textContent = node.text || ''; body.appendChild(p);
    } else if (node.type === 'capture') {
      const p = document.createElement('p'); p.textContent = node.text || ''; body.appendChild(p);
      const m = document.createElement('p'); m.className = 'muted-line'; m.textContent = 'Se guarda como: ' + node.field; body.appendChild(m);
    } else if (node.type === 'delay') {
      const p = document.createElement('p'); p.textContent = 'Espera ' + node.seconds + ' segundo' + (node.seconds === 1 ? '' : 's') + ' antes de responder.'; body.appendChild(p);
    } else if (node.type === 'action') {
      const p = document.createElement('p'); p.textContent = node.kind || ''; body.appendChild(p);
      if (node.note) { const m = document.createElement('p'); m.className = 'muted-line'; m.textContent = node.note; body.appendChild(m); }
    } else if (node.type === 'trigger') {
      const p = document.createElement('p'); p.className = 'muted-line'; p.textContent = node.text || 'Punto de partida del flujo.'; body.appendChild(p);
    } else if (node.type === 'end') {
      const p = document.createElement('p'); p.className = 'muted-line'; p.textContent = 'La conversación termina aquí.'; body.appendChild(p);
    }
    el.appendChild(body);

    if (node.type !== 'trigger') {
      const inPort = document.createElement('span'); inPort.className = 'fport fport-in'; inPort.dataset.node = node.id; el.appendChild(inPort);
    }
    if (node.type !== 'question' && node.type !== 'end') {
      const outPort = document.createElement('span'); outPort.className = 'fport fport-out-single fport-out'; outPort.dataset.node = node.id; outPort.dataset.port = 0; el.appendChild(outPort);
    }

    head.addEventListener('pointerdown', function (e) { startDragNode(e, node, el); });
    // Clic en el cuerpo del nodo (no en la cabecera, eso ya lo maneja startDragNode): solo
    // seleccionar, sin reconstruir el lienzo — mismo motivo que en applySelectionUI.
    el.addEventListener('pointerdown', function () { applySelectionUI(node.id); });
    const delBtn = head.querySelector('.fnode-del');
    if (delBtn) delBtn.addEventListener('click', function (e) { e.stopPropagation(); deleteNode(node.id); });
    el.querySelectorAll('.fport-out').forEach(function (p) {
      p.addEventListener('pointerdown', function (e) { e.stopPropagation(); startConnect(e, node.id, parseInt(p.dataset.port, 10)); });
    });
    return el;
  }

  // Margen "de sobra" alrededor del diagrama para que el desplazamiento (arrastrar con mouse,
  // dos dedos en trackpad) se sienta libre en vez de topar con una pared justo después del
  // último nodo — antes el margen era de solo 100/220px y por eso se sentía atrapado.
  const CANVAS_FREE_MARGIN = 600;
  function sizeCanvas() {
    let maxX = 1400, maxY = 1400;
    graph.nodes.forEach(function (n) {
      maxX = Math.max(maxX, n.x + n.w + CANVAS_FREE_MARGIN);
      maxY = Math.max(maxY, n.y + CANVAS_FREE_MARGIN);
    });
    canvas.style.width = maxX + 'px'; canvas.style.height = maxY + 'px';
    svg.setAttribute('width', maxX); svg.setAttribute('height', maxY);
    svg.setAttribute('viewBox', '0 0 ' + maxX + ' ' + maxY);
  }

  // ---------------------------------------------------------------- zoom
  // El lienzo (nodos + SVG) es un único elemento que se escala con CSS transform, así que
  // el ancho/alto lógico que fija sizeCanvas() no cambia — solo se ve más grande o más chico.
  function setZoom(newScale) {
    zoomScale = Math.round(Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, newScale)) * 100) / 100;
    canvas.style.transform = 'scale(' + zoomScale + ')';
    canvas.style.transformOrigin = '0 0';
    if (zoomLabelEl) zoomLabelEl.textContent = Math.round(zoomScale * 100) + '%';
  }

  // ---------------------------------------------------------------- conexiones
  function portPoint(nodeId, kind, portIndex, canvasRect) {
    const sel = kind === 'in' ? `.fport-in[data-node="${nodeId}"]` : `.fport-out[data-node="${nodeId}"][data-port="${portIndex}"]`;
    const el = canvas.querySelector(sel);
    if (!el) return null;
    const r = el.getBoundingClientRect();
    const c = canvasRect || canvas.getBoundingClientRect();
    // getBoundingClientRect() da píxeles YA escalados por el zoom (es lo que se ve en
    // pantalla); se divide por zoomScale para volver a las coordenadas lógicas del lienzo,
    // que es en las que están dibujadas las curvas del SVG (su viewBox no cambia con el zoom).
    return { x: (r.left - c.left + r.width / 2) / zoomScale, y: (r.top - c.top + r.height / 2) / zoomScale };
  }

  function curvePath(p1, p2) {
    const dx = p2.x - p1.x, dy = p2.y - p1.y;
    let cx1, cy1, cx2, cy2;
    if (Math.abs(dy) >= Math.abs(dx)) { cx1 = p1.x; cy1 = p1.y + dy * 0.5; cx2 = p2.x; cy2 = p2.y - dy * 0.5; }
    else { cx1 = p1.x + dx * 0.5; cy1 = p1.y; cx2 = p2.x - dx * 0.5; cy2 = p2.y; }
    return `M ${p1.x} ${p1.y} C ${cx1} ${cy1}, ${cx2} ${cy2}, ${p2.x} ${p2.y}`;
  }

  // Reconstruye TODAS las conexiones desde cero. Se usa cuando la forma del grafo cambia de
  // verdad (cargar, agregar/borrar nodo, conectar/desconectar, seleccionar, redimensionar) —
  // nunca durante un arrastre, que usa updateConnectionsForNode() en su lugar (ver más abajo
  // por qué: reconstruir las conexiones en cada cuadro es lo que se sentía "trabado").
  function redrawConnections() {
    connGroup.innerHTML = '';
    const canvasRect = canvas.getBoundingClientRect();
    graph.conns.forEach(function (c, idx) {
      const p1 = portPoint(c.from, 'out', c.fromPort, canvasRect);
      const p2 = portPoint(c.to, 'in', 0, canvasRect);
      if (!p1 || !p2) return;
      const d = curvePath(p1, p2);

      const visible = document.createElementNS('http://www.w3.org/2000/svg', 'path');
      visible.setAttribute('d', d);
      visible.setAttribute('data-conn-idx', idx);
      visible.setAttribute('class', 'fconn fconn-visible' + (selectedConnId === idx ? ' selected' : ''));
      visible.style.pointerEvents = 'none';
      connGroup.appendChild(visible);

      const hit = document.createElementNS('http://www.w3.org/2000/svg', 'path');
      hit.setAttribute('d', d);
      hit.setAttribute('data-conn-idx', idx);
      hit.classList.add('fconn-hit');
      hit.style.pointerEvents = 'stroke'; hit.style.strokeWidth = '10'; hit.style.opacity = '0'; hit.style.fill = 'none'; hit.style.stroke = '#000';
      hit.addEventListener('pointerdown', function (e) { e.stopPropagation(); selectedConnId = (selectedConnId === idx ? null : idx); redrawConnections(); });
      connGroup.appendChild(hit);

      const midx = (p1.x + p2.x) / 2, midy = (p1.y + p2.y) / 2;
      const g = document.createElementNS('http://www.w3.org/2000/svg', 'g');
      g.setAttribute('data-conn-idx', idx);
      g.setAttribute('class', 'fconn-del-group');
      g.setAttribute('transform', `translate(${midx},${midy})`);
      g.innerHTML = '<g class="fconn-del"><circle r="9"></circle><text x="0" y="3.5" text-anchor="middle">×</text></g>';
      g.querySelector('.fconn-del').addEventListener('pointerdown', function (e) {
        e.stopPropagation();
        graph.conns.splice(idx, 1);
        selectedConnId = null;
        redrawConnections(); markDirty();
        if (statConnsEl) statConnsEl.textContent = graph.conns.length;
      });
      connGroup.appendChild(g);
    });
  }

  // Actualiza en el sitio (sin recrear nada) solo las conexiones que tocan un nodo — lo que
  // de verdad se necesita cuadro a cuadro mientras se arrastra una tarjeta. Con esto, mover
  // un nodo que participa en pocas conexiones no obliga a recalcular todas las del diagrama.
  function updateConnectionsForNode(nodeId, connIndices, canvasRect) {
    connIndices.forEach(function (idx) {
      const c = graph.conns[idx];
      if (!c) return;
      const p1 = portPoint(c.from, 'out', c.fromPort, canvasRect);
      const p2 = portPoint(c.to, 'in', 0, canvasRect);
      if (!p1 || !p2) return;
      const d = curvePath(p1, p2);
      const visible = connGroup.querySelector(`.fconn-visible[data-conn-idx="${idx}"]`);
      const hit = connGroup.querySelector(`.fconn-hit[data-conn-idx="${idx}"]`);
      const group = connGroup.querySelector(`.fconn-del-group[data-conn-idx="${idx}"]`);
      if (visible) visible.setAttribute('d', d);
      if (hit) hit.setAttribute('d', d);
      if (group) group.setAttribute('transform', `translate(${(p1.x + p2.x) / 2},${(p1.y + p2.y) / 2})`);
    });
  }

  // ---------------------------------------------------------------- arrastrar nodos
  function startDragNode(e, node, el) {
    if (e.button !== undefined && e.button !== 0) return;
    e.preventDefault();
    e.stopPropagation(); // evita que el pointerdown burbujee y dispare el listener de selección de "el" por separado (ver más abajo)
    const startX = e.clientX, startY = e.clientY, ox = node.x, oy = node.y;
    // Solo las conexiones que tocan ESTE nodo pueden moverse al arrastrarlo — el resto del
    // diagrama es geométricamente ajeno al gesto, así que no hay razón para tocarlo.
    const connIndices = [];
    graph.conns.forEach(function (c, i) { if (c.from === node.id || c.to === node.id) connIndices.push(i); });
    let raf = null;
    function move(ev) {
      // El mouse se mueve en píxeles de PANTALLA; si el lienzo está al 50%, mover el mouse
      // 100px debe correr el nodo 200px lógicos para que la tarjeta siga el cursor 1 a 1.
      node.x = Math.max(0, ox + (ev.clientX - startX) / zoomScale);
      node.y = Math.max(0, oy + (ev.clientY - startY) / zoomScale);
      el.style.left = node.x + 'px'; el.style.top = node.y + 'px';
      if (!raf) raf = requestAnimationFrame(function () {
        if (connIndices.length) updateConnectionsForNode(node.id, connIndices, canvas.getBoundingClientRect());
        raf = null;
      });
    }
    function up() {
      document.removeEventListener('pointermove', move);
      document.removeEventListener('pointerup', up);
      // El tamaño del lienzo y todas las conexiones se recalculan UNA vez al soltar,
      // no en cada cuadro del arrastre.
      sizeCanvas();
      redrawConnections();
      markDirty();
    }
    document.addEventListener('pointermove', move);
    document.addEventListener('pointerup', up);
    applySelectionUI(node.id);
  }

  // ---------------------------------------------------------------- conectar nodos
  function startConnect(e, fromId, fromPort) {
    e.preventDefault();
    const c = canvas.getBoundingClientRect();
    const start = portPoint(fromId, 'out', fromPort);
    const ghost = document.createElementNS('http://www.w3.org/2000/svg', 'path');
    ghost.setAttribute('class', 'fconn-ghost');
    connGroup.appendChild(ghost);
    function move(ev) { ghost.setAttribute('d', curvePath(start, { x: (ev.clientX - c.left) / zoomScale, y: (ev.clientY - c.top) / zoomScale })); }
    function up(ev) {
      document.removeEventListener('pointermove', move);
      document.removeEventListener('pointerup', up);
      ghost.remove();
      const target = document.elementFromPoint(ev.clientX, ev.clientY);
      if (target && target.classList.contains('fport-in')) {
        const toId = target.dataset.node;
        if (toId && toId !== fromId) {
          graph.conns = graph.conns.filter(function (cc) { return !(cc.from === fromId && cc.fromPort === fromPort); });
          graph.conns.push({ from: fromId, fromPort: fromPort, to: toId });
          redrawConnections(); markDirty();
          if (statConnsEl) statConnsEl.textContent = graph.conns.length;
          utils.showToast('Conexión creada', 'info');
        }
      }
    }
    document.addEventListener('pointermove', move);
    document.addEventListener('pointerup', up);
  }

  // ---------------------------------------------------------------- selección / borrado

  // Selección "liviana": marca un nodo como seleccionado sin reconstruir el lienzo. Es la que
  // se usa en un simple clic o al EMPEZAR un arrastre — si en ese momento se llamara a
  // renderNodes() (como hacía selectNode antes de este arreglo), reemplazaría el elemento DOM
  // justo cuando el arrastre está a punto de agarrarlo, y el arrastre seguiría moviendo un
  // elemento ya desconectado de la pantalla: la caja se veía "trabada" hasta soltar y volver
  // a hacer clic, que es cuando por fin se dibujaba en su posición real.
  function applySelectionUI(id) {
    const prev = canvas.querySelector('.fnode.selected');
    if (prev && prev.dataset.id !== id) prev.classList.remove('selected');
    const now = canvas.querySelector(`.fnode[data-id="${id}"]`);
    if (now) now.classList.add('selected');
    selectedNodeId = id; selectedConnId = null;
    setTab('edit');
    renderInspector();
  }

  function deleteNode(id) {
    const node = nodeById(id);
    if (node.type === 'trigger') { utils.showToast('El disparador no se puede eliminar', 'warning'); return; }
    graph.nodes = graph.nodes.filter(function (n) { return n.id !== id; });
    graph.conns = graph.conns.filter(function (c) { return c.from !== id && c.to !== id; });
    if (selectedNodeId === id) selectedNodeId = null;
    renderNodes(); redrawConnections(); renderInspector(); markDirty();
  }

  // ---------------------------------------------------------------- inspector
  function setTab(tab) {
    activeTab = tab;
    document.getElementById('flowTabEdit').classList.toggle('active', tab === 'edit');
    document.getElementById('flowTabTest').classList.toggle('active', tab === 'test');
    renderInspector();
  }

  function renderInspector() {
    inspBody.innerHTML = '';
    if (activeTab === 'test') { renderPreview(); return; }

    const node = selectedNodeId ? nodeById(selectedNodeId) : null;
    if (!node) {
      inspBody.innerHTML =
        '<div class="flow-empty-insp">' +
        '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.3"><rect x="2" y="2" width="12" height="12" rx="2"/><path d="M5 8h6M8 5v6"/></svg>' +
        '<span>Selecciona un nodo del lienzo para editarlo, o agrega uno nuevo desde la paleta.</span></div>';
      return;
    }

    addField('Nombre del nodo', function (wrap) {
      const input = mkInput('text', node.name);
      input.addEventListener('input', function () { node.name = input.value; updateNodeTitle(node); markDirty(); });
      wrap.appendChild(input);
    });

    if (node.type === 'message' || node.type === 'trigger') {
      addField(node.type === 'message' ? 'Mensaje que envía el bot' : 'Descripción (opcional)', function (wrap) {
        const ta = mkTextarea(node.text);
        ta.addEventListener('input', function () { node.text = ta.value; refreshNodeBody(node); markDirty(); });
        wrap.appendChild(ta);
      }, { grow: true });
    }
    if (node.type === 'delay') {
      addField('Segundos de espera', function (wrap) {
        const input = mkInput('number', node.seconds); input.min = 1; input.max = 15;
        input.addEventListener('input', function () { node.seconds = Math.max(1, parseInt(input.value, 10) || 1); refreshNodeBody(node); markDirty(); });
        wrap.appendChild(input);
        const hint = document.createElement('div'); hint.className = 'fhint';
        hint.textContent = 'Pausa antes de contestar, para que la conversación se sienta escrita por una persona.';
        wrap.appendChild(hint);
      });
    }
    if (node.type === 'capture') {
      addField('Pregunta (respuesta libre)', function (wrap) {
        const ta = mkTextarea(node.text);
        ta.addEventListener('input', function () { node.text = ta.value; refreshNodeBody(node); markDirty(); });
        wrap.appendChild(ta);
      }, { grow: true });
      addField('Nombre del dato', function (wrap) {
        const input = mkInput('text', node.field);
        input.addEventListener('input', function () { node.field = input.value; refreshNodeBody(node); markDirty(); });
        wrap.appendChild(input);
        const hint = document.createElement('div'); hint.className = 'fhint';
        hint.textContent = 'Así aparece esta respuesta en el resumen que recibe el equipo, ej. "Cantidad de personas: 45".';
        wrap.appendChild(hint);
      });
    }
    if (node.type === 'question') {
      addField('Pregunta / mensaje previo', function (wrap) {
        const ta = mkTextarea(node.text || '');
        ta.addEventListener('input', function () { node.text = ta.value; refreshNodeBody(node); markDirty(); });
        wrap.appendChild(ta);
      }, { grow: true });
      addField('Botones de respuesta', function (wrap) {
        const list = document.createElement('div'); list.className = 'fopt-list';
        node.options.forEach(function (opt, i) {
          const row = document.createElement('div'); row.className = 'fopt-item';
          const input = mkInput('text', opt);
          input.addEventListener('input', function () { node.options[i] = input.value; refreshNodeBody(node); markDirty(); });
          row.appendChild(input);
          const rm = document.createElement('button'); rm.type = 'button'; rm.className = 'fopt-remove'; rm.title = 'Quitar opción';
          rm.innerHTML = '<svg viewBox="0 0 16 16" width="10" height="10" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"><path d="M3.5 3.5l9 9M12.5 3.5l-9 9"/></svg>';
          rm.addEventListener('click', function () {
            graph.conns = graph.conns.filter(function (c) { return !(c.from === node.id && c.fromPort === i); });
            graph.conns.forEach(function (c) { if (c.from === node.id && c.fromPort > i) c.fromPort--; });
            node.options.splice(i, 1);
            renderNodes(); redrawConnections(); renderInspector(); markDirty();
          });
          row.appendChild(rm);
          list.appendChild(row);
        });
        wrap.appendChild(list);
        const add = document.createElement('button'); add.type = 'button'; add.className = 'fopt-add';
        add.textContent = node.options.length >= 3 ? 'Máximo 3 botones (límite de WhatsApp)' : '+ Agregar opción';
        add.disabled = node.options.length >= 3;
        add.addEventListener('click', function () { node.options.push('Nueva opción'); refreshNodeBody(node); renderInspector(); markDirty(); });
        wrap.appendChild(add);
      });
    }
    if (node.type === 'action') {
      addField('Qué hace esta acción', function (wrap) {
        const sel = document.createElement('select');
        ACTION_KINDS.forEach(function (k) { const o = document.createElement('option'); o.value = k; o.textContent = k; if (k === node.kind) o.selected = true; sel.appendChild(o); });
        sel.addEventListener('change', function () { node.kind = sel.value; refreshNodeBody(node); markDirty(); });
        wrap.appendChild(sel);
      });
      addField('Nota interna (opcional)', function (wrap) {
        const ta = mkTextarea(node.note || '');
        ta.addEventListener('input', function () { node.note = ta.value; refreshNodeBody(node); markDirty(); });
        wrap.appendChild(ta);
      }, { grow: true });
    }
  }

  function addField(label, build, opts) {
    const f = document.createElement('div'); f.className = 'ffield' + (opts && opts.grow ? ' grow' : '');
    const l = document.createElement('label'); l.textContent = label; f.appendChild(l);
    build(f); inspBody.appendChild(f);
  }
  function mkInput(type, val) { const i = document.createElement('input'); i.type = type; i.value = val == null ? '' : val; return i; }
  function mkTextarea(val) { const t = document.createElement('textarea'); t.value = val || ''; return t; }

  function updateNodeTitle(node) {
    const el = canvas.querySelector(`.fnode[data-id="${node.id}"] .fnode-title`);
    if (el) el.textContent = node.name;
  }
  function refreshNodeBody(node) {
    const el = canvas.querySelector(`.fnode[data-id="${node.id}"]`);
    if (!el) return;
    const sx = canvasViewport.scrollLeft, sy = canvasViewport.scrollTop;
    el.replaceWith(buildNodeEl(node));
    canvasViewport.scrollLeft = sx; canvasViewport.scrollTop = sy;
    redrawConnections(); sizeCanvas();
  }

  // ---------------------------------------------------------------- paleta
  function renderPalette() {
    const list = document.getElementById('flowPaletteList');
    list.innerHTML = '';
    PALETTE_TYPES.forEach(function (type) {
      const btn = document.createElement('button');
      btn.type = 'button'; btn.className = 'flow-palette-item';
      btn.title = TYPE_HELP[type];
      btn.innerHTML = `<span class="flow-palette-icon" style="--type-color:var(--flow-${type})">${ICONS[type]}</span><span class="flow-palette-label">${TYPE_LABEL[type]}</span>`;
      btn.addEventListener('click', function () { addNode(type); });
      list.appendChild(btn);
    });
  }

  // Leyenda de "cómo leer esto": a diferencia de la paleta (que solo ofrece los tipos que se
  // pueden agregar), incluye TAMBIÉN "Disparador" y "Fin" — para que alguien que solo mira el
  // diagrama, sin intención de editarlo, entienda igual cada color que ve en el lienzo.
  function renderLegend() {
    const list = document.getElementById('flowLegendList');
    if (!list) return;
    list.innerHTML = '';
    TYPE_ORDER.forEach(function (type) {
      const row = document.createElement('div');
      row.className = 'flow-legend-item';
      row.innerHTML = `<span class="flow-legend-dot" style="--type-color:var(--flow-${type})"></span>` +
        `<span><span class="flow-legend-label">${TYPE_LABEL[type]}</span><span class="flow-legend-desc">${TYPE_HELP[type]}</span></span>`;
      list.appendChild(row);
    });
  }

  // ---------------------------------------------------------------- organizar automático
  // "Vertical" (de arriba hacia abajo) y "Horizontal" (de izquierda a derecha): recalculan
  // la posición de TODOS los nodos según qué tan lejos está cada uno del disparador, sin
  // tocar su contenido ni sus conexiones. El usuario puede seguir arrastrando cualquier
  // tarjeta después para ajustar a mano.

  // Distancia de cada nodo al disparador, contando el camino MÁS LARGO que llega a él —
  // así un nodo al que convergen dos ramas (como "Fin") siempre queda dibujado después de
  // ambas, nunca a mitad de una de ellas.
  function computeDepths() {
    const depth = {};
    graph.nodes.forEach(function (n) { depth[n.id] = (n.type === 'trigger') ? 0 : null; });
    let changed = true, guard = 0;
    while (changed && guard < 50) {
      changed = false; guard++;
      graph.conns.forEach(function (c) {
        const fromDepth = depth[c.from];
        if (fromDepth == null) return;
        const proposed = fromDepth + 1;
        if (depth[c.to] == null || proposed > depth[c.to]) { depth[c.to] = proposed; changed = true; }
      });
    }
    // Un nodo que quedó sin ninguna conexión de entrada (huérfano) no tiene de dónde heredar
    // profundidad; se agrupa junto al disparador en vez de desaparecer del cálculo.
    graph.nodes.forEach(function (n) { if (depth[n.id] == null) depth[n.id] = 0; });
    return depth;
  }

  function measuredNodeHeight(id) {
    const el = canvas.querySelector(`.fnode[data-id="${id}"]`);
    if (!el) return 100;
    return el.getBoundingClientRect().height / zoomScale;
  }

  function autoLayout(direction) {
    const depth = computeDepths();
    const levels = {};
    graph.nodes.forEach(function (n) { (levels[depth[n.id]] = levels[depth[n.id]] || []).push(n); });
    const levelKeys = Object.keys(levels).map(Number).sort(function (a, b) { return a - b; });

    if (direction === 'vertical') {
      // "Principal" = hacia abajo (una fila por nivel); "cruzado" = a lo ancho (las ramas
      // del mismo nivel se reparten una al lado de la otra, centradas).
      const GAP_MAIN = 70, GAP_CROSS = 40, CENTER = 620;
      let cursorMain = 20;
      levelKeys.forEach(function (level) {
        const nodesInLevel = levels[level].slice().sort(function (a, b) { return a.x - b.x; });
        let maxCross = 60;
        nodesInLevel.forEach(function (n) { maxCross = Math.max(maxCross, measuredNodeHeight(n.id)); });
        const totalWidth = nodesInLevel.reduce(function (sum, n) { return sum + n.w; }, 0)
          + GAP_CROSS * Math.max(0, nodesInLevel.length - 1);
        let cursorCross = CENTER - totalWidth / 2;
        nodesInLevel.forEach(function (n) {
          n.x = Math.max(20, Math.round(cursorCross));
          n.y = Math.round(cursorMain);
          cursorCross += n.w + GAP_CROSS;
        });
        cursorMain += maxCross + GAP_MAIN;
      });
    } else {
      // Horizontal: lo mismo pero con los ejes cambiados — "principal" hacia la derecha
      // (una columna por nivel), "cruzado" hacia abajo dentro de cada columna.
      const GAP_MAIN = 90, GAP_CROSS = 30, CENTER = 420;
      let cursorMain = 20;
      levelKeys.forEach(function (level) {
        const nodesInLevel = levels[level].slice().sort(function (a, b) { return a.y - b.y; });
        let maxCross = 170;
        nodesInLevel.forEach(function (n) { maxCross = Math.max(maxCross, n.w); });
        const heights = nodesInLevel.map(function (n) { return measuredNodeHeight(n.id); });
        const totalHeight = heights.reduce(function (sum, h) { return sum + h; }, 0)
          + GAP_CROSS * Math.max(0, nodesInLevel.length - 1);
        let cursorCross = CENTER - totalHeight / 2;
        nodesInLevel.forEach(function (n, i) {
          n.x = Math.round(cursorMain);
          n.y = Math.max(20, Math.round(cursorCross));
          cursorCross += heights[i] + GAP_CROSS;
        });
        cursorMain += maxCross + GAP_MAIN;
      });
    }

    renderNodes();
    redrawConnections();
    sizeCanvas();
    markDirty();
    utils.showToast(direction === 'vertical' ? 'Organizado en vertical' : 'Organizado en horizontal', 'info');
  }

  function addNode(type) {
    idCounter++;
    const id = 'n' + idCounter;
    const defaults = newNodeDefaults(type);
    // scrollLeft/scrollTop están en píxeles visuales (ya con el zoom aplicado); se dividen
    // por zoomScale para que el nodo nuevo aparezca cerca de lo que se está viendo, en
    // coordenadas lógicas del lienzo.
    const sx = canvasViewport.scrollLeft / zoomScale, sy = canvasViewport.scrollTop / zoomScale;
    const node = Object.assign({ id: id, type: type, x: sx + 60 + (idCounter % 5) * 18, y: sy + 60 + (idCounter % 5) * 18, w: type === 'question' ? 300 : 240 }, defaults);
    graph.nodes.push(node);
    renderNodes(); redrawConnections(); markDirty();
    applySelectionUI(id); // el nodo ya se acaba de dibujar de cero arriba; alcanza con marcarlo seleccionado
  }

  // ---------------------------------------------------------------- vista previa de chat
  function renderPreview() {
    inspBody.innerHTML =
      '<div class="fphone">' +
        '<div class="fphone-head"><span class="fphone-avatar">F</span><div><div class="fphone-name">farmhouse</div><div class="fphone-status"><span class="dot"></span>en línea</div></div>' +
        '<button type="button" class="fphone-restart" id="btnFlowRestart">' +
          '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"><path d="M13.5 8a5.5 5.5 0 1 1-1.6-3.9"/><path d="M13.5 2.5v3.5H10"/></svg>Reiniciar</button></div>' +
        '<div class="fphone-body" id="flowPhoneBody"></div>' +
      '</div>';
    document.getElementById('btnFlowRestart').addEventListener('click', startPreview);
    startPreview();
  }

  let previewSeq = 0;
  function startPreview() {
    previewSeq++;
    const mySeq = previewSeq;
    const body = document.getElementById('flowPhoneBody');
    if (!body) return;
    body.innerHTML = '';
    const trigger = graph.nodes.find(function (n) { return n.type === 'trigger'; });
    if (!trigger) { addBubble(body, 'sys', 'No hay ningún disparador en el flujo.'); return; }
    const next = connsFrom(trigger.id, 0)[0];
    if (!next) { addBubble(body, 'sys', 'El disparador todavía no está conectado a nada.'); return; }
    walk(nodeById(next.to), body, mySeq);
  }

  function addBubble(container, kind, text) {
    const b = document.createElement('div'); b.className = 'fbubble ' + kind; b.textContent = text;
    container.appendChild(b); container.scrollTop = container.scrollHeight;
    return b;
  }
  function wait(ms) { return new Promise(function (res) { setTimeout(res, ms); }); }

  function walk(node, body, mySeq) {
    if (!node || mySeq !== previewSeq) return;

    if (node.type === 'delay') {
      const typing = document.createElement('div'); typing.className = 'ftyping'; typing.innerHTML = '<span></span><span></span><span></span>';
      body.appendChild(typing); body.scrollTop = body.scrollHeight;
      const reduced = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
      wait(reduced ? 120 : Math.min(900, node.seconds * 250)).then(function () {
        if (mySeq !== previewSeq) return;
        typing.remove();
        advance(node, body, mySeq);
      });
      return;
    }
    if (node.type === 'message') { addBubble(body, 'in', node.text || ''); wait(80).then(function () { advance(node, body, mySeq); }); return; }
    if (node.type === 'action') { addBubble(body, 'sys', '⚙ ' + node.kind + (node.note ? ' — ' + node.note : '')); wait(80).then(function () { advance(node, body, mySeq); }); return; }
    if (node.type === 'end') { addBubble(body, 'end', '— ' + node.name + ' —'); return; }

    if (node.type === 'capture') {
      addBubble(body, 'in', node.text || '');
      const row = document.createElement('div'); row.className = 'fphone-capture-row';
      row.innerHTML = '<input type="text" placeholder="Escribe tu respuesta…"><button type="button">Enviar</button>';
      const input = row.querySelector('input'), btn = row.querySelector('button');
      function submit() {
        const val = input.value.trim();
        if (!val || mySeq !== previewSeq) return;
        row.remove();
        addBubble(body, 'out', val);
        const edge = connsFrom(node.id, 0)[0];
        if (!edge) { addBubble(body, 'sys', 'Este paso todavía no continúa a ningún nodo.'); return; }
        wait(120).then(function () { walk(nodeById(edge.to), body, mySeq); });
      }
      btn.addEventListener('click', submit);
      input.addEventListener('keydown', function (e) { if (e.key === 'Enter') submit(); });
      body.appendChild(row); body.scrollTop = body.scrollHeight;
      setTimeout(function () { input.focus(); }, 50);
      return;
    }

    if (node.type === 'question') {
      addBubble(body, 'in', node.text || '');
      const optWrap = document.createElement('div'); optWrap.className = 'fphone-options';
      node.options.forEach(function (opt, i) {
        const btn = document.createElement('button'); btn.type = 'button'; btn.className = 'fphone-opt-btn'; btn.textContent = opt;
        btn.addEventListener('click', function () {
          if (mySeq !== previewSeq) return;
          optWrap.remove();
          addBubble(body, 'out', opt);
          const edge = connsFrom(node.id, i)[0];
          if (!edge) { addBubble(body, 'sys', 'Este botón todavía no está conectado a nada.'); return; }
          wait(120).then(function () { walk(nodeById(edge.to), body, mySeq); });
        });
        optWrap.appendChild(btn);
      });
      body.appendChild(optWrap); body.scrollTop = body.scrollHeight;
      return;
    }
  }

  function advance(node, body, mySeq) {
    const edge = connsFrom(node.id, 0)[0];
    if (!edge) { addBubble(body, 'sys', 'El flujo no continúa desde aquí todavía.'); return; }
    walk(nodeById(edge.to), body, mySeq);
  }

  window.addEventListener('resize', function () { if (mounted) redrawConnections(); });

  return { open: open, hasUnsavedChanges: hasUnsavedChanges };
})();
