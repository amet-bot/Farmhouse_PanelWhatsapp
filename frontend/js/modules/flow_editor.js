/**
 * Farmhouse WhatsApp Center - Editor Visual de Flujo del Bot
 * Pestaña "Flujo visual" (solo admin): edita contenido real del bot y representa su recorrido
 * mediante nodos arrastrables. El grafo se guarda en el backend (tabla bot_flows).
 *
 * IMPORTANTE — alcance deliberado de esta versión: el bot real de WhatsApp
 * (routers/webhooks.py::_process_auto_flow_background) es mucho más complejo que lo que este
 * editor puede modelar hoy — sucursal dinámica entre 5 sucursales, intenciones universales que
 * interrumpen desde cualquier punto (reiniciar/cancelar/cambiar sucursal/atrás), y un
 * sub-flujo anidado de 4 preguntas para pedidos corporativos. Este editor es un diagrama de
 * referencia + un simulador de "Probar flujo" que corre enteramente en el navegador. Los
 * textos de los nodos conocidos sí los consume el bot real; las conexiones solo se ejecutan
 * realmente en el tramo corporativo indicado abajo. El resto de rutas es una guía visual.
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
    question: 'Aquí el camino se parte: cada opción que toque el cliente sigue por una línea distinta.',
    capture: 'El bot pregunta y espera que el cliente escriba lo que sea (sin botones).',
    delay: 'Una pausa antes de seguir, para que no se sienta como una respuesta robótica.',
    action: 'El sistema hace algo por su cuenta (pausar el bot, avisar al equipo, etc.).',
    end: 'Aquí termina ese camino de la conversación.',
  };
  const TYPE_ORDER = ['trigger', 'message', 'question', 'capture', 'delay', 'action', 'end'];

  // Vocabulario visual de diagrama de flujo de toda la vida (el que se enseña en programación):
  // cada tipo de paso tiene su propia silueta, así se entiende de un vistazo qué hace cada
  // tarjeta sin tener que leerla entera. La forma se dibuja en una capa de fondo aparte
  // (.fnode-shape) y NO recortando .fnode, porque un clip-path sobre el nodo entero recortaría
  // también los puertos de conexión, que a propósito sobresalen de sus bordes.
  const SHAPE = {
    trigger: 'terminal', end: 'terminal', message: 'process',
    question: 'decision', capture: 'input', delay: 'delay', action: 'subprocess',
  };
  // Alto (en px) de las puntas superior e inferior del rombo. Tiene que coincidir con el
  // clip-path de .fshape-decision en flow-editor.css: aquí se usa para colocar los puertos de
  // cada rama justo encima de la "V" de abajo del rombo, no flotando fuera de la figura.
  const DECISION_CAP = 34;
  const SHAPE_MINI = {
    terminal: '<svg viewBox="0 0 30 16"><rect x="1.2" y="1.2" width="27.6" height="13.6" rx="6.8"/></svg>',
    process: '<svg viewBox="0 0 30 16"><rect x="1.2" y="1.2" width="27.6" height="13.6" rx="2"/></svg>',
    decision: '<svg viewBox="0 0 30 16"><path d="M15 1.2 28.8 8 15 14.8 1.2 8Z"/></svg>',
    input: '<svg viewBox="0 0 30 16"><path d="M5.2 1.2H28.8L24.8 14.8H1.2Z"/></svg>',
    delay: '<svg viewBox="0 0 30 16"><path d="M1.2 1.2H21a6.8 6.8 0 0 1 0 13.6H1.2Z"/></svg>',
    subprocess: '<svg viewBox="0 0 30 16"><rect x="1.2" y="1.2" width="27.6" height="13.6" rx="2"/><path d="M5.6 1.2v13.6M24.4 1.2v13.6"/></svg>',
  };
  // Cómo se llama esa figura en un diagrama de flujo clásico — va en la leyenda, para que quien
  // ya vio un diagrama de estos reconozca de inmediato qué está mirando.
  const SHAPE_NAME = {
    terminal: 'óvalo', process: 'rectángulo', decision: 'rombo',
    input: 'romboide', delay: 'figura de espera', subprocess: 'proceso del sistema',
  };
  function shapeMini(type) {
    return '<span class="flow-shape-mini" style="--type-color:var(--flow-' + type + ')">' + SHAPE_MINI[SHAPE[type]] + '</span>';
  }
  // Estos "kind" son solo ilustrativos en esta versión: no existe todavía un motor real
  // conectado que los interprete (a diferencia del panel de catering).
  const ACTION_KINDS = ['Pausar automatización y notificar al equipo', 'Agregar nota interna'];
  const PALETTE_TYPES = ['message', 'question', 'capture', 'delay', 'action', 'end'];

  // Contrato visible del editor: evita que el administrador confunda una tarjeta decorativa
  // con una instrucción que el backend realmente ejecuta. Los IDs de contenido coinciden con
  // los consultados por flow_content.py; la ruta real corresponde al motor corporativo acotado.
  const REAL_ROUTE_NODE_IDS = new Set([
    'corporate_event_type_question', 'corporate_headcount_question',
    'corporate_date_question', 'corporate_location_question',
    'corporate_location_after_combined', 'corporate_closing',
  ]);
  // Tramo apagado: el bot ya no hace las 4 preguntas del pedido corporativo — al elegir "Evento
  // o empresa" entrega directo el número del equipo de catering (ver CORPORATE_INTAKE_ENABLED en
  // services/auto_responses.py). Estas tarjetas se dejan a la vista porque el interruptor puede
  // volver a encenderse, pero mientras tanto hay que decirlo: si no, alguien edita un texto aquí
  // y se queda esperando verlo en WhatsApp.
  const INACTIVE_NODE_IDS = new Set([
    'corporate_intro', 'corporate_event_type_question', 'corporate_headcount_question',
    'corporate_date_question', 'corporate_location_question',
    'corporate_location_after_combined', 'corporate_closing', 'corporate_pause_action',
    'corporate_invalid_option_retry', 'corporate_headcount_retry', 'corporate_date_retry',
  ]);
  const REAL_CONTENT_NODE_IDS = new Set([
    'entry_gate', 'main_welcome', 'order_type_question',
    'branch_selection_menu_direct_body', 'branch_selection_visit_body',
    'branch_selection_delivery_body', 'branch_selection_pickup_body',
    'branch_visit_opening', 'branch_pickup_opening', 'branch_delivery_opening',
    'manager_help_question', 'manager_assigned_message', 'manager_declined_message',
    'menu_link_delivery_body', 'menu_link_pickup_body', 'menu_link_generic_body',
    'payment_ach', 'payment_card', 'payment_yappy', 'human_handoff_message',
    'corporate_intro', 'corporate_event_type_question', 'corporate_headcount_question',
    'corporate_date_question', 'corporate_location_question',
    'corporate_location_after_combined', 'corporate_closing',
    'corporate_invalid_option_retry', 'corporate_headcount_retry', 'corporate_date_retry',
    'restart_message', 'cancel_message', 'change_order_type_message', 'change_branch_message',
    'unknown_main_message', 'unknown_order_message', 'unknown_branch_message',
    'after_menu_help_question', 'visit_recovery_message', 'attachment_received_message',
    'corporate_catering_handoff', 'bot_followup_message', 'yappy_payment_success_message',
  ]);

  function nodeImpact(node) {
    if (INACTIVE_NODE_IDS.has(node.id)) {
      return {
        kind: 'inactive', label: 'Fuera de uso',
        help: 'El bot hoy no pasa por aquí: al elegir "Evento o empresa" entrega directo el número del equipo de catering. Este tramo queda guardado por si se vuelve a activar.',
      };
    }
    if (REAL_ROUTE_NODE_IDS.has(node.id)) {
      return {
        kind: 'route', label: 'Ruta real',
        help: 'El texto y las conexiones de este tramo corporativo sí afectan al bot real.',
      };
    }
    if (REAL_CONTENT_NODE_IDS.has(node.id)) {
      return {
        kind: 'content', label: 'Contenido real',
        help: 'El texto de este nodo sí puede aparecer en WhatsApp. Sus conexiones son solo una guía visual.',
      };
    }
    return {
      kind: 'simulation', label: 'Solo simulación',
      help: 'Este nodo ayuda a diseñar y probar el recorrido, pero el bot real no lo ejecuta automáticamente.',
    };
  }

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

  // Recordar si el usuario prefiere trabajar con la paleta y/o el panel lateral ocultos (más
  // espacio para el lienzo) — por navegador, no por flujo, así no hay que repetir la
  // preferencia cada vez que se abre "Flujo visual" o se cambia de diagrama.
  const PANEL_STORAGE_KEYS = { palette: 'farmhouse_flow_palette_collapsed', inspector: 'farmhouse_flow_inspector_collapsed' };
  function isPanelCollapsed(key) { return localStorage.getItem(PANEL_STORAGE_KEYS[key]) === '1'; }
  function setPanelCollapsed(key, collapsed) {
    try { localStorage.setItem(PANEL_STORAGE_KEYS[key], collapsed ? '1' : '0'); } catch (e) { /* modo privado: no pasa nada, solo no se recuerda */ }
  }

  // Zoom: el lienzo entero (nodos + SVG) se escala con un solo transform CSS, así que todo
  // — el tamaño de las tarjetas, el grosor de los puertos, las curvas — se agranda o
  // achica junto, sin tener que recalcular cada valor a mano. Lo único que hay que corregir
  // en el código es la geometría que se MIDE o se ARRASTRA en píxeles de pantalla
  // (portPoint, el arrastre de nodos, el arrastre de una conexión): esos sí necesitan
  // dividir por zoomScale para seguir hablando en las coordenadas "reales" del diagrama.
  // ZOOM_STEP es para los botones +/- (un clic, un salto perceptible está bien).
  // ZOOM_WHEEL_STEP es más chico porque la rueda dispara muchos eventos seguidos en un
  // solo gesto de scroll — con el mismo paso que los botones, cada "tick" se sentía brusco.
  // ZOOM_STEP es para los botones +/- (un clic, un salto perceptible). La rueda no usa un paso
  // fijo sino uno proporcional a cuánto se giró (ZOOM_WHEEL_SENSITIVITY): así un giro suave
  // mueve poquito y uno fuerte mueve más, en vez de dar saltos iguales y bruscos.
  const ZOOM_MIN = 0.25, ZOOM_MAX = 2, ZOOM_STEP = 0.15, ZOOM_WHEEL_SENSITIVITY = 0.001;
  let zoomScale = 1;

  let root, toolbar, workspace, canvasViewport, canvas, svg, connGroup, inspBody, saveBtn, dirtyFlag, statNodesEl, statConnsEl, zoomLabelEl;
  let inspectorEl, inspectorToggleBtn;

  // Lienzo de conexiones: incluye las puntas de flecha. Sin flecha, una línea entre dos
  // tarjetas no dice hacia dónde va la conversación — es el detalle que más ayuda a leer un
  // diagrama de flujo de corrido. Se usa tanto al montar el editor como al abrir cada flujo.
  const CONN_SVG_HTML =
    '<svg class="flow-conn-svg" id="flowConnSvg">' +
      '<defs>' +
        '<marker id="fconnArrow" viewBox="0 0 10 10" refX="9.2" refY="5" markerWidth="6.5" markerHeight="6.5" orient="auto-start-reverse">' +
          '<path class="fconn-arrow-head" d="M0.5 1.2 9.5 5 0.5 8.8Z"/></marker>' +
        '<marker id="fconnArrowSel" viewBox="0 0 10 10" refX="9.2" refY="5" markerWidth="6.5" markerHeight="6.5" orient="auto-start-reverse">' +
          '<path class="fconn-arrow-head selected" d="M0.5 1.2 9.5 5 0.5 8.8Z"/></marker>' +
      '</defs>' +
      '<g id="flowConnGroup"></g>' +
    '</svg>';

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
        '<span class="flow-panel-toggle-group" role="group" aria-label="Mostrar u ocultar paneles">' +
          '<button type="button" class="btn-flow-panel-toggle" id="btnTogglePalette" title="Mostrar/ocultar la paleta de nodos">' +
            '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><rect x="1.5" y="2.5" width="13" height="11" rx="2"/><path d="M6 2.5v11"/></svg>' +
            'Paleta' +
          '</button>' +
          '<button type="button" class="btn-flow-panel-toggle" id="btnToggleInspector" title="Mostrar/ocultar el panel de edición y prueba">' +
            '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><rect x="1.5" y="2.5" width="13" height="11" rx="2"/><path d="M10 2.5v11"/></svg>' +
            'Panel' +
          '</button>' +
        '</span>' +
        '<span class="spacer"></span>' +
        '<button type="button" class="btn-flow-discard" id="btnFlowDiscard">' +
          '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"><path d="M13.5 8a5.5 5.5 0 1 1-1.6-3.9"/><path d="M13.5 2.5v3.5H10"/></svg>' +
          'Descartar cambios' +
        '</button>' +
          '<button type="button" class="btn-flow-save" id="btnFlowSave" title="Guarda el diagrama y aplica de inmediato los contenidos reales">' +
          '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M2.5 2.5h9l2 2v9h-11z"/><path d="M5 2.5V6h5V2.5M4.5 9.5h7v4h-7z"/></svg>' +
          'Guardar y aplicar' +
        '</button>' +
      '</div>' +
      '<div class="flow-workspace">' +
        '<aside class="flow-palette">' +
          '<div class="flow-palette-title">Agregar nodo</div><div id="flowPaletteList"></div>' +
          '<p class="flow-palette-hint"><strong>Importante:</strong> los nodos nuevos sirven para diseñar y simular. No se ejecutan en WhatsApp hasta que el sistema los conecte expresamente.</p>' +
          '<div class="flow-legend"><div class="flow-palette-title">Cómo leer esto</div>' +
            '<p class="flow-legend-intro">Es un diagrama de flujo como los de programación: se lee de arriba hacia abajo siguiendo las flechas, y cada figura significa un tipo de paso distinto. En los rombos el camino se parte: cada línea que sale lleva escrito qué tiene que elegir el cliente para tomarla.</p>' +
            '<div id="flowLegendList"></div>' +
          '</div>' +
        '</aside>' +
        // El control de zoom vive FUERA del contenedor que hace scroll (flowCanvasOuter, que
        // no se mueve) y no adentro de él (flowViewport, el que sí se mueve) — si viviera
        // adentro, se iría de la pantalla apenas alguien recorriera un diagrama grande.
        '<div class="flow-canvas-outer" id="flowCanvasOuter">' +
          '<div class="flow-canvas-viewport" id="flowViewport"><div class="flow-canvas" id="flowCanvas">' + CONN_SVG_HTML + '</div></div>' +
          '<div class="flow-zoom-controls" role="group" aria-label="Zoom del diagrama">' +
            '<button type="button" id="btnZoomOut" title="Alejar" aria-label="Alejar">' +
              '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"><circle cx="6.8" cy="6.8" r="5"/><path d="M13.8 13.8l-2.9-2.9M4.4 6.8h4.8"/></svg>' +
            '</button>' +
            '<button type="button" id="btnZoomReset" class="flow-zoom-pct" title="Volver al 100% y centrar el diagrama. También puedes acercar y alejar con la rueda del mouse, y arrastrar el fondo para moverte.">100%</button>' +
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

    const paletteEl = root.querySelector('.flow-palette');
    inspectorEl = root.querySelector('.flow-inspector');
    const paletteToggleBtn = document.getElementById('btnTogglePalette');
    inspectorToggleBtn = document.getElementById('btnToggleInspector');
    function applyPanelState(key, el, btn) {
      const collapsed = isPanelCollapsed(key);
      el.classList.toggle('collapsed', collapsed);
      btn.classList.toggle('is-hidden', collapsed);
    }
    function togglePanel(key, el, btn) {
      const collapsed = !el.classList.contains('collapsed');
      el.classList.toggle('collapsed', collapsed);
      btn.classList.toggle('is-hidden', collapsed);
      setPanelCollapsed(key, collapsed);
      // El lienzo cambia de ancho al abrir/cerrar un panel: se recalcula el centrado cuando
      // termina la animación de 150ms, no antes (si no, se centra contra el ancho viejo).
      setTimeout(applyCanvasTransform, 180);
    }
    applyPanelState('palette', paletteEl, paletteToggleBtn);
    applyPanelState('inspector', inspectorEl, inspectorToggleBtn);
    paletteToggleBtn.addEventListener('click', function () { togglePanel('palette', paletteEl, paletteToggleBtn); });
    inspectorToggleBtn.addEventListener('click', function () { togglePanel('inspector', inspectorEl, inspectorToggleBtn); });

    document.getElementById('btnZoomOut').addEventListener('click', function () { setZoom(zoomScale - ZOOM_STEP); });
    document.getElementById('btnZoomIn').addEventListener('click', function () { setZoom(zoomScale + ZOOM_STEP); });
    // El botón del porcentaje vuelve al 100% Y recentra el diagrama: es el "sacame de donde me
    // perdí" de un solo clic, que es para lo que uno lo busca cuando se fue lejos con el zoom.
    document.getElementById('btnZoomReset').addEventListener('click', function () {
      setZoom(1);
      centerViewOnContent();
    });
    // La rueda del mouse acerca y aleja directamente (sin tener que sostener Ctrl), tomando como
    // centro el punto que está debajo del cursor. Para recorrer el diagrama se arrastra el fondo
    // del lienzo, que es el gesto que ya estaba y no depende de la rueda.
    canvasViewport.addEventListener('wheel', function (e) {
      e.preventDefault();
      const factor = Math.exp(-e.deltaY * ZOOM_WHEEL_SENSITIVITY);
      setZoom(zoomScale * factor, { x: e.clientX, y: e.clientY });
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

    // Al cambiar el tamaño de la ventana cambia el ancho disponible del lienzo, y con él dónde
    // hay que dejar el diagrama para que siga centrado.
    window.addEventListener('resize', function () { if (flowKey) applyCanvasTransform(); });

    mounted = true;
  }

  // ---------------------------------------------------------------- abrir / cargar
  async function open(key) {
    ensureShell();
    flowKey = key;
    selectedNodeId = null; selectedConnId = null; activeTab = 'test';
    setZoom(1); // cada vez que se abre un flujo arranca al 100%, no en el zoom que quedó de la vez anterior
    inspBody.innerHTML = '<div class="flow-empty-insp">Cargando…</div>';
    canvas.innerHTML = CONN_SVG_HTML;
    svg = document.getElementById('flowConnSvg');
    connGroup = document.getElementById('flowConnGroup');

    try {
      const data = await api.get(`/bot-flows/${key}`);
      graph = data.graph && Array.isArray(data.graph.nodes) ? data.graph : { nodes: [], conns: [] };
      originalGraphJson = JSON.stringify(graph);
      const subtitle = document.getElementById('flowEditorSubtitle');
      if (subtitle) subtitle.textContent = data.name + (data.updated_by ? ` · última edición: ${data.updated_by}` : '');
      renderNodes();
      requestAnimationFrame(function () { redrawConnections(); centerViewOnContent(); });
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
      utils.showToast('Flujo guardado y contenido real aplicado', 'success');
    } catch (err) {
      utils.showToast((err && err.message) || 'No se pudo guardar el flujo', 'error');
      saveBtn.disabled = false;
    }
  }

  // ---------------------------------------------------------------- render nodos
  function renderNodes() {
    canvas.querySelectorAll('.fnode, .flow-canvas-note').forEach(function (el) { el.remove(); });
    graph.nodes.forEach(function (node) { canvas.appendChild(buildNodeEl(node)); });
    renderLooseNote();
    sizeCanvas();
    if (statNodesEl) statNodesEl.textContent = graph.nodes.length;
    if (statConnsEl) statConnsEl.textContent = graph.conns.length;
  }

  // Rótulo del "anexo": las tarjetas que no cuelgan de ningún paso anterior (cancelar, reiniciar,
  // "no te entendí"…) quedan agrupadas debajo del árbol principal, y sin una línea que las
  // explique parecen parte del flujo que alguien se olvidó de conectar. Es solo un cartel del
  // editor: no se guarda en el diagrama ni cuenta como nodo.
  function renderLooseNote() {
    const islands = splitIslands(childrenMap());
    if (islands.length < 2) return;
    const loose = [];
    islands.slice(1).forEach(function (isl) {
      isl.members.forEach(function (id) { const n = nodeById(id); if (n) loose.push(n); });
    });
    if (!loose.length) return;
    const mainBottom = Math.max.apply(null, islands[0].members.map(function (id) {
      const n = nodeById(id); return n ? n.y : 0;
    }));
    const top = Math.min.apply(null, loose.map(function (n) { return n.y; }));
    // Si el usuario ya movió las tarjetas a mano y quedaron mezcladas con el árbol, no se
    // inventa una separación que en pantalla no existe.
    if (!(top > mainBottom)) return;
    const note = document.createElement('div');
    note.className = 'flow-canvas-note';
    note.style.left = Math.min.apply(null, loose.map(function (n) { return n.x; })) + 'px';
    note.style.top = (top - 40) + 'px';
    note.textContent = 'Respuestas sueltas — el bot las usa cuando hacen falta (cancelar, reiniciar, “no te entendí”…); no vienen de un paso anterior.';
    canvas.appendChild(note);
  }

  function buildNodeEl(node) {
    const impact = nodeImpact(node);
    const shape = SHAPE[node.type] || 'process';
    const el = document.createElement('div');
    el.className = 'fnode fshape-' + shape + (node.id === selectedNodeId ? ' selected' : '');
    el.dataset.id = node.id;
    el.style.left = node.x + 'px';
    el.style.top = node.y + 'px';
    el.style.width = node.w + 'px';
    el.style.setProperty('--type-color', 'var(--flow-' + node.type + ')');

    // Dos capas superpuestas para la figura: la de abajo pinta el contorno y la de arriba, 1.5px
    // hacia adentro, el relleno. Hace falta así porque un borde CSS normal desaparece en cuanto
    // la figura se recorta con clip-path (rombo, romboide) — quedaría una silueta sin línea.
    const shapeEl = document.createElement('span');
    shapeEl.className = 'fnode-shape';
    shapeEl.innerHTML = '<span class="fnode-shape-fill"></span>';
    el.appendChild(shapeEl);

    const inner = document.createElement('div');
    inner.className = 'fnode-inner';
    el.appendChild(inner);

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
    inner.appendChild(head);

    const body = document.createElement('div');
    body.className = 'fnode-body';

    const impactBadge = document.createElement('div');
    impactBadge.className = 'fnode-impact ' + impact.kind;
    impactBadge.title = impact.help;
    impactBadge.innerHTML = '<span class="fnode-impact-dot"></span><span>' + impact.label + '</span>';
    body.appendChild(impactBadge);

    if (node.type === 'question') {
      if (node.text) { const p = document.createElement('p'); p.className = 'muted-line'; p.textContent = node.text; body.appendChild(p); }
      // Las opciones se listan numeradas adentro del rombo; el número es el que lleva cada
      // puerto de salida en la "V" de abajo, para saber sin adivinar qué rama sale de dónde.
      node.options.forEach(function (opt, i) {
        const row = document.createElement('div'); row.className = 'fopt-row';
        const num = document.createElement('span'); num.className = 'fopt-num'; num.textContent = String(i + 1); row.appendChild(num);
        const chip = document.createElement('span'); chip.className = 'fopt-chip'; chip.textContent = opt; row.appendChild(chip);
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
    inner.appendChild(body);

    if (node.type !== 'trigger') {
      const inPort = document.createElement('span'); inPort.className = 'fport fport-in'; inPort.dataset.node = node.id; el.appendChild(inPort);
    }
    if (node.type === 'question') {
      // Cada rama sale de un punto distinto de la "V" inferior del rombo (izquierda → derecha,
      // en el mismo orden en que se leen las opciones), como el abanico de salidas de una
      // decisión en un diagrama de flujo. Antes salían todas del costado derecho, apiladas una
      // debajo de otra: por eso las líneas se cruzaban entre sí apenas había más de dos opciones.
      const total = node.options.length || 1;
      node.options.forEach(function (opt, i) {
        const f = (i + 0.5) / total;
        const port = document.createElement('span');
        port.className = 'fport fport-out fport-branch';
        port.dataset.node = node.id; port.dataset.port = i;
        port.title = 'Si elige: ' + opt;
        port.textContent = String(i + 1);
        port.style.left = (f * 100) + '%';
        port.style.bottom = (DECISION_CAP * Math.abs(2 * f - 1)) + 'px';
        el.appendChild(port);
      });
    } else if (node.type !== 'end') {
      const outPort = document.createElement('span'); outPort.className = 'fport fport-out-single fport-out'; outPort.dataset.node = node.id; outPort.dataset.port = 0; el.appendChild(outPort);
    }

    // Toda la tarjeta se arrastra (antes solo la cabecera): ahora que las figuras no tienen
    // barra de título con fondo propio, esa franja ya no se distingue como "el agarre".
    el.addEventListener('pointerdown', function (e) {
      if (e.target.closest('.fport') || e.target.closest('.fnode-del')) return;
      startDragNode(e, node, el);
    });
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
  // Vale lo mismo que el margen izquierdo que deja "Organizar" (ver LAYOUT_MARGIN_CROSS): así el
  // diagrama queda con el mismo aire de los dos lados y no pegado contra el borde izquierdo.
  const CANVAS_FREE_MARGIN = 420;
  function sizeCanvas() {
    let maxX = 1400, maxY = 1400;
    graph.nodes.forEach(function (n) {
      maxX = Math.max(maxX, n.x + n.w + CANVAS_FREE_MARGIN);
      maxY = Math.max(maxY, n.y + CANVAS_FREE_MARGIN);
    });
    canvas.style.width = maxX + 'px'; canvas.style.height = maxY + 'px';
    svg.setAttribute('width', maxX); svg.setAttribute('height', maxY);
    svg.setAttribute('viewBox', '0 0 ' + maxX + ' ' + maxY);
    applyCanvasTransform();
  }

  // Ancho que ocupan realmente las tarjetas. Se calcula solo con los datos del grafo (sin medir
  // el DOM) porque se consulta en cada giro de la rueda del zoom.
  function contentBoundsX() {
    if (!graph.nodes.length) return null;
    let minX = Infinity, maxX = -Infinity;
    graph.nodes.forEach(function (n) { minX = Math.min(minX, n.x); maxX = Math.max(maxX, n.x + n.w); });
    return { minX: minX, maxX: maxX };
  }

  // Cuando el diagrama, al nivel de zoom actual, entra entero en la pantalla, no hay scroll que
  // ajustar: quedaría pegado contra el borde izquierdo (el scroll no puede ser negativo). En ese
  // caso se lo corre con el propio transform del lienzo hasta dejarlo centrado.
  function canvasPadX() {
    const b = contentBoundsX();
    if (!b) return 0;
    const free = canvasViewport.clientWidth - (b.maxX - b.minX) * zoomScale;
    return free > 0 ? Math.max(0, free / 2 - b.minX * zoomScale) : 0;
  }

  function applyCanvasTransform() {
    canvas.style.transform = 'translateX(' + Math.round(canvasPadX()) + 'px) scale(' + zoomScale + ')';
    canvas.style.transformOrigin = '0 0';
  }

  // Rectángulo que ocupan realmente las tarjetas (no el lienzo completo, que siempre tiene aire
  // de sobra alrededor). Es lo que se usa para dejar el diagrama centrado en pantalla.
  function contentBounds() {
    if (!graph.nodes.length) return null;
    let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
    graph.nodes.forEach(function (n) {
      minX = Math.min(minX, n.x); minY = Math.min(minY, n.y);
      maxX = Math.max(maxX, n.x + n.w); maxY = Math.max(maxY, n.y + measuredNodeHeight(n.id));
    });
    return { minX: minX, minY: minY, maxX: maxX, maxY: maxY };
  }

  // Deja el diagrama centrado a lo ancho y arrancando por arriba. A lo alto NO se centra a
  // propósito: el flujo se lee de arriba hacia abajo, así que lo que uno quiere ver al abrir es
  // el principio, no la mitad.
  function centerViewOnContent() {
    const b = contentBounds();
    if (!b) return;
    applyCanvasTransform();
    const centerX = ((b.minX + b.maxX) / 2) * zoomScale + canvasPadX();
    canvasViewport.scrollLeft = Math.max(0, centerX - canvasViewport.clientWidth / 2);
    canvasViewport.scrollTop = Math.max(0, b.minY * zoomScale - 32);
  }

  // ---------------------------------------------------------------- zoom
  // El lienzo (nodos + SVG) es un único elemento que se escala con CSS transform, así que
  // el ancho/alto lógico que fija sizeCanvas() no cambia — solo se ve más grande o más chico.
  // El lienzo se agranda desde su esquina superior izquierda (transform-origin 0 0), así que al
  // acercar, todo "se va" hacia abajo y a la derecha: parecía que el zoom empujaba el diagrama
  // en una sola dirección. Para que se sienta natural se corrige el scroll en el mismo paso, de
  // modo que el punto que estaba bajo el cursor (o el centro de la pantalla, si el zoom vino de
  // los botones) se quede exactamente donde estaba.
  function setZoom(newScale, anchor) {
    const previous = zoomScale;
    const next = Math.round(Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, newScale)) * 100) / 100;
    if (next === previous) return;

    const vpRect = canvasViewport.getBoundingClientRect();
    const anchorX = anchor ? anchor.x - vpRect.left : canvasViewport.clientWidth / 2;
    const anchorY = anchor ? anchor.y - vpRect.top : canvasViewport.clientHeight / 2;
    // Punto del diagrama (en coordenadas lógicas, sin zoom) que hay que dejar quieto.
    const padBefore = canvasPadX();
    const fixedX = (canvasViewport.scrollLeft + anchorX - padBefore) / previous;
    const fixedY = (canvasViewport.scrollTop + anchorY) / previous;

    zoomScale = next;
    applyCanvasTransform();
    if (zoomLabelEl) zoomLabelEl.textContent = Math.round(zoomScale * 100) + '%';

    const padAfter = canvasPadX();
    // Si hay margen dinámico, es porque el diagrama entero entra a lo ancho en la pantalla: ahí
    // no hay nada que recorrer y el lugar correcto es el centro, sin importar dónde quedó el
    // scroll antes de alejar.
    canvasViewport.scrollLeft = padAfter > 0 ? 0 : Math.max(0, fixedX * zoomScale - anchorX);
    canvasViewport.scrollTop = Math.max(0, fixedY * zoomScale - anchorY);
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

  // Las conexiones se dibujan como en un diagrama de flujo de programación: tramos rectos en
  // ángulo recto (con la esquina apenas redondeada) en vez de curvas libres. Dos curvas
  // cualesquiera que se cruzan se ven como un enredo; dos líneas rectas que se cruzan se leen
  // sin esfuerzo, porque el ojo puede seguir cada una. Además siempre bajan primero, cruzan a lo
  // ancho por un "pasillo" intermedio y vuelven a bajar: el recorrido queda predecible.
  const CONN_CORNER = 11;  // radio de la esquina redondeada
  const CONN_STUB = 26;    // tramo recto mínimo al salir de un nodo y al entrar al siguiente

  function roundedPath(pts) {
    let d = 'M ' + pts[0].x + ' ' + pts[0].y;
    for (let i = 1; i < pts.length - 1; i++) {
      const prev = pts[i - 1], cur = pts[i], next = pts[i + 1];
      const len1 = Math.hypot(cur.x - prev.x, cur.y - prev.y);
      const len2 = Math.hypot(next.x - cur.x, next.y - cur.y);
      if (len1 < 0.5 || len2 < 0.5) continue; // tramo de largo cero: no hay esquina que redondear
      const r1 = Math.min(CONN_CORNER, len1 / 2), r2 = Math.min(CONN_CORNER, len2 / 2);
      const ax = cur.x - ((cur.x - prev.x) / len1) * r1, ay = cur.y - ((cur.y - prev.y) / len1) * r1;
      const bx = cur.x + ((next.x - cur.x) / len2) * r2, by = cur.y + ((next.y - cur.y) / len2) * r2;
      d += ' L ' + ax + ' ' + ay + ' Q ' + cur.x + ' ' + cur.y + ' ' + bx + ' ' + by;
    }
    const last = pts[pts.length - 1];
    return d + ' L ' + last.x + ' ' + last.y;
  }

  // Devuelve el trazo más el punto donde va la etiqueta de la rama ("si elige X") y el punto
  // donde aparece la ✕ para borrar la conexión — separados a propósito, para que el botón de
  // borrar no tape la etiqueta.
  function routeConn(p1, p2) {
    const dx = p2.x - p1.x, dy = p2.y - p1.y;
    if (Math.abs(dx) < 2 && dy > 0) {
      return {
        d: 'M ' + p1.x + ' ' + p1.y + ' L ' + p2.x + ' ' + p2.y,
        label: { x: p1.x, y: p1.y + Math.max(16, dy * 0.32) },
        handle: { x: p2.x, y: p1.y + dy * 0.72 },
      };
    }
    if (dy > CONN_STUB * 2) {
      // Camino normal (el destino está más abajo): bajar, cruzar, bajar.
      const midY = p1.y + Math.max(CONN_STUB, dy / 2);
      return {
        d: roundedPath([p1, { x: p1.x, y: midY }, { x: p2.x, y: midY }, p2]),
        label: { x: (p1.x + p2.x) / 2, y: midY },
        handle: { x: p2.x, y: (midY + p2.y) / 2 },
      };
    }
    // El destino está al mismo nivel o más arriba (una vuelta atrás, un reinicio): se rodea por
    // un pasillo lateral en vez de atravesar el diagrama en diagonal por encima de las tarjetas.
    const laneX = dx >= 0 ? Math.max(p1.x, p2.x) + 72 : Math.min(p1.x, p2.x) - 72;
    const downY = p1.y + CONN_STUB, upY = p2.y - CONN_STUB;
    return {
      d: roundedPath([p1, { x: p1.x, y: downY }, { x: laneX, y: downY }, { x: laneX, y: upY }, { x: p2.x, y: upY }, p2]),
      label: { x: laneX, y: (downY + upY) / 2 },
      handle: { x: (laneX + p2.x) / 2, y: upY },
    };
  }

  // Reconstruye TODAS las conexiones desde cero. Se usa cuando la forma del grafo cambia de
  // verdad (cargar, agregar/borrar nodo, conectar/desconectar, seleccionar, redimensionar) —
  // nunca durante un arrastre, que usa updateConnectionsForNode() en su lugar (ver más abajo
  // por qué: reconstruir las conexiones en cada cuadro es lo que se sentía "trabado").
  // Etiqueta de la rama sobre la línea ("si elige: Delivery"). Es lo que convierte el diagrama
  // en algo que se lee solo: sin ella hay que adivinar cuál de las tres líneas que salen de una
  // decisión corresponde a cada opción. El ancho del recuadro se mide después de insertar el
  // texto, porque depende de la fuente real del navegador.
  const SVG_NS = 'http://www.w3.org/2000/svg';
  function appendBranchLabel(text, point) {
    const g = document.createElementNS(SVG_NS, 'g');
    g.setAttribute('class', 'fconn-label');
    g.setAttribute('transform', `translate(${point.x},${point.y})`);
    const label = text.length > 26 ? text.slice(0, 25) + '…' : text;
    const t = document.createElementNS(SVG_NS, 'text');
    t.setAttribute('x', '0'); t.setAttribute('y', '3.4');
    t.setAttribute('text-anchor', 'middle');
    t.textContent = label;
    g.appendChild(t);
    connGroup.appendChild(g);
    const w = Math.max(20, t.getComputedTextLength()) + 13;
    const rect = document.createElementNS(SVG_NS, 'rect');
    rect.setAttribute('x', String(-w / 2)); rect.setAttribute('y', '-8.5');
    rect.setAttribute('width', String(w)); rect.setAttribute('height', '17');
    rect.setAttribute('rx', '8.5');
    g.insertBefore(rect, t);
    return g;
  }

  function redrawConnections() {
    connGroup.innerHTML = '';
    const canvasRect = canvas.getBoundingClientRect();
    graph.conns.forEach(function (c, idx) {
      const p1 = portPoint(c.from, 'out', c.fromPort, canvasRect);
      const p2 = portPoint(c.to, 'in', 0, canvasRect);
      if (!p1 || !p2) return;
      const route = routeConn(p1, p2);
      const d = route.d;

      const visible = document.createElementNS(SVG_NS, 'path');
      visible.setAttribute('d', d);
      visible.setAttribute('data-conn-idx', idx);
      visible.setAttribute('class', 'fconn fconn-visible' + (selectedConnId === idx ? ' selected' : ''));
      visible.setAttribute('marker-end', selectedConnId === idx ? 'url(#fconnArrowSel)' : 'url(#fconnArrow)');
      visible.style.pointerEvents = 'none';
      connGroup.appendChild(visible);

      const hit = document.createElementNS(SVG_NS, 'path');
      hit.setAttribute('d', d);
      hit.setAttribute('data-conn-idx', idx);
      hit.classList.add('fconn-hit');
      hit.style.pointerEvents = 'stroke'; hit.style.strokeWidth = '10'; hit.style.opacity = '0'; hit.style.fill = 'none'; hit.style.stroke = '#000';
      hit.addEventListener('pointerdown', function (e) { e.stopPropagation(); selectedConnId = (selectedConnId === idx ? null : idx); redrawConnections(); });
      connGroup.appendChild(hit);

      const fromNode = nodeById(c.from);
      if (fromNode && fromNode.type === 'question' && fromNode.options && fromNode.options[c.fromPort]) {
        const labelEl = appendBranchLabel(fromNode.options[c.fromPort], route.label);
        labelEl.setAttribute('data-conn-idx', idx);
      }

      const g = document.createElementNS(SVG_NS, 'g');
      g.setAttribute('data-conn-idx', idx);
      g.setAttribute('class', 'fconn-del-group');
      g.setAttribute('transform', `translate(${route.handle.x},${route.handle.y})`);
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
      const route = routeConn(p1, p2);
      const visible = connGroup.querySelector(`.fconn-visible[data-conn-idx="${idx}"]`);
      const hit = connGroup.querySelector(`.fconn-hit[data-conn-idx="${idx}"]`);
      const group = connGroup.querySelector(`.fconn-del-group[data-conn-idx="${idx}"]`);
      const label = connGroup.querySelector(`.fconn-label[data-conn-idx="${idx}"]`);
      if (visible) visible.setAttribute('d', route.d);
      if (hit) hit.setAttribute('d', route.d);
      if (group) group.setAttribute('transform', `translate(${route.handle.x},${route.handle.y})`);
      if (label) label.setAttribute('transform', `translate(${route.label.x},${route.label.y})`);
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
    function move(ev) { ghost.setAttribute('d', routeConn(start, { x: (ev.clientX - c.left) / zoomScale, y: (ev.clientY - c.top) / zoomScale }).d); }
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
    // Elegir un nodo para editarlo mientras el panel está oculto lo revela (una sola vez,
    // no cambia la preferencia guardada) — de lo contrario el clic no mostraría nada.
    if (tab === 'edit' && inspectorEl && inspectorEl.classList.contains('collapsed')) {
      inspectorEl.classList.remove('collapsed');
      if (inspectorToggleBtn) inspectorToggleBtn.classList.remove('is-hidden');
    }
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

    const impact = nodeImpact(node);
    const impactInfo = document.createElement('div');
    impactInfo.className = 'flow-impact-info ' + impact.kind;
    impactInfo.innerHTML = '<div><span class="fnode-impact-dot"></span><strong>' + impact.label + '</strong></div>' +
      '<p>' + impact.help + '</p>';
    inspBody.appendChild(impactInfo);

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
      btn.innerHTML = `${shapeMini(type)}<span class="flow-palette-label">${TYPE_LABEL[type]}</span>`;
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
      row.innerHTML = shapeMini(type) +
        `<span><span class="flow-legend-label">${TYPE_LABEL[type]} <em>(${SHAPE_NAME[SHAPE[type]]})</em></span>` +
        `<span class="flow-legend-desc">${TYPE_HELP[type]}</span></span>`;
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
    // Cualquier nodo sin conexión de entrada es una "raíz" (profundidad 0) desde el arranque,
    // no solo el disparador — si no, una rama huérfana (ej. una ruta alterna que no cuelga del
    // disparador) nunca propagaba profundidad a SUS propios nodos posteriores: como el
    // huérfano solo se fijaba en 0 hasta DESPUÉS del ciclo de propagación de abajo, todo lo que
    // colgaba de él se quedaba sin profundidad asignada y cabecera terminaba también en 0,
    // apilado encima del disparador en vez de seguir su propia cadena hacia abajo.
    const hasIncoming = {};
    graph.conns.forEach(function (c) { hasIncoming[c.to] = true; });
    graph.nodes.forEach(function (n) { depth[n.id] = (n.type === 'trigger' || !hasIncoming[n.id]) ? 0 : null; });
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
    // Por si acaso queda algún nodo sin tocar (no debería, con la inicialización de arriba).
    graph.nodes.forEach(function (n) { if (depth[n.id] == null) depth[n.id] = 0; });
    return depth;
  }

  function measuredNodeHeight(id) {
    const el = canvas.querySelector(`.fnode[data-id="${id}"]`);
    if (!el) return 100;
    return el.getBoundingClientRect().height / zoomScale;
  }

  // Hijos directos de cada nodo, en el orden de las opciones (rama 1, rama 2, rama 3…) para que
  // el árbol quede dibujado en el mismo orden en que el cliente ve los botones en WhatsApp.
  function childrenMap() {
    const kids = {};
    graph.conns.forEach(function (c) { (kids[c.from] = kids[c.from] || []).push(c); });
    Object.keys(kids).forEach(function (k) {
      kids[k].sort(function (a, b) { return (a.fromPort || 0) - (b.fromPort || 0); });
    });
    return kids;
  }

  // Divide el diagrama en "islas": el árbol que cuelga del disparador, y cada grupo suelto que
  // no cuelga de nadie (los mensajes de recuperación: cancelar, reiniciar, no entendí…). Antes
  // todas esas tarjetas sueltas caían en la MISMA primera fila que el disparador, una al lado de
  // la otra: una fila de 17 tarjetas y 6.000px de ancho que no se parecía en nada a un árbol y
  // obligaba a recorrer el lienzo a lo largo para ver el flujo de verdad.
  function splitIslands(kids) {
    const claimed = {};
    const islands = [];
    const trigger = graph.nodes.find(function (n) { return n.type === 'trigger'; });
    const hasIncoming = {};
    graph.conns.forEach(function (c) { hasIncoming[c.to] = true; });

    const rootOrder = [];
    if (trigger) rootOrder.push(trigger.id);
    graph.nodes.forEach(function (n) { if (!hasIncoming[n.id] && !rootOrder.includes(n.id)) rootOrder.push(n.id); });
    graph.nodes.forEach(function (n) { if (!rootOrder.includes(n.id)) rootOrder.push(n.id); }); // por si hubiera un ciclo cerrado

    rootOrder.forEach(function (rootId) {
      if (claimed[rootId]) return;
      const members = [rootId];
      const treeKids = {};
      claimed[rootId] = true;
      // Recorrido en anchura: cada nodo lo "reclama" el primer camino que llega a él, y esa es
      // la conexión que manda para dibujar el árbol. Las demás conexiones que entren al nodo
      // (atajos, vueltas atrás) se siguen dibujando, pero no mueven a nadie de lugar.
      const queue = [rootId];
      while (queue.length) {
        const u = queue.shift();
        treeKids[u] = treeKids[u] || [];
        (kids[u] || []).forEach(function (c) {
          if (claimed[c.to]) return;
          claimed[c.to] = true;
          treeKids[u].push(c.to);
          members.push(c.to);
          queue.push(c.to);
        });
      }
      islands.push({ root: rootId, members: members, treeKids: treeKids });
    });
    return islands;
  }

  function autoLayout(direction) {
    const vertical = direction === 'vertical';
    const kids = childrenMap();
    const islands = splitIslands(kids);
    const depth = computeDepths();
    const GAP_CROSS = 44, GAP_MAIN = 78, ISLAND_GAP = 90;
    // Margen inicial. En el eje "cruzado" (a lo ancho, cuando se organiza en vertical) vale lo
    // mismo que el aire que sizeCanvas() deja del otro lado, así el diagrama queda con el mismo
    // espacio a izquierda y derecha en vez de pegado contra el borde izquierdo del lienzo.
    const MARGIN_CROSS = vertical ? CANVAS_FREE_MARGIN : 60;
    const MARGIN_MAIN = vertical ? 40 : 60;

    function crossSize(n) { return vertical ? n.w : measuredNodeHeight(n.id); }
    function mainSize(n) { return vertical ? measuredNodeHeight(n.id) : n.w; }

    const laid = islands.map(function (isl) {
      const nodes = isl.members.map(nodeById).filter(Boolean);
      const cross = {};
      let cursor = 0;
      // Árbol "prolijo" (el mismo criterio de cualquier diagrama de flujo dibujado a mano):
      // cada hoja ocupa el siguiente lugar libre a lo ancho, y cada padre queda centrado
      // exactamente sobre sus hijos. Por eso un padre con tres ramas queda justo en el medio de
      // las tres, con las líneas abriéndose en abanico hacia abajo en vez de cruzarse.
      function place(id, guard) {
        const n = nodeById(id);
        if (!n || guard > 300) return cursor;
        const ch = (isl.treeKids[id] || []).filter(function (cid) { return nodeById(cid); });
        if (!ch.length) {
          const c = cursor + crossSize(n) / 2;
          cursor += crossSize(n) + GAP_CROSS;
          cross[id] = c;
          return c;
        }
        const cs = ch.map(function (cid) { return place(cid, guard + 1); });
        const c = (cs[0] + cs[cs.length - 1]) / 2;
        cross[id] = c;
        return c;
      }
      place(isl.root, 0);
      nodes.forEach(function (n) {
        if (cross[n.id] == null) { cross[n.id] = cursor + crossSize(n) / 2; cursor += crossSize(n) + GAP_CROSS; }
      });

      // Eje principal: una fila (o columna) por nivel de profundidad, con el alto real de las
      // tarjetas de ese nivel, para que nunca se encime una fila con la siguiente.
      const byLevel = {};
      nodes.forEach(function (n) { (byLevel[depth[n.id]] = byLevel[depth[n.id]] || []).push(n); });
      const levels = Object.keys(byLevel).map(Number).sort(function (a, b) { return a - b; });
      const mainAt = {};
      let mainCursor = 0;
      levels.forEach(function (lv) {
        mainAt[lv] = mainCursor;
        let widest = vertical ? 70 : 170;
        byLevel[lv].forEach(function (n) { widest = Math.max(widest, mainSize(n)); });
        mainCursor += widest + GAP_MAIN;
      });

      // Repaso final: si dentro de un nivel quedaron dos tarjetas encimadas (pasa cuando un
      // padre es más ancho que todo su ramaje), se corre la de la derecha lo justo y necesario.
      levels.forEach(function (lv) {
        const list = byLevel[lv].slice().sort(function (a, b) { return cross[a.id] - cross[b.id]; });
        let minStart = -Infinity;
        list.forEach(function (n) {
          let start = cross[n.id] - crossSize(n) / 2;
          if (start < minStart) start = minStart;
          cross[n.id] = start + crossSize(n) / 2;
          minStart = start + crossSize(n) + GAP_CROSS;
        });
      });

      let firstStart = Infinity, lastEnd = -Infinity;
      nodes.forEach(function (n) {
        firstStart = Math.min(firstStart, cross[n.id] - crossSize(n) / 2);
        lastEnd = Math.max(lastEnd, cross[n.id] + crossSize(n) / 2);
      });
      if (!nodes.length) { firstStart = 0; lastEnd = 0; }
      return {
        nodes: nodes, cross: cross, mainAt: mainAt, offset: firstStart,
        crossExtent: Math.max(0, lastEnd - firstStart),
        mainExtent: Math.max(0, mainCursor - GAP_MAIN),
      };
    });

    // El árbol que cuelga del disparador va arriba de todo; las tarjetas sueltas (mensajes de
    // cancelar, reiniciar, "no te entendí"…) se acomodan debajo en filas compactas, como un
    // anexo — no estiradas a lo ancho en la misma primera fila que el inicio del flujo.
    const mainIsland = laid[0];
    const maxRowCross = MARGIN_CROSS + Math.max(mainIsland ? mainIsland.crossExtent : 0, 1500);
    let originMain = MARGIN_MAIN + (mainIsland ? mainIsland.mainExtent + ISLAND_GAP * 1.6 : 0);
    let originCross = MARGIN_CROSS;
    let rowMain = 0;

    laid.forEach(function (isl, i) {
      let oCross = MARGIN_CROSS, oMain = MARGIN_MAIN;
      if (i > 0) {
        if (originCross > MARGIN_CROSS && originCross + isl.crossExtent > maxRowCross) {
          originCross = MARGIN_CROSS; originMain += rowMain + ISLAND_GAP; rowMain = 0;
        }
        oCross = originCross; oMain = originMain;
        originCross += isl.crossExtent + ISLAND_GAP;
        rowMain = Math.max(rowMain, isl.mainExtent);
      }
      isl.nodes.forEach(function (n) {
        const c = Math.round(oCross + isl.cross[n.id] - isl.offset - crossSize(n) / 2);
        const m = Math.round(oMain + (isl.mainAt[depth[n.id]] || 0));
        if (vertical) { n.x = Math.max(0, c); n.y = Math.max(0, m); }
        else { n.y = Math.max(0, c); n.x = Math.max(0, m); }
      });
    });

    renderNodes();
    redrawConnections();
    sizeCanvas();
    // El centrado espera un cuadro: el lienzo acaba de cambiar de tamaño y, si se toca el scroll
    // en el mismo instante, el navegador todavía lo limita al ancho viejo y se queda corto.
    requestAnimationFrame(centerViewOnContent);
    markDirty();
    utils.showToast(direction === 'vertical' ? 'Organizado en vertical' : 'Organizado en horizontal', 'info');
  }

  function addNode(type) {
    // El contador arrancaba siempre en 1000: en cada sesión nueva el primer nodo agregado volvía
    // a ser "n1001" aunque el flujo guardado ya tuviera uno con ese id (dos nodos con el mismo
    // id: borrar uno borraba los dos y las conexiones quedaban ambiguas). Se salta cualquier id
    // que ya exista en el grafo cargado.
    const existingIds = new Set((graph.nodes || []).map((n) => n.id));
    do { idCounter++; } while (existingIds.has('n' + idCounter));
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
      '<div class="flow-preview-note"><strong>Vista del diagrama</strong><span>Sirve para revisar textos y caminos dibujados. No reproduce todas las reglas, validaciones ni decisiones del bot real.</span></div>' +
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
