/* ==============================================================================
   FARMHOUSE - Menú Digital Interactivo (/menu)
   Vanilla JS, sin dependencias. Consume /api/menu/items, /api/branches/ y
   publica pedidos en /api/orders/public.
   ============================================================================== */
(function () {
  "use strict";

  const CART_STORAGE_KEY = "fh_menu_cart_v1";
  const WA_ORIGIN_STORAGE_KEY = "farmhouse_wa_origin";
  const MENU_SESSION_STORAGE_KEY = "farmhouse_menu_session";
  const WA_NUMBER_RE = /^\d{8,15}$/;
  const DELIVERY_SURCHARGE = 3.5;
  const CART_SYNC_DEBOUNCE_MS = 300;

  const state = {
    tabs: [],
    branches: [],
    activeTabKey: null,
    searchQuery: "",
    branchCode: null,
    branchName: "Farmhouse",
    customerName: "",
    customerPhone: "",
    conversationId: null,
    sessionToken: null,
    originWaNumber: null,
    cart: loadCartFromStorage(),
    deliveryType: "pickup",
    paymentMethod: null,
    modal: {
      product: null,
      tabAddons: { warm: [], cold: [], flat: [] },
      addonMode: null, // 'premiums' | 'flat'
      selectedSizeSku: null,
      selectedAddonSkus: new Set(),
      quantity: 1,
    },
  };

  const el = (id) => document.getElementById(id);
  const escapeHtml = (str) => String(str ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
  const money = (n) => `$${Number(n || 0).toFixed(2)}`;
  // Los labels de categoría llegan del backend con un emoji decorativo (ej. "🥗 Salads");
  // la nueva identidad visual evita depender de emojis, así que se recorta aquí en el
  // frontend sin tocar la respuesta de la API.
  const stripLeadingEmoji = (str) => String(str ?? "").replace(/^\p{Extended_Pictographic}️?\s*/u, "").trim();
  const FALLBACK_IMG_HTML = `<div class="product-card-fallback-badge"><img src="/assets/images/farmhouse-logo.png" alt="" class="product-card-fallback-logo"></div>`;

  function syncModalOpenState() {
    const productOpen = el("productModalBackdrop") && !el("productModalBackdrop").hidden;
    const cartOpen = el("cartDrawerBackdrop") && !el("cartDrawerBackdrop").hidden;
    document.body.classList.toggle("modal-open", Boolean(productOpen || cartOpen));
  }

  function loadCartFromStorage() {
    try {
      const raw = localStorage.getItem(CART_STORAGE_KEY);
      return raw ? JSON.parse(raw) : [];
    } catch (e) {
      return [];
    }
  }

  function persistCart() {
    try {
      localStorage.setItem(CART_STORAGE_KEY, JSON.stringify(state.cart));
    } catch (e) { /* almacenamiento no disponible */ }
  }

  function showToast(message, isError = false) {
    const toast = el("toast");
    if (!toast) return;
    toast.textContent = message;
    toast.className = "menu-toast" + (isError ? " error" : "");
    toast.hidden = false;
    clearTimeout(showToast._t);
    showToast._t = setTimeout(() => { toast.hidden = true; }, 3200);
  }

  function parseUrlParams() {
    const params = new URLSearchParams(window.location.search);
    const branchParam = params.get("branch") || params.get("branch_code") || params.get("branch_id");
    const phoneParam = params.get("phone") || params.get("tel");
    const nameParam = params.get("name") || params.get("cliente");
    const convParam = params.get("conv") || params.get("conversation_id");
    const sessionParam = params.get("session");
    const waParam = params.get("wa");

    if (branchParam) state.branchCode = branchParam.trim();
    if (phoneParam) state.customerPhone = phoneParam.trim();
    if (nameParam) state.customerName = nameParam.trim();
    if (convParam) state.conversationId = convParam.trim();

    // Token de sesión firmado por el backend (ver security.auth.create_menu_session_token) que
    // asocia este carrito con la conversationId real de forma segura: `conv=` en la URL es solo
    // informativo, el backend NUNCA confía en él para sincronizar el carrito (evita que alguien
    // edite `conv=20` a mano y termine actualizando el pedido de otra conversación). Se guarda en
    // sessionStorage para sobrevivir un recargo de página sin depender de que el link siga con
    // el parámetro (mismo patrón que WA_ORIGIN_STORAGE_KEY, líneas arriba).
    if (sessionParam) {
      state.sessionToken = sessionParam.trim();
      try { sessionStorage.setItem(MENU_SESSION_STORAGE_KEY, state.sessionToken); } catch (e) { /* no disponible */ }
    } else {
      try {
        const storedSession = sessionStorage.getItem(MENU_SESSION_STORAGE_KEY);
        if (storedSession) state.sessionToken = storedSession;
      } catch (e) { /* no disponible */ }
    }

    // Número de WhatsApp de origen (el chat desde el que el cliente llegó al menú). Solo se
    // acepta un formato de teléfono válido; el backend lo vuelve a validar contra el número
    // oficial antes de usarlo (nunca se confía en un valor de la URL para decidir el destino
    // real del pedido). Se guarda en sessionStorage para sobrevivir un recargo de página.
    if (waParam && WA_NUMBER_RE.test(waParam.trim())) {
      state.originWaNumber = waParam.trim();
      try { sessionStorage.setItem(WA_ORIGIN_STORAGE_KEY, state.originWaNumber); } catch (e) { /* no disponible */ }
    } else {
      try {
        const stored = sessionStorage.getItem(WA_ORIGIN_STORAGE_KEY);
        if (stored && WA_NUMBER_RE.test(stored)) state.originWaNumber = stored;
      } catch (e) { /* no disponible */ }
    }
  }

  // ===================== CARGA INICIAL =====================

  async function init() {
    parseUrlParams();
    wireStaticEvents();
    renderCart();

    try {
      const [menuRes, branchesRes] = await Promise.all([
        fetch("/api/menu/items"),
        fetch("/api/branches/"),
      ]);
      const menuData = await menuRes.json();
      state.branches = branchesRes.ok ? await branchesRes.json() : [];
      state.tabs = menuData.tabs || [];
      state.activeTabKey = state.tabs[0] ? state.tabs[0].key : null;

      applyInitialBranch();
      applyCustomerInfoUI();
      renderCategoryPills();
      renderProducts();
    } catch (err) {
      el("productsGrid").innerHTML = `<div class="menu-loading">No pudimos cargar el menú. Por favor recarga la página.</div>`;
      console.error("[menu_app] Error cargando el menú:", err);
    }
  }

  function applyInitialBranch() {
    const select = el("branchSelect");
    const badge = el("headerBranchBadge");
    const cartTag = el("cartBranchTag");
    const heroLabel = el("heroBranchLabel");

    if (state.branches.length === 0) return;

    let matched = null;
    if (state.branchCode) {
      matched = state.branches.find(b =>
        b.code.toUpperCase() === state.branchCode.toUpperCase() ||
        String(b.id) === String(state.branchCode) ||
        b.name.toLowerCase().includes(state.branchCode.toLowerCase())
      );
    }

    if (!matched && state.branches[0]) {
      matched = state.branches[0];
    }

    if (matched) {
      state.branchCode = matched.code;
      state.branchName = matched.name;
    }

    const setBranchLabels = (name) => {
      if (badge) badge.textContent = name;
      if (cartTag) cartTag.textContent = name;
      if (heroLabel) heroLabel.textContent = name;
    };
    setBranchLabels(state.branchName);

    if (select) {
      select.innerHTML = state.branches.map(
        (b) => `<option value="${escapeHtml(b.code)}" ${b.code === state.branchCode ? "selected" : ""}>${escapeHtml(b.name)}</option>`
      ).join("");

      select.addEventListener("change", (e) => {
        const selectedCode = e.target.value;
        const b = state.branches.find(x => x.code === selectedCode);
        if (b) {
          state.branchCode = b.code;
          state.branchName = b.name;
          setBranchLabels(b.name);
          showToast(`Sucursal actualizada: ${b.name}`);
          scheduleCartSync();
        }
      });
    }
  }

  function applyCustomerInfoUI() {
    const summary = el("customerSummaryBadge");
    const inputs = el("customerInputFields");
    const sumName = el("summaryCustomerName");
    const sumPhone = el("summaryCustomerPhone");
    const inpName = el("customerName");
    const inpPhone = el("customerPhone");

    if (state.customerName || state.customerPhone) {
      if (summary) summary.hidden = false;
      if (inputs) inputs.style.display = "none";
      if (sumName) sumName.textContent = state.customerName || "Cliente";
      if (sumPhone) sumPhone.textContent = state.customerPhone ? (state.customerPhone.startsWith("+") ? state.customerPhone : `+${state.customerPhone}`) : "";
    } else {
      if (summary) summary.hidden = true;
      if (inputs) inputs.style.display = "flex";
    }

    if (inpName && state.customerName) inpName.value = state.customerName;
    if (inpPhone && state.customerPhone) inpPhone.value = state.customerPhone;
  }

  // ===================== CATEGORÍAS Y PRODUCTOS =====================

  function renderCategoryPills() {
    const nav = el("categoryPills");
    if (!nav) return;
    nav.innerHTML = state.tabs.map((tab) => `
      <button class="category-pill ${tab.key === state.activeTabKey ? "active" : ""}" data-tab="${tab.key}">${escapeHtml(stripLeadingEmoji(tab.label))}</button>
    `).join("");
    nav.querySelectorAll(".category-pill").forEach((btn) => {
      btn.addEventListener("click", () => {
        state.activeTabKey = btn.dataset.tab;
        renderCategoryPills();
        renderProducts();
      });
    });
  }

  function getVisibleProducts() {
    const query = state.searchQuery.trim().toLowerCase();
    if (query) {
      const all = state.tabs.flatMap((t) => t.products.map((p) => ({ ...p, _tabKey: t.key })));
      return all.filter((p) => p.title.toLowerCase().includes(query) || (p.description || "").toLowerCase().includes(query));
    }
    const tab = state.tabs.find((t) => t.key === state.activeTabKey);
    return tab ? tab.products.map((p) => ({ ...p, _tabKey: tab.key })) : [];
  }

  function renderProducts() {
    const products = getVisibleProducts();
    const grid = el("productsGrid");
    const emptyState = el("emptyState");

    if (!grid) return;

    if (products.length === 0) {
      grid.innerHTML = "";
      if (emptyState) emptyState.hidden = false;
      return;
    }
    if (emptyState) emptyState.hidden = true;

    grid.innerHTML = products.map((p, idx) => {
      const price = p.sizes[0] ? p.sizes[0].price : 0;
      return `
        <button class="product-card" data-idx="${idx}" type="button" aria-label="${escapeHtml(p.title)}, ${p.has_sizes ? "desde " : ""}${money(price)}">
          <div class="product-card-img-wrap">
            <img class="product-card-photo" src="${escapeHtml(p.image_url)}" alt="${escapeHtml(p.title)}" loading="lazy" onerror="this.parentElement.classList.add('img-error')">
            ${FALLBACK_IMG_HTML}
          </div>
          <div class="product-card-body">
            <div class="product-card-title">${escapeHtml(p.title)}</div>
            <div class="product-card-desc">${escapeHtml(p.description)}</div>
            <div class="product-card-footer">
              <span class="product-card-price">${p.has_sizes ? "Desde " : ""}${money(price)}</span>
              <span class="product-card-add">Agregar</span>
            </div>
          </div>
        </button>
      `;
    }).join("");

    grid.querySelectorAll(".product-card").forEach((card) => {
      card.addEventListener("click", () => {
        const idx = Number(card.dataset.idx);
        const prod = products[idx];
        if (prod) openProductModal(prod);
      });
    });
  }

  // ===================== MODAL DE PERSONALIZACIÓN =====================

  function openProductModal(product) {
    const tab = state.tabs.find((t) => t.key === (product._tabKey || state.activeTabKey));
    state.modal.product = product;
    state.modal.tabAddons = (tab && tab.addons) ? tab.addons : { warm: [], cold: [], flat: [] };
    state.modal.addonMode = (tab && tab.addon_mode) ? tab.addon_mode : null;
    state.modal.selectedSizeSku = product.sizes[0] ? product.sizes[0].sku : null;
    state.modal.selectedAddonSkus = new Set();
    state.modal.quantity = 1;

    el("productModalTitle").textContent = product.title;
    el("productModalDesc").textContent = product.description;
    const modalImgWrap = el("productModalImgWrap");
    const modalImg = el("productModalImg");
    modalImgWrap.classList.remove("img-error");
    modalImg.onerror = () => modalImgWrap.classList.add("img-error");
    modalImg.src = product.image_url || "";
    modalImg.alt = product.title;
    el("productNotes").value = "";
    el("qtyValue").textContent = "1";

    const sizeSection = el("sizeSection");
    const sizeOptions = el("sizeOptions");
    if (product.has_sizes && product.sizes.length > 1) {
      sizeSection.hidden = false;
      sizeOptions.innerHTML = product.sizes.map((s) => `
        <button type="button" class="option-pill ${s.sku === state.modal.selectedSizeSku ? "active" : ""}" data-sku="${escapeHtml(s.sku)}">
          ${escapeHtml(s.label)} (${money(s.price)})
        </button>
      `).join("");
      sizeOptions.querySelectorAll(".option-pill").forEach((btn) => {
        btn.addEventListener("click", () => {
          state.modal.selectedSizeSku = btn.dataset.sku;
          sizeOptions.querySelectorAll(".option-pill").forEach((b) => b.classList.toggle("active", b === btn));
          updateModalPrice();
        });
      });
    } else {
      sizeSection.hidden = true;
    }

    renderAddonList("warmAddonsSection", "warmAddonsList", state.modal.tabAddons.warm || []);
    renderAddonList("coldAddonsSection", "coldAddonsList", state.modal.tabAddons.cold || []);
    renderAddonList("flatAddonsSection", "flatAddonsList", state.modal.tabAddons.flat || []);

    updateModalPrice();
    el("productModalBackdrop").hidden = false;
    syncModalOpenState();
  }

  function renderAddonList(sectionId, listId, addons) {
    const section = el(sectionId);
    const list = el(listId);
    if (!section || !list) return;

    if (!addons || addons.length === 0) {
      section.hidden = true;
      list.innerHTML = "";
      return;
    }
    section.hidden = false;
    list.innerHTML = addons.map((a) => `
      <label class="addon-item">
        <input type="checkbox" value="${escapeHtml(a.sku)}" data-price="${a.price}" data-title="${escapeHtml(a.title)}">
        <span class="addon-name">${escapeHtml(a.title)}</span>
        <span class="addon-price">+${money(a.price)}</span>
      </label>
    `).join("");

    list.querySelectorAll("input[type=checkbox]").forEach((cb) => {
      cb.addEventListener("change", () => {
        if (cb.checked) {
          state.modal.selectedAddonSkus.add(cb.value);
        } else {
          state.modal.selectedAddonSkus.delete(cb.value);
        }
        updateModalPrice();
      });
    });
  }

  function getModalCurrentUnitPrice() {
    const p = state.modal.product;
    if (!p) return 0;
    const size = p.sizes.find((s) => s.sku === state.modal.selectedSizeSku) || p.sizes[0];
    let price = size ? size.price : 0;

    const allAddons = [
      ...(state.modal.tabAddons.warm || []),
      ...(state.modal.tabAddons.cold || []),
      ...(state.modal.tabAddons.flat || []),
    ];
    state.modal.selectedAddonSkus.forEach((sku) => {
      const a = allAddons.find((x) => x.sku === sku);
      if (a) price += a.price;
    });
    return price;
  }

  function updateModalPrice() {
    const unit = getModalCurrentUnitPrice();
    const total = unit * state.modal.quantity;
    el("productModalPrice").textContent = money(total);
  }

  function closeProductModal() {
    el("productModalBackdrop").hidden = true;
    state.modal.product = null;
    syncModalOpenState();
  }

  function openCartDrawer() {
    el("cartDrawerBackdrop").hidden = false;
    syncModalOpenState();
  }

  function closeCartDrawer() {
    el("cartDrawerBackdrop").hidden = true;
    syncModalOpenState();
  }

  // ===================== CARRITO Y TOTALES =====================

  function addItemFromModal() {
    const p = state.modal.product;
    if (!p) return;
    const size = p.sizes.find((s) => s.sku === state.modal.selectedSizeSku) || p.sizes[0];
    const allAddons = [
      ...(state.modal.tabAddons.warm || []),
      ...(state.modal.tabAddons.cold || []),
      ...(state.modal.tabAddons.flat || []),
    ];
    const addons = Array.from(state.modal.selectedAddonSkus).map((sku) => {
      const a = allAddons.find((x) => x.sku === sku);
      return { sku: a.sku, title: a.title, price: a.price };
    });

    state.cart.push({
      uid: "item_" + Date.now() + "_" + Math.random().toString(36).substr(2, 6),
      sku: size.sku,
      title: p.title,
      size_label: size.label || null,
      unit_price: size.price,
      quantity: state.modal.quantity,
      addons,
      notes: el("productNotes").value.trim(),
    });

    persistCart();
    renderCart();
    closeProductModal();
    showToast(`Agregado al pedido: ${p.title}`);
    scheduleCartSync();
  }

  function renderCart() {
    const list = el("cartItemsList");
    const countEl = el("cartCount");
    const subtotalEl = el("cartSubtotal");
    const floatingBtn = el("floatingCartBtn");
    const headerBtn = el("headerCartBtn");
    const headerCountEl = el("headerCartCount");

    const totalQty = state.cart.reduce((acc, it) => acc + it.quantity, 0);
    const subtotal = state.cart.reduce((acc, it) => {
      const addSum = it.addons.reduce((s, a) => s + a.price, 0);
      return acc + (it.unit_price + addSum) * it.quantity;
    }, 0);

    if (countEl) countEl.textContent = totalQty;
    if (subtotalEl) subtotalEl.textContent = money(subtotal);
    if (floatingBtn) floatingBtn.hidden = totalQty === 0;
    if (headerCountEl) {
      headerCountEl.textContent = totalQty > 99 ? "99+" : String(totalQty);
      headerCountEl.hidden = totalQty === 0;
    }
    if (headerBtn) headerBtn.setAttribute("aria-label", `Ver pedido, ${totalQty} producto${totalQty === 1 ? "" : "s"}, ${money(subtotal)}`);

    if (!list) return;

    if (state.cart.length === 0) {
      list.innerHTML = `<div class="cart-empty">Tu pedido está vacío. Elige tus platos favoritos del menú.</div>`;
    } else {
      list.innerHTML = state.cart.map((it, idx) => {
        const itemAddTotal = it.addons.reduce((s, a) => s + a.price, 0);
        const itemLineTotal = (it.unit_price + itemAddTotal) * it.quantity;
        return `
          <div class="cart-item">
            <div class="cart-item-header">
              <span class="cart-item-title">${escapeHtml(it.title)}${it.size_label ? ` (${escapeHtml(it.size_label)})` : ""}</span>
              <span class="cart-item-price">${money(itemLineTotal)}</span>
            </div>
            ${it.addons.length ? `<div class="cart-item-addons">${it.addons.map((a) => `+ ${escapeHtml(a.title)} (${money(a.price)})`).join("<br>")}</div>` : ""}
            ${it.notes ? `<div class="cart-item-notes">Nota: ${escapeHtml(it.notes)}</div>` : ""}
            <div class="cart-item-controls">
              <div class="qty-stepper qty-stepper-sm">
                <button type="button" class="btn-cart-dec" data-idx="${idx}" aria-label="Restar cantidad de ${escapeHtml(it.title)}">−</button>
                <span>${it.quantity}</span>
                <button type="button" class="btn-cart-inc" data-idx="${idx}" aria-label="Sumar cantidad de ${escapeHtml(it.title)}">+</button>
              </div>
              <button type="button" class="btn-cart-del" data-idx="${idx}" aria-label="Eliminar ${escapeHtml(it.title)} del pedido">Eliminar</button>
            </div>
          </div>
        `;
      }).join("");

      list.querySelectorAll(".btn-cart-inc").forEach((b) => {
        b.addEventListener("click", () => {
          const idx = Number(b.dataset.idx);
          if (state.cart[idx]) state.cart[idx].quantity += 1;
          persistCart();
          renderCart();
          scheduleCartSync();
        });
      });
      list.querySelectorAll(".btn-cart-dec").forEach((b) => {
        b.addEventListener("click", () => {
          const idx = Number(b.dataset.idx);
          if (state.cart[idx]) {
            state.cart[idx].quantity -= 1;
            if (state.cart[idx].quantity <= 0) state.cart.splice(idx, 1);
          }
          persistCart();
          renderCart();
          scheduleCartSync();
        });
      });
      list.querySelectorAll(".btn-cart-del").forEach((b) => {
        b.addEventListener("click", () => {
          state.cart.splice(Number(b.dataset.idx), 1);
          persistCart();
          renderCart();
          scheduleCartSync();
        });
      });
    }

    updateTotals();
  }

  function updateTotals() {
    const subtotal = state.cart.reduce((acc, it) => {
      const addSum = it.addons.reduce((s, a) => s + a.price, 0);
      return acc + (it.unit_price + addSum) * it.quantity;
    }, 0);
    const delivery = state.deliveryType === "delivery" ? DELIVERY_SURCHARGE : 0;
    const finalTotal = subtotal + delivery;

    if (el("totalSubtotal")) el("totalSubtotal").textContent = money(subtotal);
    if (el("totalDelivery")) el("totalDelivery").textContent = money(delivery);
    if (el("totalFinal")) el("totalFinal").textContent = money(finalTotal);
  }

  // ===================== SINCRONIZACIÓN DEL CARRITO CON EL PANEL =====================
  // El panel administrativo NO puede leer localStorage/sessionStorage del navegador del
  // cliente (son dispositivos y sesiones distintas), así que cada cambio relevante del
  // carrito se manda al backend, que lo guarda como el "carrito activo" de esta conversación
  // y lo retransmite por WebSocket a los agentes de la sucursal (ver PUT /api/orders/cart).
  // Se debounce para no mandar una petición por cada tecla/click cuando el cliente hace
  // varios cambios seguidos (agregar producto, subir cantidad, etc.).

  let cartSyncTimer = null;
  let cartSyncInFlight = false;
  let cartSyncPendingRetry = false;

  function setLiveSyncIndicator(status) {
    // status: 'idle' | 'syncing' | 'synced' | 'offline'
    const dot = el("cartSyncDot");
    if (!dot) return;
    dot.classList.remove("sync-syncing", "sync-synced", "sync-offline");
    if (status === "syncing") dot.classList.add("sync-syncing");
    else if (status === "synced") dot.classList.add("sync-synced");
    else if (status === "offline") dot.classList.add("sync-offline");
  }

  function scheduleCartSync() {
    if (!state.sessionToken) return; // Enlace viejo sin sesión: el carrito funciona local, sin sincronizar.
    clearTimeout(cartSyncTimer);
    cartSyncTimer = setTimeout(syncCartNow, CART_SYNC_DEBOUNCE_MS);
  }

  async function syncCartNow() {
    if (!state.sessionToken) return;
    if (cartSyncInFlight) { cartSyncPendingRetry = true; return; }

    const branchSelect = el("branchSelect");
    const branchCode = state.branchCode || (branchSelect ? branchSelect.value : "");
    const deliveryAddress = el("deliveryAddress") ? el("deliveryAddress").value.trim() : "";

    const payload = {
      session: state.sessionToken,
      branch_code: branchCode || null,
      delivery_type: state.deliveryType,
      delivery_address: state.deliveryType === "delivery" ? (deliveryAddress || null) : null,
      payment_method: state.paymentMethod || null,
      items: state.cart.map((item) => ({
        sku: item.sku,
        quantity: item.quantity,
        addon_skus: item.addons.map((a) => a.sku),
        notes: item.notes || null,
      })),
    };

    cartSyncInFlight = true;
    setLiveSyncIndicator("syncing");
    try {
      const res = await fetch("/api/orders/cart", {
        method: "PUT",
        headers: { "Content-Type": "application/json", "X-Requested-With": "XMLHttpRequest" },
        body: JSON.stringify(payload),
      });
      if (!res.ok) throw new Error("cart sync failed: " + res.status);
      setLiveSyncIndicator("synced");
    } catch (err) {
      // El carrito del cliente sigue funcionando aunque falle la sincronización (Punto 25):
      // no se pierde nada, no se molesta al cliente con errores técnicos, y se reintenta en
      // el siguiente cambio (o de inmediato si hubo cambios mientras esta petición viajaba).
      console.warn("[menu_app] No se pudo sincronizar el carrito con el panel:", err);
      setLiveSyncIndicator("offline");
    } finally {
      cartSyncInFlight = false;
      if (cartSyncPendingRetry) {
        cartSyncPendingRetry = false;
        syncCartNow();
      }
    }
  }

  // ===================== EVENTOS =====================

  function wireStaticEvents() {
    const search = el("searchInput");
    if (search) {
      search.addEventListener("input", (e) => {
        state.searchQuery = e.target.value;
        renderProducts();
      });
    }

    const closeProd = el("productModalClose");
    if (closeProd) closeProd.addEventListener("click", closeProductModal);

    const backProd = el("productModalBackdrop");
    if (backProd) {
      backProd.addEventListener("click", (e) => {
        if (e.target === backProd) closeProductModal();
      });
    }

    const qm = el("qtyMinus");
    const qp = el("qtyPlus");
    if (qm) {
      qm.addEventListener("click", () => {
        if (state.modal.quantity > 1) {
          state.modal.quantity -= 1;
          el("qtyValue").textContent = String(state.modal.quantity);
          updateModalPrice();
        }
      });
    }
    if (qp) {
      qp.addEventListener("click", () => {
        if (state.modal.quantity < 50) {
          state.modal.quantity += 1;
          el("qtyValue").textContent = String(state.modal.quantity);
          updateModalPrice();
        }
      });
    }

    const addBtn = el("addToOrderBtn");
    if (addBtn) addBtn.addEventListener("click", addItemFromModal);

    const floatBtn = el("floatingCartBtn");
    if (floatBtn) floatBtn.addEventListener("click", openCartDrawer);

    const headerCartBtn = el("headerCartBtn");
    if (headerCartBtn) headerCartBtn.addEventListener("click", openCartDrawer);

    const closeCart = el("cartDrawerClose");
    if (closeCart) closeCart.addEventListener("click", closeCartDrawer);

    const backCart = el("cartDrawerBackdrop");
    if (backCart) {
      backCart.addEventListener("click", (e) => {
        if (e.target === backCart) closeCartDrawer();
      });
    }

    document.addEventListener("keydown", (e) => {
      if (e.key !== "Escape") return;
      if (!el("productModalBackdrop").hidden) closeProductModal();
      else if (!el("cartDrawerBackdrop").hidden) closeCartDrawer();
    });

    document.querySelectorAll(".delivery-option").forEach((btn) => {
      btn.addEventListener("click", () => {
        state.deliveryType = btn.dataset.delivery;
        document.querySelectorAll(".delivery-option").forEach((b) => b.classList.toggle("active", b === btn));
        const isDelivery = state.deliveryType === "delivery";
        const section = el("deliveryAddressSection");
        if (section) section.hidden = !isDelivery;
        if (el("deliveryAddress")) el("deliveryAddress").hidden = !isDelivery;
        if (el("deliveryAddressLabel")) el("deliveryAddressLabel").hidden = !isDelivery;
        updateTotals();
        scheduleCartSync();
      });
    });

    const btnDetectGps = el("btnDetectGps");
    if (btnDetectGps) {
      btnDetectGps.addEventListener("click", handleDetectGps);
    }

    const deliveryAddressInput = el("deliveryAddress");
    if (deliveryAddressInput) {
      deliveryAddressInput.addEventListener("input", () => scheduleCartSync());
    }

    document.querySelectorAll("#paymentOptions .option-pill").forEach((btn) => {
      btn.addEventListener("click", () => {
        state.paymentMethod = btn.dataset.payment;
        document.querySelectorAll("#paymentOptions .option-pill").forEach((b) => b.classList.toggle("active", b === btn));
        scheduleCartSync();
      });
    });

    const sendBtn = el("sendOrderBtn");
    if (sendBtn) sendBtn.addEventListener("click", submitOrder);
  }

  // ===================== DETECCIÓN DE UBICACIÓN GPS =====================

  async function handleDetectGps() {
    const btn = el("btnDetectGps");
    const label = el("btnDetectGpsLabel");
    const hint = el("deliveryGpsHint");
    const input = el("deliveryAddress");

    // 1. Validar contexto seguro HTTPS
    if (window.location.protocol === "http:" && window.location.hostname !== "localhost" && window.location.hostname !== "127.0.0.1") {
      window.location.href = window.location.href.replace("http:", "https:");
      return;
    }

    if (!navigator.geolocation) {
      if (hint) {
        hint.innerHTML = "⚠️ Tu navegador o dispositivo no soporta geolocalización. Por favor escribe tu dirección manualmente abajo.";
        hint.className = "delivery-gps-hint error";
        hint.hidden = false;
      }
      if (input) input.focus();
      return;
    }

    if (btn) btn.disabled = true;
    if (label) label.textContent = "Obteniendo ubicación...";
    if (hint) {
      hint.innerHTML = "⏳ Solicitando permiso de ubicación a tu navegador...";
      hint.className = "delivery-gps-hint";
      hint.hidden = false;
    }

    // Función auxiliar para solicitar posición al navegador con Promise
    const getPos = (highAcc) => new Promise((resolve, reject) => {
      navigator.geolocation.getCurrentPosition(resolve, reject, {
        enableHighAccuracy: highAcc,
        timeout: 9000,
        maximumAge: 30000,
      });
    });

    let pos = null;
    let lastErr = null;

    try {
      // Intentar primero con alta precisión (GPS)
      pos = await getPos(true);
    } catch (e1) {
      lastErr = e1;
      // Si falló por timeout o indisponibilidad (común en PCs de escritorio sin chip GPS), intentar con red Wi-Fi/IP
      if (e1.code === 2 || e1.code === 3) {
        try {
          pos = await getPos(false);
        } catch (e2) {
          lastErr = e2;
        }
      }
    }

    if (!pos) {
      if (btn) btn.disabled = false;
      if (label) label.textContent = "Usar mi ubicación actual";

      let helpHtml = "";
      if (lastErr && lastErr.code === 1) {
        helpHtml = `
          <strong>🔒 El navegador tiene bloqueado el permiso de ubicación para este sitio:</strong>
          <ol>
            <li>Haz clic en el <strong>ícono de candado 🔒 o ajustes</strong> arriba junto a la dirección web.</li>
            <li>Cambia <strong>Ubicación</strong> a <strong>"Permitir"</strong> (o "Preguntar").</li>
            <li>Vuelve a tocar el botón <strong>"Usar mi ubicación actual"</strong>.</li>
          </ol>
          <div style="margin-top:6px;font-size:11px;color:var(--fh-text-muted)">O si prefieres, escribe tu dirección manualmente en el recuadro.</div>
        `;
      } else if (lastErr && lastErr.code === 2) {
        helpHtml = `⚠️ <strong>Ubicación no disponible:</strong> No se pudo detectar señal GPS. Escribe tu dirección manual en el recuadro.`;
      } else if (lastErr && lastErr.code === 3) {
        helpHtml = `⚠️ <strong>Tiempo de espera agotado:</strong> El dispositivo tardó demasiado. Puedes escribir tu dirección manual.`;
      } else {
        helpHtml = `⚠️ No se pudo obtener la ubicación. Puedes escribirla manualmente en el recuadro abajo.`;
      }

      if (hint) {
        hint.innerHTML = helpHtml;
        hint.className = "delivery-gps-hint error";
        hint.hidden = false;
      }
      if (input) input.focus();
      return;
    }

    // Éxito obteniendo coordenadas
    const lat = pos.coords.latitude.toFixed(6);
    const lng = pos.coords.longitude.toFixed(6);
    const mapsLink = `https://maps.google.com/?q=${lat},${lng}`;

    if (label) label.textContent = "Actualizar ubicación";
    if (btn) btn.disabled = false;

    let addressText = "";
    try {
      const controller = new AbortController();
      const timeoutId = setTimeout(() => controller.abort(), 2500);
      const res = await fetch(
        `https://nominatim.openstreetmap.org/reverse?format=json&lat=${lat}&lon=${lng}&zoom=18&addressdetails=1`,
        { signal: controller.signal }
      );
      clearTimeout(timeoutId);
      if (res.ok) {
        const data = await res.json();
        const addr = data.address || {};
        const road = addr.road || addr.street || addr.pedestrian || "";
        const neighborhood = addr.neighbourhood || addr.suburb || addr.quarter || addr.city_district || "";
        const city = addr.city || addr.town || addr.municipality || "Panamá";
        const parts = [road, neighborhood, city].filter(Boolean);
        if (parts.length > 0) {
          addressText = `${parts.join(", ")} (GPS: ${mapsLink})`;
        }
      }
    } catch (e) {
      // Si tarda o falla, se continúa con el enlace directo
    }

    if (!addressText) {
      addressText = `Ubicación GPS: ${mapsLink}`;
    }

    if (input) {
      input.value = addressText;
      input.focus();
    }

    if (hint) {
      hint.innerHTML = `✅ <strong>Ubicación detectada.</strong> Puedes escribir detalles adicionales si lo prefieres (ej: edificio, piso o casa).`;
      hint.className = "delivery-gps-hint success";
      hint.hidden = false;
    }

    scheduleCartSync();
  }

  // ===================== ENVÍO DEL PEDIDO =====================

  async function submitOrder() {
    const branchSelect = el("branchSelect");
    const branchCode = state.branchCode || (branchSelect ? branchSelect.value : "");
    const customerName = (el("customerName") && el("customerName").value.trim()) || state.customerName || "Cliente Farmhouse";
    const customerPhone = (el("customerPhone") && el("customerPhone").value.trim()) || state.customerPhone || "507";
    const deliveryAddress = el("deliveryAddress") ? el("deliveryAddress").value.trim() : "";

    if (!branchCode) return showToast("Por favor selecciona una sucursal.", true);
    if (state.cart.length === 0) return showToast("Tu pedido está vacío.", true);
    if (state.deliveryType === "delivery" && !deliveryAddress) return showToast("Por favor escribe tu dirección de entrega.", true);
    if (!state.paymentMethod) return showToast("Por favor selecciona un método de pago.", true);

    const payload = {
      branch_code: branchCode,
      delivery_type: state.deliveryType,
      delivery_address: state.deliveryType === "delivery" ? deliveryAddress : null,
      payment_method: state.paymentMethod,
      customer_name: customerName,
      customer_phone: customerPhone,
      origin_wa: state.originWaNumber || null,
      items: state.cart.map((item) => ({
        sku: item.sku,
        quantity: item.quantity,
        addon_skus: item.addons.map((a) => a.sku),
        notes: item.notes || null,
      })),
    };

    const btn = el("sendOrderBtn");
    const btnLabel = el("sendOrderLabel");
    btn.disabled = true;
    if (btnLabel) btnLabel.textContent = "Enviando...";
    try {
      const res = await fetch("/api/orders/public", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Requested-With": "XMLHttpRequest" },
        body: JSON.stringify(payload),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "No se pudo enviar el pedido.");

      state.cart = [];
      persistCart();
      renderCart();
      closeCartDrawer();
      showToast(`Pedido ${data.order_code} listo. Abriendo WhatsApp...`);
      window.location.href = data.whatsapp_url;
    } catch (err) {
      showToast(err.message || "Error enviando el pedido.", true);
    } finally {
      btn.disabled = false;
      if (btnLabel) btnLabel.textContent = "Enviar pedido por WhatsApp";
    }
  }

  document.addEventListener("DOMContentLoaded", init);
})();
