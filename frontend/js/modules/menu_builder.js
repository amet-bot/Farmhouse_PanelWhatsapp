/* ==============================================================================
   FARMHOUSE - "Arma tu bowl" (y todo producto con opciones) del Menú Digital (/menu)

   window.FHBowl     dibuja el bowl visto desde arriba en un <svg>, y lo va llenando a
                     medida que el cliente elige: base al fondo, toppings y premiums en
                     porciones alrededor, el dressing en hilo y el crunch espolvoreado.
   window.FHBuilder  la hoja para elegir: tamaño, cada grupo (base, toppings...), premiums
                     o add-ons y notas. Devuelve la línea del carrito con onAdd(item).

   Las opciones (qué se puede elegir y cuántas) vienen de /api/menu/items (product.builder,
   ver backend/services/menu_builders.py); el servidor vuelve a validar todo al pedir.
   ============================================================================== */
(function () {
  "use strict";

  const SVG_NS = "http://www.w3.org/2000/svg";
  const C = 150;          // centro del bowl (viewBox 300x300)
  const R_IN = 118;       // radio del interior del bowl
  const reduceMotion = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  const escapeHtml = (str) => String(str ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
  const money = (n) => `$${Number(n || 0).toFixed(2)}`;

  // ---------- utilidades de dibujo ----------
  function hashStr(s) {
    let h = 2166136261;
    for (let i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 16777619); }
    return h >>> 0;
  }
  function rng(seed) { // mulberry32: el mismo ingrediente se dibuja siempre igual
    let a = seed >>> 0;
    return () => {
      a = (a + 0x6D2B79F5) >>> 0;
      let t = a;
      t = Math.imul(t ^ (t >>> 15), t | 1);
      t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }
  function shade(hex, amt) { // amt -1..1 (negativo oscurece)
    const n = parseInt(String(hex || "#999999").slice(1), 16);
    let r = (n >> 16) & 255, g = (n >> 8) & 255, b = n & 255;
    const t = amt < 0 ? 0 : 255, p = Math.abs(amt);
    r = Math.round((t - r) * p + r); g = Math.round((t - g) * p + g); b = Math.round((t - b) * p + b);
    return `#${((1 << 24) + (r << 16) + (g << 8) + b).toString(16).slice(1)}`;
  }
  function node(tag, attrs, parent) {
    const n = document.createElementNS(SVG_NS, tag);
    for (const k in attrs) n.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(n);
    return n;
  }

  // Forma de cada ingrediente (las porciones se dibujan con piezas de esa forma).
  const KIND = {
    tomates_cherry: "round", cranberries: "berry", kalamata: "round", frijolitos: "bean", blueberries: "berry",
    zapallo: "cube", roasted_corn: "corn", pimenton: "strip", zucchini: "slice", mango: "cube", pina: "cube",
    zanahorias_ralladas: "shred", cebolla_encurtida: "shred", charred_onions: "shred", zanahorias_encurtidas: "shred",
    cilantro: "leaf", zero_waste_greens: "leaf", brocoli: "floret",
    pepino: "slice", limon: "wedge", jalapenos: "ring", banana: "slice", fresa: "berryhalf",
    tomates_pepino: "mix",
    PRM_POLLO_SPICED: "meat", PRM_POLLO_ROSTIZADO: "meat", PRM_SALMON_BULGOGI: "meat", PRM_STEAK: "meat",
    PRM_TOFU_BULGOGI: "cube", PRM_FETA: "cube", PRM_PARMESAN_CRUMBLE: "crumb", PRM_QUESO_CABRA: "crumb",
    PRM_AVO_SMASH: "scoop", PRM_HUMMUS: "scoop", PRM_WHIPPED_FETA: "scoop", PRM_HUEVO_DURO: "egg",
  };
  const PREMIUM_COLORS = {
    PRM_POLLO_SPICED: "#b8642e", PRM_POLLO_ROSTIZADO: "#d8a064", PRM_SALMON_BULGOGI: "#ee8a68",
    PRM_TOFU_BULGOGI: "#e9cf98", PRM_STEAK: "#6e3324", PRM_PARMESAN_CRUMBLE: "#f0dc98",
    PRM_QUESO_CABRA: "#fbf7ee", PRM_FETA: "#fcf9f1", PRM_AVO_SMASH: "#a3c45a", PRM_HUEVO_DURO: "#fbf8f0",
    PRM_HUMMUS: "#dfbd86", PRM_WHIPPED_FETA: "#fffaf2",
    ACAI_SPREAD_PEANUT: "#b07a3f", ACAI_SPREAD_ALMOND: "#c99a64", ACAI_SPREAD_NUTELLA: "#4a2a1c",
  };
  const SPREAD_SKUS = new Set(["ACAI_SPREAD_PEANUT", "ACAI_SPREAD_ALMOND", "ACAI_SPREAD_NUTELLA"]);

  // Una pieza de un ingrediente en (x,y) con giro rot.
  function piece(g, kind, color, x, y, rot, rnd) {
    const dark = shade(color, -0.28), light = shade(color, 0.35);
    const t = `translate(${x.toFixed(1)} ${y.toFixed(1)}) rotate(${rot.toFixed(0)})`;
    switch (kind) {
      case "round": {
        const r = 5.5 + rnd() * 1.6;
        node("circle", { cx: x, cy: y, r, fill: color, stroke: dark, "stroke-width": 0.8 }, g);
        node("circle", { cx: x - r * 0.35, cy: y - r * 0.35, r: r * 0.28, fill: "#fff", opacity: 0.45 }, g);
        break;
      }
      case "berry": node("circle", { cx: x, cy: y, r: 3.6 + rnd(), fill: color, stroke: dark, "stroke-width": 0.6 }, g); break;
      case "bean": node("ellipse", { cx: 0, cy: 0, rx: 4.2, ry: 2.7, fill: color, stroke: shade(color, 0.25), "stroke-width": 0.6, transform: t }, g); break;
      case "cube": {
        const s = 7 + rnd() * 2.5;
        node("rect", { x: -s / 2, y: -s / 2, width: s, height: s, rx: 1.6, fill: color, stroke: dark, "stroke-width": 0.7, transform: t }, g);
        break;
      }
      case "corn": node("rect", { x: -2.2, y: -2.2, width: 4.4, height: 4.4, rx: 1.4, fill: color, stroke: dark, "stroke-width": 0.5, transform: t }, g); break;
      case "strip": node("rect", { x: -7, y: -2.6, width: 14, height: 5.2, rx: 2.4, fill: color, stroke: dark, "stroke-width": 0.6, transform: t }, g); break;
      case "shred": node("path", { d: "M-7 -1 Q0 3 7 -1", fill: "none", stroke: color, "stroke-width": 2.4, "stroke-linecap": "round", transform: t }, g); break;
      case "leaf": {
        node("ellipse", { cx: 0, cy: 0, rx: 6.5, ry: 3.6, fill: color, transform: t }, g);
        node("path", { d: "M-5 0 L5 0", stroke: dark, "stroke-width": 0.6, transform: t }, g);
        break;
      }
      case "floret": {
        node("rect", { x: -1.2, y: 0, width: 2.4, height: 6, fill: shade(color, 0.3), transform: t }, g);
        [[-3, -1], [3, -1], [0, -4], [0, 1]].forEach(([dx, dy]) => node("circle", { cx: dx, cy: dy, r: 3.2, fill: color, stroke: dark, "stroke-width": 0.4, transform: t }, g));
        break;
      }
      case "slice": {
        const r = 6.5 + rnd() * 1.2;
        node("circle", { cx: x, cy: y, r, fill: light, stroke: color, "stroke-width": 2 }, g);
        node("circle", { cx: x, cy: y, r: r * 0.35, fill: shade(color, 0.55) }, g);
        break;
      }
      case "ring": {
        node("circle", { cx: x, cy: y, r: 5, fill: "none", stroke: color, "stroke-width": 2.2 }, g);
        node("circle", { cx: x + 1, cy: y, r: 1, fill: "#f3efc8" }, g);
        break;
      }
      case "wedge": node("path", { d: "M0 0 L9 -4 A10 10 0 0 1 9 4 Z", fill: color, stroke: dark, "stroke-width": 0.8, transform: t }, g); break;
      case "berryhalf": {
        node("path", { d: "M-5 -4 Q0 -7 5 -4 Q4 4 0 7 Q-4 4 -5 -4 Z", fill: color, stroke: dark, "stroke-width": 0.6, transform: t }, g);
        node("path", { d: "M-2 -2 Q0 2 2 -2", fill: "none", stroke: light, "stroke-width": 1, transform: t }, g);
        break;
      }
      case "meat": {
        node("rect", { x: -9, y: -3.6, width: 18, height: 7.2, rx: 3, fill: color, stroke: dark, "stroke-width": 0.8, transform: t }, g);
        node("path", { d: "M-5 -3 L-3 3 M0 -3 L2 3 M5 -3 L7 3", stroke: dark, "stroke-width": 1, opacity: 0.6, transform: t }, g);
        break;
      }
      case "crumb": node("circle", { cx: x, cy: y, r: 2 + rnd() * 2, fill: color, stroke: shade(color, -0.15), "stroke-width": 0.5 }, g); break;
      default: node("ellipse", { cx: 0, cy: 0, rx: 5, ry: 3.5, fill: color, stroke: dark, "stroke-width": 0.6, transform: t }, g);
    }
  }

  // Una porción completa (un ingrediente) centrada en 0,0. Se mueve con transform.
  function portion(key, color, seedKey) {
    const g = document.createElementNS(SVG_NS, "g");
    const rnd = rng(hashStr(seedKey || key));
    const kind = KIND[key] || "blob";
    if (kind === "scoop") {
      node("circle", { cx: 0, cy: 0, r: 19, fill: color, stroke: shade(color, -0.18), "stroke-width": 1 }, g);
      node("path", { d: "M-9 0 A9 9 0 1 1 0 9 A5 5 0 1 1 4 -1", fill: "none", stroke: shade(color, -0.2), "stroke-width": 1.6, "stroke-linecap": "round" }, g);
      if (key === "PRM_HUMMUS") [[-6, -8], [7, 5], [-3, 9]].forEach(([dx, dy]) => node("circle", { cx: dx, cy: dy, r: 1.3, fill: "#a64b2a" }, g));
      return g;
    }
    if (kind === "egg") {
      [[-9, -4, 30], [9, 5, -20]].forEach(([dx, dy, rot]) => {
        node("ellipse", { cx: dx, cy: dy, rx: 11, ry: 8.5, fill: "#fdfbf5", stroke: "#e6dfcf", "stroke-width": 0.8, transform: `rotate(${rot} ${dx} ${dy})` }, g);
        node("circle", { cx: dx, cy: dy, r: 4.6, fill: "#f2b832" }, g);
      });
      return g;
    }
    // Montoncito: sombra + base del color del ingrediente, para que se lea encima de la base.
    node("ellipse", { cx: 1.5, cy: 3, rx: 29, ry: 27, fill: "#000", opacity: 0.2 }, g);
    const mound = kind === "mix" ? "#e9b49a" : shade(color, 0.45);
    node("circle", { cx: 0, cy: 0, r: 27, fill: mound, stroke: shade(color, -0.1), "stroke-width": 1, "stroke-opacity": 0.35 }, g);
    const n = kind === "corn" || kind === "crumb" || kind === "berry" ? 22 : (kind === "meat" || kind === "slice" ? 8 : 13);
    for (let i = 0; i < n; i++) {
      const a = rnd() * Math.PI * 2, d = Math.sqrt(rnd()) * 19;
      let k = kind, col = color;
      if (kind === "mix") { k = i % 2 ? "round" : "slice"; col = i % 2 ? "#e0483a" : "#a3d47f"; }
      piece(g, k, col, Math.cos(a) * d, Math.sin(a) * d, rnd() * 180, rnd);
    }
    return g;
  }

  // ---------- el bowl ----------
  // spec: { visual: "bowl"|"acai", bases: [{key,color}], portions: [{id,key,color}],
  //         drizzles: [{id,color}], sprinkles: [{id,key,color}] }
  function createBowl(svg) {
    svg.setAttribute("viewBox", "0 0 300 300");
    svg.innerHTML = "";
    const defs = node("defs", {}, svg);
    const grad = node("radialGradient", { id: `${svg.id}-in`, cx: "50%", cy: "45%", r: "60%" }, defs);
    node("stop", { offset: "70%", "stop-color": "#000", "stop-opacity": 0 }, grad);
    node("stop", { offset: "100%", "stop-color": "#000", "stop-opacity": 0.22 }, grad);
    const clip = node("clipPath", { id: `${svg.id}-clip` }, defs);
    node("circle", { cx: C, cy: C, r: R_IN }, clip);

    node("ellipse", { cx: C, cy: C + 10, rx: 140, ry: 136, fill: "#000", opacity: 0.18, class: "bowl-shadow" }, svg);
    node("circle", { cx: C, cy: C, r: 140, fill: "#f7f2e8", stroke: "#e3d9c4", "stroke-width": 2 }, svg);
    node("circle", { cx: C, cy: C, r: R_IN + 3, fill: "none", stroke: "#e9e0cd", "stroke-width": 3 }, svg);
    const inner = node("g", { "clip-path": `url(#${svg.id}-clip)` }, svg);
    const empty = node("circle", { cx: C, cy: C, r: R_IN, fill: "#eee6d5" }, inner);
    const layers = {
      base: node("g", {}, inner),
      portions: node("g", {}, inner),
      drizzle: node("g", {}, inner),
      sprinkle: node("g", {}, inner),
    };
    node("circle", { cx: C, cy: C, r: R_IN, fill: `url(#${svg.id}-in)`, "pointer-events": "none" }, inner);
    const hint = node("text", { x: C, y: C + 5, "text-anchor": "middle", class: "bowl-empty-text" }, svg);
    return { svg, defs, empty, layers, hint, nodes: new Map(), baseKey: "" };
  }

  function popIn(g, delay) {
    if (reduceMotion) return;
    g.classList.add("bw-pop");
    if (delay) g.style.animationDelay = `${delay}ms`;
  }
  function popOut(g) {
    if (reduceMotion) { g.remove(); return; }
    g.classList.add("bw-out");
    setTimeout(() => g.remove(), 260);
  }

  function basePattern(b, defs, id, visual) {
    const pat = node("pattern", { id, width: 34, height: 34, patternUnits: "userSpaceOnUse" }, defs);
    node("rect", { width: 34, height: 34, fill: b.color }, pat);
    const rnd = rng(hashStr(b.key));
    const k = b.key;
    for (let i = 0; i < 16; i++) {
      const x = rnd() * 34, y = rnd() * 34, rot = rnd() * 180;
      if (visual === "acai") {
        node("path", { d: `M${x} ${y} q4 -3 8 0`, stroke: shade(b.color, 0.18), "stroke-width": 1.4, fill: "none", opacity: 0.7 }, pat);
      } else if (k === "quinoa") {
        node("circle", { cx: x, cy: y, r: 1.4, fill: i % 6 === 0 ? "#b06a5a" : shade(b.color, -0.12) }, pat);
      } else if (k === "cilantro_pesto_rice") {
        node("ellipse", { cx: x, cy: y, rx: 2.4, ry: 1.2, fill: i % 3 ? "#f4f1e3" : "#7aa04a", transform: `rotate(${rot} ${x} ${y})` }, pat);
      } else if (k === "kale_repollo") {
        node("ellipse", { cx: x, cy: y, rx: 6, ry: 3, fill: i % 5 === 0 ? "#86608f" : shade(b.color, i % 2 ? -0.1 : 0.08), transform: `rotate(${rot} ${x} ${y})` }, pat);
      } else {
        node("ellipse", { cx: x, cy: y, rx: 6.5, ry: 3.6, fill: shade(b.color, i % 2 ? -0.1 : 0.1), transform: `rotate(${rot} ${x} ${y})` }, pat);
      }
    }
    return `url(#${id})`;
  }

  function renderBase(bowl, spec) {
    const bases = spec.visual === "acai" ? [{ key: "acai", color: "#4b1f4f" }] : (spec.bases || []);
    const sig = bases.map((b) => b.key).join("|");
    if (sig === bowl.baseKey) return;
    bowl.baseKey = sig;
    const old = bowl.layers.base.firstChild;
    if (old) popOut(old);
    bowl.defs.querySelectorAll("pattern").forEach((p) => p.remove());
    bowl.hint.textContent = bases.length ? "" : (spec.emptyText || "Elige tu base");
    if (!bases.length) return;
    const g = node("g", {}, bowl.layers.base);
    bases.forEach((b, i) => {
      const fill = basePattern(b, bowl.defs, `${bowl.svg.id}-p-${b.key}`, spec.visual);
      if (bases.length === 1) node("circle", { cx: C, cy: C, r: R_IN, fill }, g);
      else {
        const d = i === 0
          ? `M${C} ${C - R_IN} A${R_IN} ${R_IN} 0 0 0 ${C} ${C + R_IN} Z`
          : `M${C} ${C - R_IN} A${R_IN} ${R_IN} 0 0 1 ${C} ${C + R_IN} Z`;
        node("path", { d, fill }, g);
      }
    });
    popIn(g);
  }

  // Dónde va cada porción: alrededor del borde y, si hay proteína caliente, una al centro.
  function layoutPortions(list) {
    const pos = new Map();
    const center = list.find((p) => p.center);
    const ring = list.filter((p) => p !== center);
    if (center) pos.set(center.id, [C, C]);
    const n = ring.length;
    const radius = n <= 1 && !center ? 0 : (n <= 3 ? 60 : (n <= 6 ? 74 : 80));
    ring.forEach((p, i) => {
      const a = -Math.PI / 2 + (i * 2 * Math.PI) / Math.max(n, 1) + (center ? Math.PI / Math.max(n, 1) : 0);
      pos.set(p.id, [C + Math.cos(a) * radius, C + Math.sin(a) * radius]);
    });
    return pos;
  }

  function render(bowl, spec) {
    renderBase(bowl, spec);

    const wanted = new Map();
    (spec.portions || []).forEach((p) => wanted.set("p:" + p.id, p));
    (spec.drizzles || []).forEach((d, i) => wanted.set("d:" + d.id, { ...d, idx: i }));
    (spec.sprinkles || []).forEach((s) => wanted.set("s:" + s.id, s));

    bowl.nodes.forEach((g, id) => { if (!wanted.has(id)) { popOut(g); bowl.nodes.delete(id); } });

    const pos = layoutPortions(spec.portions || []);
    (spec.portions || []).forEach((p) => {
      const id = "p:" + p.id;
      const [x, y] = pos.get(p.id);
      let outer = bowl.nodes.get(id);
      if (!outer) {
        outer = node("g", { class: "bw-move" }, bowl.layers.portions);
        outer.style.transform = `translate(${x}px, ${y}px)`;
        const inner = portion(p.key, p.color, p.id);
        outer.appendChild(inner);
        popIn(inner);
        bowl.nodes.set(id, outer);
      } else {
        outer.style.transform = `translate(${x}px, ${y}px)`;
      }
    });

    (spec.drizzles || []).forEach((d, i) => {
      const id = "d:" + d.id;
      if (bowl.nodes.has(id)) return;
      const g = node("g", {}, bowl.layers.drizzle);
      const off = i * 22;
      const path = `M${48 + off} ${92 + off} C ${110} ${40 + off}, ${120} ${150 + off}, ${170} ${100 + off} S ${230} ${150 + off}, ${255 - off} ${120 + off}
                    M${60 + off} ${180 - off} C ${110} ${140 - off}, ${150} ${230 - off}, ${200} ${190 - off} S ${240} ${200 - off}, ${250} ${170 - off}`;
      node("path", { d: path, fill: "none", stroke: "#000", "stroke-opacity": 0.18, "stroke-width": 7.5, "stroke-linecap": "round", class: "bw-draw" }, g);
      node("path", { d: path, fill: "none", stroke: d.color, "stroke-width": 5, "stroke-linecap": "round", class: "bw-draw" }, g);
      bowl.nodes.set(id, g);
    });

    (spec.sprinkles || []).forEach((s) => {
      const id = "s:" + s.id;
      if (bowl.nodes.has(id)) return;
      const g = node("g", {}, bowl.layers.sprinkle);
      const rnd = rng(hashStr(s.id + "spr"));
      const kind = { garbanzos_crispy: "round", totopos: "chip", almendras: "sliver", pecans: "nut", cebollitas: "shred", chilli_crunch: "flake" }[s.key] || "dot";
      for (let i = 0; i < 30; i++) {
        const a = rnd() * Math.PI * 2, d = Math.sqrt(rnd()) * (R_IN - 14);
        const x = C + Math.cos(a) * d, y = C + Math.sin(a) * d, rot = rnd() * 180;
        const one = node("g", {}, g);
        if (kind === "round") piece(one, "round", s.color, x, y, rot, () => 0.1);
        else if (kind === "chip") node("path", { d: `M${x} ${y - 5} L${x + 5} ${y + 4} L${x - 5} ${y + 4} Z`, fill: s.color, stroke: shade(s.color, -0.25), "stroke-width": 0.6, transform: `rotate(${rot} ${x} ${y})` }, one);
        else if (kind === "sliver") node("ellipse", { cx: x, cy: y, rx: 4.5, ry: 1.7, fill: s.color, stroke: shade(s.color, -0.3), "stroke-width": 0.4, transform: `rotate(${rot} ${x} ${y})` }, one);
        else if (kind === "nut") node("ellipse", { cx: x, cy: y, rx: 4.2, ry: 2.6, fill: s.color, stroke: shade(s.color, -0.3), "stroke-width": 0.6, transform: `rotate(${rot} ${x} ${y})` }, one);
        else if (kind === "shred") node("path", { d: `M${x - 4} ${y} q4 3 8 0`, fill: "none", stroke: s.color, "stroke-width": 2, "stroke-linecap": "round", transform: `rotate(${rot} ${x} ${y})` }, one);
        else if (kind === "flake") node("rect", { x: x - 2, y: y - 1.2, width: 4, height: 2.4, fill: i % 3 ? s.color : "#e8c27a", transform: `rotate(${rot} ${x} ${y})` }, one);
        else node("circle", { cx: x, cy: y, r: 1.4 + rnd() * 1.2, fill: i % 4 ? s.color : shade(s.color, -0.25) }, one);
        popIn(one, i * 14);
      }
      bowl.nodes.set(id, g);
    });
  }

  // Bowl de muestra (tarjeta del menú y portada).
  const SAMPLES = {
    bowl: [
      { bases: ["mezclum", "quinoa"] },
      { portions: ["tomates_cherry", "roasted_corn", "PRM_AVO_SMASH", "zanahorias_ralladas", "frijolitos", "cebolla_encurtida"], center: "PRM_POLLO_SPICED" },
      { dressing: "green_tahini" },
      { crunch: "almendras" },
    ],
    acai: [
      { portions: ["fresa", "banana", "blueberries"] },
      { crunch: "miso_tahini_granola" },
      { spread: "ACAI_SPREAD_PEANUT" },
    ],
  };

  function sampleSpec(visual, catalog, steps) {
    const spec = { visual, bases: [], portions: [], drizzles: [], sprinkles: [], emptyText: "" };
    const find = (key) => catalog[key] || { key, color: PREMIUM_COLORS[key] || "#999" };
    SAMPLES[visual].slice(0, steps).forEach((s) => {
      (s.bases || []).forEach((k) => spec.bases.push(find(k)));
      (s.portions || []).forEach((k) => spec.portions.push({ id: k, key: k, color: find(k).color }));
      if (s.center) spec.portions.push({ id: s.center, key: s.center, color: find(s.center).color, center: true });
      if (s.dressing) spec.drizzles.push({ id: s.dressing, color: find(s.dressing).color });
      if (s.crunch) spec.sprinkles.push({ id: s.crunch, key: s.crunch, color: find(s.crunch).color });
      if (s.spread) spec.drizzles.push({ id: s.spread, color: PREMIUM_COLORS[s.spread] });
    });
    return spec;
  }

  function optionCatalog(product) {
    const map = {};
    ((product.builder && product.builder.groups) || []).forEach((g) => g.options.forEach((o) => { map[o.key] = o; }));
    return map;
  }

  let sampleSeq = 0;
  // Dibuja un bowl de muestra en `host`. animate=true lo va armando paso a paso en bucle.
  function mountSample(host, product, animate) {
    const visual = product.builder && product.builder.visual;
    if (!visual || !SAMPLES[visual]) return;
    const svg = document.createElementNS(SVG_NS, "svg");
    svg.id = `fhbowl-sample-${++sampleSeq}`;
    svg.setAttribute("class", "bowl-svg");
    svg.setAttribute("aria-hidden", "true");
    host.innerHTML = "";
    host.appendChild(svg);
    const catalog = optionCatalog(product);
    const total = SAMPLES[visual].length;
    if (!animate || reduceMotion) {
      render(createBowl(svg), sampleSpec(visual, catalog, total));
      return;
    }
    let bowl = createBowl(svg), step = 0;
    const tick = () => {
      if (!svg.isConnected) return;
      step += 1;
      if (step > total + 2) { bowl = createBowl(svg); step = 0; }
      render(bowl, sampleSpec(visual, catalog, Math.min(step, total)));
      setTimeout(tick, step === 0 ? 500 : 1100);
    };
    tick();
  }

  window.FHBowl = { createBowl, render, mountSample, PREMIUM_COLORS, SPREAD_SKUS };

  // ======================================================================
  //  HOJA PARA ARMAR / ELEGIR
  // ======================================================================
  const el = (id) => document.getElementById(id);
  const MAX_ITEM_QTY = 20;
  const MAX_ADDON_QTY = 4;
  const B = {
    product: null, onAdd: null, sizeSku: null, picked: {}, addons: new Map(), qty: 1,
    bowl: null, sections: [], observer: null, eyebrow: "",
  };

  function sizeOf() { return B.product.sizes.find((s) => s.sku === B.sizeSku) || B.product.sizes[0]; }
  function allAddons() {
    const a = B.product.addons || {};
    return [...(a.warm || []), ...(a.cold || []), ...(a.flat || [])];
  }
  function groupDef(key) { return B.product.builder.groups.find((g) => g.key === key); }

  function unitPrice() {
    let p = sizeOf().price;
    B.product.builder.groups.forEach((g) => (B.picked[g.key] || []).forEach((k) => {
      const o = g.options.find((x) => x.key === k);
      if (o && o.price) p += o.price;
    }));
    allAddons().forEach((a) => { p += a.price * (B.addons.get(a.sku) || 0); });
    return p;
  }

  function missingGroups() {
    return B.product.builder.groups.filter((g) => (B.picked[g.key] || []).length < g.min);
  }

  // ---------- secciones ----------
  function sectionsFor(product) {
    const out = [];
    if (product.has_sizes && product.sizes.length > 1) out.push({ id: "size", title: "Tamaño", kind: "size" });
    product.builder.groups.forEach((g) => out.push({ id: "g-" + g.key, title: g.title, kind: "group", group: g }));
    const a = product.addons || {};
    if ((a.warm || []).length) out.push({ id: "warm", title: "Proteína", kind: "addons", list: a.warm, hint: "Opcional · cargo adicional" });
    if ((a.cold || []).length) out.push({ id: "cold", title: "Premiums", kind: "addons", list: a.cold, hint: "Opcional · cargo adicional" });
    if ((a.flat || []).length) out.push({ id: "flat", title: product.builder.visual === "acai" ? "Spreads y add-ons" : "Extras", kind: "addons", list: a.flat, hint: "Opcional · cargo adicional" });
    out.push({ id: "notes", title: "Notas", kind: "notes" });
    return out;
  }

  function optionHtml(sectionId, key, label, color, price, active, extra) {
    return `
      <button type="button" class="bopt ${active ? "is-on" : ""}" data-section="${sectionId}" data-key="${escapeHtml(key)}" aria-pressed="${active}">
        ${color ? `<span class="bopt-dot" style="--c:${escapeHtml(color)}"></span>` : ""}
        <span class="bopt-label">${escapeHtml(label)}</span>
        ${price ? `<span class="bopt-price">+${money(price)}</span>` : ""}
        ${extra || ""}
        <span class="bopt-check" aria-hidden="true"></span>
      </button>`;
  }

  function qtyHtml(q) {
    if (!q) return "";
    return `<span class="bopt-qty"><span class="bopt-qbtn" data-act="dec" role="button" aria-label="Quitar uno">−</span><b>${q}</b><span class="bopt-qbtn" data-act="inc" role="button" aria-label="Agregar otro">+</span></span>`;
  }
  const addonExtra = (sku) => qtyHtml(B.addons.get(sku) || 0);
  const countOf = (list, key) => list.filter((k) => k === key).length;
  // "3x Fresa, Banana": lo elegido de un grupo, juntando los repetidos.
  function groupedLabels(g, keys) {
    const seen = [];
    keys.forEach((k) => { if (!seen.includes(k)) seen.push(k); });
    return seen.map((k) => {
      const o = g.options.find((x) => x.key === k);
      const n = countOf(keys, k);
      return o ? { key: k, n, label: n > 1 ? `${n}x ${o.label}` : o.label, color: o.color } : null;
    }).filter(Boolean);
  }

  function sectionBody(sec) {
    if (sec.kind === "size") {
      return B.product.sizes.map((s) => optionHtml("size", s.sku, `${s.label === "Large" ? "Large" : s.label}`, null, null, s.sku === B.sizeSku,
        `<span class="bopt-price is-base">${money(s.price)}</span>`)).join("");
    }
    if (sec.kind === "group") {
      const picked = B.picked[sec.group.key] || [];
      return sec.group.options.map((o) => {
        const n = countOf(picked, o.key);
        return optionHtml(sec.id, o.key, o.label, o.color, o.price, n > 0, sec.group.repeatable ? qtyHtml(n) : "");
      }).join("");
    }
    if (sec.kind === "addons") {
      return sec.list.map((a) => optionHtml(sec.id, a.sku, a.title, PREMIUM_COLORS[a.sku] || null, a.price, (B.addons.get(a.sku) || 0) > 0, addonExtra(a.sku))).join("");
    }
    return `<textarea id="builderNotes" class="notes-input" maxlength="300" placeholder='Ej: "dressing aparte", "sin cilantro"...'></textarea>`;
  }

  function countText(sec) {
    if (sec.kind !== "group") return "";
    const n = (B.picked[sec.group.key] || []).length;
    return sec.group.max > 1 ? `${n}/${sec.group.max}` : "";
  }

  function sectionDone(sec) {
    if (sec.kind === "size") return true;
    if (sec.kind === "group") {
      const n = (B.picked[sec.group.key] || []).length;
      return n >= sec.group.min && (n > 0 || sec.group.min === 0 && B.visited.has(sec.id));
    }
    if (sec.kind === "addons") return sec.list.some((a) => B.addons.get(a.sku));
    return false;
  }

  function renderSections() {
    const wrap = el("builderGroups");
    let num = 0;
    wrap.innerHTML = B.sections.map((sec) => {
      const isStep = sec.kind !== "notes";
      if (isStep) num += 1;
      const hint = sec.kind === "group"
        ? sec.group.hint + (sec.group.repeatable ? " · puedes repetir" : "")
        : (sec.hint || (sec.kind === "notes" ? "Opcional" : ""));
      return `
        <section class="bgroup ${sec.kind === "notes" ? "is-notes" : ""}" id="bsec-${sec.id}" data-section="${sec.id}">
          <header class="bgroup-head">
            ${isStep ? `<span class="bgroup-num">${num}</span>` : ""}
            <h3>${escapeHtml(sec.title)}</h3>
            <span class="bgroup-hint">${escapeHtml(hint)}</span>
            <span class="bgroup-count" data-count="${sec.id}">${countText(sec)}</span>
          </header>
          <div class="bgroup-options ${sec.kind === "notes" ? "" : "bopts"}">${sectionBody(sec)}</div>
        </section>`;
    }).join("");
  }

  function refreshSection(secId) {
    const sec = B.sections.find((s) => s.id === secId);
    const box = document.querySelector(`#bsec-${secId} .bgroup-options`);
    if (!sec || !box) return;
    box.innerHTML = sectionBody(sec);
    const c = document.querySelector(`[data-count="${secId}"]`);
    if (c) c.textContent = countText(sec);
  }

  function renderSteps() {
    const nav = el("builderSteps");
    let num = 0;
    nav.innerHTML = B.sections.filter((s) => s.kind !== "notes").map((sec) => {
      num += 1;
      const done = sectionDone(sec);
      const missing = sec.kind === "group" && (B.picked[sec.group.key] || []).length < sec.group.min;
      return `<button type="button" class="bstep ${done ? "is-done" : ""} ${missing ? "is-req" : ""}" data-go="${sec.id}">
        <span class="bstep-num">${done ? "✓" : num}</span>${escapeHtml(sec.title)}</button>`;
    }).join("");
  }

  // ---------- estado -> bowl ----------
  function bowlSpec() {
    const visual = B.product.builder.visual;
    const spec = { visual, bases: [], portions: [], drizzles: [], sprinkles: [] };
    B.product.builder.groups.forEach((g) => {
      const seen = {};
      (B.picked[g.key] || []).forEach((k) => {
        const o = g.options.find((x) => x.key === k);
        if (!o) return;
        seen[k] = (seen[k] || 0) + 1;
        const id = "g" + k + "#" + seen[k];
        if (g.style === "base") spec.bases.push(o);
        else if (g.style === "drizzle") spec.drizzles.push({ id, color: o.color });
        else if (g.style === "sprinkle") spec.sprinkles.push({ id, key: k, color: o.color });
        else if (g.style === "wedge") spec.portions.push({ id, key: k, color: o.color });
      });
    });
    const warmSkus = new Set(((B.product.addons || {}).warm || []).map((a) => a.sku));
    let centerTaken = false;
    allAddons().forEach((a) => {
      const q = B.addons.get(a.sku) || 0;
      if (!q) return;
      if (SPREAD_SKUS.has(a.sku)) { spec.drizzles.push({ id: "a" + a.sku, color: PREMIUM_COLORS[a.sku] }); return; }
      if (!PREMIUM_COLORS[a.sku]) return; // polvos y proteínas en polvo: no se ven en el bowl
      const center = warmSkus.has(a.sku) && !centerTaken;
      if (center) centerTaken = true;
      spec.portions.push({ id: "a" + a.sku, key: a.sku, color: PREMIUM_COLORS[a.sku], center });
    });
    return spec;
  }

  function recapHtml() {
    const rows = [];
    B.product.builder.groups.forEach((g) => {
      const items = groupedLabels(g, B.picked[g.key] || []);
      if (items.length) rows.push({ title: g.title, items: items.map((o) => ({ label: o.label, color: o.color, sec: "g-" + g.key, key: o.key })) });
    });
    const extras = allAddons().filter((a) => B.addons.get(a.sku));
    if (extras.length) rows.push({ title: "Extras", items: extras.map((a) => ({ label: `${B.addons.get(a.sku) > 1 ? B.addons.get(a.sku) + "× " : ""}${a.title}`, color: PREMIUM_COLORS[a.sku], sec: "addon", key: a.sku })) });
    if (!rows.length) return `<p class="brecap-empty">${B.product.builder.visual ? "Tu bowl se va armando aquí mientras eliges." : "Elige tus opciones."}</p>`;
    return rows.map((r) => `
      <div class="brecap-row"><span class="brecap-title">${escapeHtml(r.title)}</span>
        ${r.items.map((i) => `<button type="button" class="bchip" data-sec="${i.sec}" data-key="${escapeHtml(i.key)}" aria-label="Quitar ${escapeHtml(i.label)}">${i.color ? `<span class="bopt-dot" style="--c:${escapeHtml(i.color)}"></span>` : ""}${escapeHtml(i.label)}<span class="bchip-x" aria-hidden="true">×</span></button>`).join("")}
      </div>`).join("");
  }

  function flyLabel(text) {
    const fly = el("bowlFly");
    if (!fly || reduceMotion) return;
    const s = document.createElement("span");
    s.className = "bowl-fly-label";
    s.textContent = "+ " + text;
    s.style.left = `${30 + Math.random() * 40}%`;
    fly.appendChild(s);
    setTimeout(() => s.remove(), 1300);
  }

  function refreshAll() {
    if (B.bowl) render(B.bowl, bowlSpec());
    el("builderRecap").innerHTML = recapHtml();
    renderSteps();
    const missing = missingGroups();
    const add = el("builderAdd");
    add.classList.toggle("is-incomplete", missing.length > 0);
    el("builderAddLabel").textContent = missing.length
      ? `Falta ${missing[0].title.toLowerCase()}${missing.length > 1 ? ` y ${missing.length - 1} más` : ""}`
      : "Agregar";
    el("builderPrice").textContent = money(unitPrice() * B.qty);
    el("builderQty").textContent = String(B.qty);
  }

  function goTo(secId, smooth = true) {
    const target = el("bsec-" + secId);
    const scroller = el("builderScroll");
    if (!target || !scroller) return;
    const top = target.offsetTop - 4;
    scroller.scrollTo({ top, behavior: smooth && !reduceMotion ? "smooth" : "auto" });
  }

  function nextSectionId(secId) {
    const i = B.sections.findIndex((s) => s.id === secId);
    const next = B.sections[i + 1];
    return next ? next.id : null;
  }

  function shake(node) {
    if (!node || reduceMotion) return;
    node.classList.remove("is-shake");
    void node.offsetWidth;
    node.classList.add("is-shake");
  }

  function onOptionClick(btn, ev) {
    const secId = btn.dataset.section;
    const key = btn.dataset.key;
    const sec = B.sections.find((s) => s.id === secId);
    if (!sec) return;
    B.visited.add(secId);

    if (sec.kind === "size") {
      B.sizeSku = key;
      refreshSection("size");
      refreshAll();
      return;
    }

    if (sec.kind === "addons") {
      const q = ev.target.closest(".bopt-qbtn");
      const cur = B.addons.get(key) || 0;
      const a = sec.list.find((x) => x.sku === key);
      let next;
      if (q) next = q.dataset.act === "inc" ? Math.min(cur + 1, MAX_ADDON_QTY) : cur - 1;
      else next = cur ? 0 : 1;
      if (next > 0) B.addons.set(key, next); else B.addons.delete(key);
      if (next > cur && a) flyLabel(a.title);
      refreshSection(secId);
      refreshAll();
      return;
    }

    const g = sec.group;
    const picked = B.picked[g.key] || (B.picked[g.key] = []);
    const qbtn = ev.target.closest(".bopt-qbtn");
    const full = () => {
      shake(btn.closest(".bgroup"));
      shake(document.querySelector(`[data-count="${secId}"]`));
      if (window.FHMenuToast) window.FHMenuToast(`Máximo ${g.max} en ${g.title.toLowerCase()}. Quita uno para cambiarlo.`);
    };
    let added = false;
    if (qbtn && g.repeatable) {
      // − / + de un topping repetible: una porción más o una menos de ese mismo.
      if (qbtn.dataset.act === "inc") {
        if (picked.length >= g.max) return full();
        picked.push(key); added = true;
      } else {
        picked.splice(picked.lastIndexOf(key), 1);
      }
    } else if (picked.includes(key)) {
      B.picked[g.key] = picked.filter((k) => k !== key);
    } else if (g.max === 1) { picked.splice(0, picked.length, key); added = true; }
    else if (picked.length >= g.max) return full();
    else { picked.push(key); added = true; }

    if (added) {
      const o = g.options.find((x) => x.key === key);
      if (o) flyLabel(o.label);
    }
    refreshSection(secId);
    refreshAll();
    // Grupo completo: pasa solo al siguiente paso.
    if (added && (B.picked[g.key] || []).length >= g.max) {
      const next = nextSectionId(secId);
      if (next) setTimeout(() => goTo(next), 380);
    }
  }

  function onRecapClick(chip) {
    const sec = chip.dataset.sec, key = chip.dataset.key;
    if (sec === "addon") {
      B.addons.delete(key);
      ["warm", "cold", "flat"].forEach(refreshSection);
    } else {
      const g = groupDef(sec.slice(2));
      if (g) B.picked[g.key] = (B.picked[g.key] || []).filter((k) => k !== key);
      refreshSection(sec);
    }
    refreshAll();
  }

  function setupSpy() {
    if (B.observer) B.observer.disconnect();
    const scroller = el("builderScroll");
    B.observer = new IntersectionObserver((entries) => {
      entries.forEach((en) => {
        if (!en.isIntersecting) return;
        const id = en.target.dataset.section;
        document.querySelectorAll(".bstep").forEach((b) => {
          const on = b.dataset.go === id;
          b.classList.toggle("is-active", on);
          if (on) b.scrollIntoView({ inline: "center", block: "nearest", behavior: reduceMotion ? "auto" : "smooth" });
        });
      });
    }, { root: scroller, rootMargin: "-20% 0px -65% 0px" });
    document.querySelectorAll(".bgroup").forEach((s) => B.observer.observe(s));
  }

  function open(product, opts) {
    B.product = product;
    B.onAdd = opts.onAdd;
    B.sizeSku = product.sizes[0] ? product.sizes[0].sku : null;
    B.picked = {};
    B.addons = new Map();
    B.qty = 1;
    B.visited = new Set();
    B.sections = sectionsFor(product);

    const root = el("builderBackdrop");
    const visual = product.builder.visual;
    root.classList.toggle("has-visual", Boolean(visual));
    root.classList.remove("is-compact");
    el("builderEyebrow").textContent = opts.eyebrow || "";
    el("builderTitle").textContent = product.title;
    el("builderDesc").textContent = product.description || "";

    const stage = el("bowlWrap");
    stage.innerHTML = "";
    B.bowl = null;
    if (visual) {
      const svg = document.createElementNS(SVG_NS, "svg");
      svg.id = "builderBowlSvg";
      svg.setAttribute("class", "bowl-svg");
      svg.setAttribute("role", "img");
      svg.setAttribute("aria-label", "Así se ve tu bowl");
      stage.appendChild(svg);
      const fly = document.createElement("div");
      fly.className = "bowl-fly";
      fly.id = "bowlFly";
      stage.appendChild(fly);
      B.bowl = createBowl(svg);
    } else if (product.image_url) {
      stage.innerHTML = `<img class="builder-photo" src="${escapeHtml(product.image_url)}" alt="${escapeHtml(product.title)}">`;
    } else if (opts.illustration) {
      stage.innerHTML = opts.illustration;
    }

    renderSections();
    refreshAll();
    root.hidden = false;
    document.body.classList.add("modal-open");
    el("builderScroll").scrollTop = 0;
    setupSpy();
    setTimeout(() => el("builderClose").focus({ preventScroll: true }), 50);
  }

  function close() {
    el("builderBackdrop").hidden = true;
    if (B.observer) B.observer.disconnect();
    B.product = null;
    const others = ["productModalBackdrop", "cartDrawerBackdrop"].some((id) => el(id) && !el(id).hidden);
    if (!others) document.body.classList.remove("modal-open");
  }

  function isOpen() { return !el("builderBackdrop").hidden; }

  function submit() {
    const missing = missingGroups();
    if (missing.length) {
      const id = "g-" + missing[0].key;
      goTo(id);
      setTimeout(() => shake(el("bsec-" + id)), 250);
      return;
    }
    const size = sizeOf();
    const choices = {};
    const groups = [];
    B.product.builder.groups.forEach((g) => {
      const keys = B.picked[g.key] || [];
      if (!keys.length) return;
      choices[g.key] = keys.slice();
      groups.push({ title: g.title, items: groupedLabels(g, keys).map((x) => x.label) });
    });
    // Opciones con costo (chilli crunch): se cobran como adicional, igual que en el servidor.
    const optionCost = [];
    B.product.builder.groups.forEach((g) => (B.picked[g.key] || []).forEach((k) => {
      const o = g.options.find((x) => x.key === k);
      if (o && o.price) optionCost.push(o);
    }));
    const addons = allAddons().filter((a) => B.addons.get(a.sku)).map((a) => ({ sku: a.sku, title: a.title, price: a.price, quantity: B.addons.get(a.sku) }));
    // from_choice: se muestra y suma en el carrito, pero no se manda en addon_skus (el servidor
    // ya lo cobra a partir de `choices`).
    optionCost.forEach((o) => addons.push({ sku: "opt:" + o.key, title: o.label, price: o.price, quantity: 1, from_choice: true }));
    B.onAdd({
      sku: size.sku,
      title: B.product.title,
      size_label: B.product.has_sizes && B.product.sizes.length > 1 ? size.label : null,
      unit_price: size.price,
      quantity: B.qty,
      addons,
      choices,
      choice_groups: groups,
      notes: (el("builderNotes") && el("builderNotes").value.trim()) || "",
    });
    close();
  }

  function wire() {
    const root = el("builderBackdrop");
    if (!root) return;
    el("builderClose").addEventListener("click", close);
    root.addEventListener("click", (e) => { if (e.target === root) close(); });
    el("builderGroups").addEventListener("click", (e) => {
      const btn = e.target.closest(".bopt");
      if (btn) onOptionClick(btn, e);
    });
    el("builderSteps").addEventListener("click", (e) => {
      const b = e.target.closest(".bstep");
      if (b) goTo(b.dataset.go);
    });
    el("builderRecap").addEventListener("click", (e) => {
      const chip = e.target.closest(".bchip");
      if (chip) onRecapClick(chip);
    });
    el("builderQtyMinus").addEventListener("click", () => { if (B.qty > 1) { B.qty -= 1; refreshAll(); } });
    el("builderQtyPlus").addEventListener("click", () => { if (B.qty < MAX_ITEM_QTY) { B.qty += 1; refreshAll(); } });
    el("builderAdd").addEventListener("click", submit);
    // En el celular, al bajar por las opciones el bowl se achica pero sigue a la vista.
    el("builderScroll").addEventListener("scroll", () => {
      root.classList.toggle("is-compact", el("builderScroll").scrollTop > 40);
    }, { passive: true });
    document.addEventListener("keydown", (e) => { if (e.key === "Escape" && isOpen()) close(); });
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", wire);
  else wire();

  window.FHBuilder = { open, close, isOpen };
})();
