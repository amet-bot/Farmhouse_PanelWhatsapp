/* ==============================================================================
   FARMHOUSE - "Arma tu bowl" (y todo producto con opciones) del Menú Digital (/menu)

   window.FHBuilder  la hoja para elegir paso a paso: tamaño, cada grupo (base, toppings,
                     dressing, crunch...), premiums o add-ons y notas. Arriba se ve la lista
                     de lo elegido. Devuelve la línea del carrito con onAdd(item).

   Las opciones (qué se puede elegir y cuántas) vienen de /api/menu/items (product.builder,
   ver backend/services/menu_builders.py); el servidor vuelve a validar todo al pedir.
   ============================================================================== */
(function () {
  "use strict";

  const reduceMotion = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  const escapeHtml = (str) => String(str ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
  const money = (n) => `$${Number(n || 0).toFixed(2)}`;

  // Color del puntito de cada premium en los botones y en la lista de lo elegido.
  const PREMIUM_COLORS = {
    PRM_POLLO_SPICED: "#b8642e", PRM_POLLO_ROSTIZADO: "#d8a064", PRM_SALMON_BULGOGI: "#ee8a68",
    PRM_TOFU_BULGOGI: "#e9cf98", PRM_STEAK: "#6e3324", PRM_PARMESAN_CRUMBLE: "#f0dc98",
    PRM_QUESO_CABRA: "#fbf7ee", PRM_FETA: "#fcf9f1", PRM_AVO_SMASH: "#a3c45a", PRM_HUEVO_DURO: "#fbf8f0",
    PRM_HUMMUS: "#dfbd86", PRM_WHIPPED_FETA: "#fffaf2",
    ACAI_SPREAD_PEANUT: "#b07a3f", ACAI_SPREAD_ALMOND: "#c99a64", ACAI_SPREAD_NUTELLA: "#4a2a1c",
  };

  // ======================================================================
  //  HOJA PARA ARMAR / ELEGIR
  // ======================================================================
  const el = (id) => document.getElementById(id);
  const MAX_ITEM_QTY = 20;
  const MAX_ADDON_QTY = 4;
  const B = {
    product: null, onAdd: null, sizeSku: null, picked: {}, addons: new Map(), qty: 1,
    sections: [], observer: null,
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

  function recapHtml() {
    const rows = [];
    B.product.builder.groups.forEach((g) => {
      const items = groupedLabels(g, B.picked[g.key] || []);
      if (items.length) rows.push({ title: g.title, items: items.map((o) => ({ label: o.label, color: o.color, sec: "g-" + g.key, key: o.key })) });
    });
    const extras = allAddons().filter((a) => B.addons.get(a.sku));
    if (extras.length) rows.push({ title: "Extras", items: extras.map((a) => ({ label: `${B.addons.get(a.sku) > 1 ? B.addons.get(a.sku) + "x " : ""}${a.title}`, color: PREMIUM_COLORS[a.sku], sec: "addon", key: a.sku })) });
    if (!rows.length) return `<p class="brecap-empty">${B.product.builder.visual ? "Aquí verás lo que lleva tu bowl." : "Elige tus opciones."}</p>`;
    return rows.map((r) => `
      <div class="brecap-row"><span class="brecap-title">${escapeHtml(r.title)}</span>
        ${r.items.map((i) => `<button type="button" class="bchip" data-sec="${i.sec}" data-key="${escapeHtml(i.key)}" aria-label="Quitar ${escapeHtml(i.label)}">${i.color ? `<span class="bopt-dot" style="--c:${escapeHtml(i.color)}"></span>` : ""}${escapeHtml(i.label)}<span class="bchip-x" aria-hidden="true">×</span></button>`).join("")}
      </div>`).join("");
  }

  function refreshAll() {
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
      let next;
      if (q) next = q.dataset.act === "inc" ? Math.min(cur + 1, MAX_ADDON_QTY) : cur - 1;
      else next = cur ? 0 : 1;
      if (next > 0) B.addons.set(key, next); else B.addons.delete(key);
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
    const isBowl = Boolean(product.builder.visual);
    root.classList.toggle("is-bowl", isBowl);
    root.classList.remove("is-compact");
    el("builderEyebrow").textContent = opts.eyebrow || "";
    el("builderTitle").textContent = product.title;
    el("builderDesc").textContent = product.description || "";

    // Bowls armables: sin imagen, arriba solo la lista de lo elegido. Los demás: su foto.
    const stage = el("builderMedia");
    stage.innerHTML = "";
    if (isBowl) {
      // nada
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
