/* ==============================================================================
   FARMHOUSE - Menú Digital Interactivo (/menu)
   Vanilla JS, sin dependencias. Consume /api/menu/items, /api/branches/ y
   publica pedidos en /api/orders/public.
   ============================================================================== */
(function () {
  "use strict";

  const CART_STORAGE_KEY = "fh_menu_cart_v1";
  const DELIVERY_SURCHARGE = 3.5;

  const state = {
    tabs: [],
    branches: [],
    activeTabKey: null,
    searchQuery: "",
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
    } catch (e) { /* almacenamiento no disponible, seguimos sin persistir */ }
  }

  function showToast(message, isError = false) {
    const toast = el("toast");
    toast.textContent = message;
    toast.className = "menu-toast" + (isError ? " error" : "");
    toast.hidden = false;
    clearTimeout(showToast._t);
    showToast._t = setTimeout(() => { toast.hidden = true; }, 3200);
  }

  // ===================== CARGA INICIAL =====================

  async function init() {
    prefillPhoneFromUrl();
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

      renderBranchSelect();
      renderCategoryPills();
      renderProducts();
    } catch (err) {
      el("productsGrid").innerHTML = `<div class="menu-loading">No pudimos cargar el menú. Por favor recarga la página. 🙏</div>`;
      console.error("[menu_app] Error cargando el menú:", err);
    }
  }

  function prefillPhoneFromUrl() {
    const params = new URLSearchParams(window.location.search);
    const phone = params.get("phone");
    if (phone) el("customerPhone").value = phone;
  }

  function renderBranchSelect() {
    const select = el("branchSelect");
    select.innerHTML = '<option value="">Sucursal...</option>' + state.branches.map(
      (b) => `<option value="${escapeHtml(b.code)}">${escapeHtml(b.name)}</option>`
    ).join("");
  }

  // ===================== CATEGORÍAS Y PRODUCTOS =====================

  function renderCategoryPills() {
    const nav = el("categoryPills");
    nav.innerHTML = state.tabs.map((tab) => `
      <button class="category-pill ${tab.key === state.activeTabKey ? "active" : ""}" data-tab="${tab.key}">${escapeHtml(tab.label)}</button>
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

    if (products.length === 0) {
      grid.innerHTML = "";
      emptyState.hidden = false;
      return;
    }
    emptyState.hidden = true;

    grid.innerHTML = products.map((p, idx) => {
      const price = p.sizes[0] ? p.sizes[0].price : 0;
      return `
        <button class="product-card" data-idx="${idx}">
          <div class="product-card-img-wrap">
            <img src="${escapeHtml(p.image_url)}" alt="${escapeHtml(p.title)}" loading="lazy" onerror="this.style.opacity='0.15'">
          </div>
          <div class="product-card-body">
            <div class="product-card-title">${escapeHtml(p.title)}</div>
            <div class="product-card-desc">${escapeHtml(p.description)}</div>
            <div class="product-card-footer">
              <span class="product-card-price">${p.has_sizes ? "Desde " : ""}${money(price)}</span>
              <span class="product-card-add">+</span>
            </div>
          </div>
        </button>
      `;
    }).join("");

    grid.querySelectorAll(".product-card").forEach((card) => {
      card.addEventListener("click", () => openProductModal(products[Number(card.dataset.idx)]));
    });
  }

  el("searchInput").addEventListener("input", (e) => {
    state.searchQuery = e.target.value;
    renderProducts();
  });

  // ===================== MODAL DE PERSONALIZACIÓN =====================

  function openProductModal(product) {
    const tab = state.tabs.find((t) => t.key === product._tabKey);
    state.modal.product = product;
    state.modal.tabAddons = tab ? tab.addons : { warm: [], cold: [], flat: [] };
    state.modal.selectedSizeSku = product.sizes[0] ? product.sizes[0].sku : null;
    state.modal.selectedAddonSkus = new Set();
    state.modal.quantity = 1;
    el("productNotes").value = "";

    el("productModalImg").src = product.image_url;
    el("productModalImg").alt = product.title;
    el("productModalTitle").textContent = product.title;
    el("productModalDesc").textContent = product.description;
    el("qtyValue").textContent = "1";

    const sizeSection = el("sizeSection");
    if (product.has_sizes) {
      sizeSection.hidden = false;
      el("sizeOptions").innerHTML = product.sizes.map((s) => `
        <button type="button" class="option-pill ${s.sku === state.modal.selectedSizeSku ? "active" : ""}" data-sku="${escapeHtml(s.sku)}">
          ${escapeHtml(s.label)} · ${money(s.price)}
        </button>
      `).join("");
      el("sizeOptions").querySelectorAll(".option-pill").forEach((btn) => {
        btn.addEventListener("click", () => {
          state.modal.selectedSizeSku = btn.dataset.sku;
          el("sizeOptions").querySelectorAll(".option-pill").forEach((b) => b.classList.toggle("active", b === btn));
          updateModalPrice();
        });
      });
    } else {
      sizeSection.hidden = true;
    }

    renderAddonSection("warmAddonsSection", "warmAddonsList", state.modal.tabAddons.warm);
    renderAddonSection("coldAddonsSection", "coldAddonsList", state.modal.tabAddons.cold);
    if (state.modal.tabAddons.flat.length) {
      el("flatAddonsTitle").textContent = tab && tab.key === "toasties" ? "Adicionales" : (tab && tab.key === "smoothies" ? "Extras del Smoothie" : "Adicionales");
      renderAddonSection("flatAddonsSection", "flatAddonsList", state.modal.tabAddons.flat);
    } else {
      el("flatAddonsSection").hidden = true;
    }

    updateModalPrice();
    el("productModalBackdrop").hidden = false;
  }

  function renderAddonSection(sectionId, listId, addons) {
    const section = el(sectionId);
    const list = el(listId);
    if (!addons || addons.length === 0) {
      section.hidden = true;
      return;
    }
    section.hidden = false;
    list.innerHTML = addons.map((a) => `
      <button type="button" class="addon-row" data-sku="${escapeHtml(a.sku)}">
        <span class="addon-row-label"><span class="addon-checkbox">✓</span> ${escapeHtml(a.title)}</span>
        <span class="addon-row-price">+${money(a.price)}</span>
      </button>
    `).join("");
    list.querySelectorAll(".addon-row").forEach((row) => {
      row.addEventListener("click", () => {
        const sku = row.dataset.sku;
        if (state.modal.selectedAddonSkus.has(sku)) {
          state.modal.selectedAddonSkus.delete(sku);
          row.classList.remove("active");
        } else {
          state.modal.selectedAddonSkus.add(sku);
          row.classList.add("active");
        }
        updateModalPrice();
      });
    });
  }

  function findAddonBySku(sku) {
    const { warm, cold, flat } = state.modal.tabAddons;
    return [...warm, ...cold, ...flat].find((a) => a.sku === sku);
  }

  function getModalUnitPrice() {
    const product = state.modal.product;
    const sizeInfo = product.sizes.find((s) => s.sku === state.modal.selectedSizeSku) || product.sizes[0];
    let price = sizeInfo ? sizeInfo.price : 0;
    state.modal.selectedAddonSkus.forEach((sku) => {
      const addon = findAddonBySku(sku);
      if (addon) price += addon.price;
    });
    return price;
  }

  function updateModalPrice() {
    const total = getModalUnitPrice() * state.modal.quantity;
    el("productModalPrice").textContent = money(total);
  }

  el("qtyMinus").addEventListener("click", () => {
    state.modal.quantity = Math.max(1, state.modal.quantity - 1);
    el("qtyValue").textContent = state.modal.quantity;
    updateModalPrice();
  });
  el("qtyPlus").addEventListener("click", () => {
    state.modal.quantity = Math.min(20, state.modal.quantity + 1);
    el("qtyValue").textContent = state.modal.quantity;
    updateModalPrice();
  });

  el("productModalClose").addEventListener("click", () => { el("productModalBackdrop").hidden = true; });
  el("productModalBackdrop").addEventListener("click", (e) => {
    if (e.target === el("productModalBackdrop")) el("productModalBackdrop").hidden = true;
  });

  el("addToOrderBtn").addEventListener("click", () => {
    const product = state.modal.product;
    const sizeInfo = product.sizes.find((s) => s.sku === state.modal.selectedSizeSku) || product.sizes[0];
    const addons = [...state.modal.selectedAddonSkus].map((sku) => findAddonBySku(sku)).filter(Boolean);
    const unitPrice = sizeInfo.price + addons.reduce((sum, a) => sum + a.price, 0);

    state.cart.push({
      cartId: `${Date.now()}_${Math.random().toString(36).slice(2, 8)}`,
      sku: sizeInfo.sku,
      title: product.title + (sizeInfo.label && sizeInfo.label !== "Único" ? ` (${sizeInfo.label})` : ""),
      imageUrl: product.image_url,
      addons: addons.map((a) => ({ sku: a.sku, title: a.title, price: a.price })),
      quantity: state.modal.quantity,
      notes: el("productNotes").value.trim(),
      unitPrice,
      lineTotal: unitPrice * state.modal.quantity,
    });
    persistCart();
    renderCart();
    el("productModalBackdrop").hidden = true;
    showToast(`✓ ${product.title} agregado al pedido`);
  });

  // ===================== CARRITO / CHECKOUT =====================

  function renderCart() {
    const count = state.cart.reduce((sum, i) => sum + i.quantity, 0);
    const subtotal = state.cart.reduce((sum, i) => sum + i.lineTotal, 0);

    el("floatingCartBtn").hidden = count === 0;
    el("cartCount").textContent = count;
    el("cartSubtotal").textContent = money(subtotal);

    const list = el("cartItemsList");
    if (state.cart.length === 0) {
      list.innerHTML = `<div class="cart-empty">Tu pedido está vacío. Agrega platos desde el menú 🌿</div>`;
    } else {
      list.innerHTML = state.cart.map((item) => `
        <div class="cart-item-row">
          <div class="cart-item-details">
            <div class="cart-item-name">${item.quantity}x ${escapeHtml(item.title)}</div>
            ${item.addons.length ? `<div class="cart-item-meta">+ ${item.addons.map((a) => escapeHtml(a.title)).join(", ")}</div>` : ""}
            ${item.notes ? `<div class="cart-item-meta">📝 ${escapeHtml(item.notes)}</div>` : ""}
          </div>
          <div class="cart-item-right">
            <span class="cart-item-price">${money(item.lineTotal)}</span>
            <button class="cart-item-remove" data-cart-id="${item.cartId}">Quitar</button>
          </div>
        </div>
      `).join("");
      list.querySelectorAll(".cart-item-remove").forEach((btn) => {
        btn.addEventListener("click", () => {
          state.cart = state.cart.filter((i) => i.cartId !== btn.dataset.cartId);
          persistCart();
          renderCart();
          updateTotals();
        });
      });
    }
    updateTotals();
  }

  function updateTotals() {
    const subtotal = state.cart.reduce((sum, i) => sum + i.lineTotal, 0);
    const deliveryCost = state.deliveryType === "delivery" ? DELIVERY_SURCHARGE : 0;
    const total = subtotal + deliveryCost;
    el("totalSubtotal").textContent = money(subtotal);
    el("totalDelivery").textContent = money(deliveryCost);
    el("totalFinal").textContent = money(total);
  }

  el("floatingCartBtn").addEventListener("click", () => { el("cartDrawerBackdrop").hidden = false; });
  el("cartDrawerClose").addEventListener("click", () => { el("cartDrawerBackdrop").hidden = true; });
  el("cartDrawerBackdrop").addEventListener("click", (e) => {
    if (e.target === el("cartDrawerBackdrop")) el("cartDrawerBackdrop").hidden = true;
  });

  function wireStaticEvents() {
    document.querySelectorAll(".delivery-option").forEach((btn) => {
      btn.addEventListener("click", () => {
        state.deliveryType = btn.dataset.delivery;
        document.querySelectorAll(".delivery-option").forEach((b) => b.classList.toggle("active", b === btn));
        el("deliveryAddress").hidden = state.deliveryType !== "delivery";
        updateTotals();
      });
    });

    document.querySelectorAll("#paymentOptions .option-pill").forEach((btn) => {
      btn.addEventListener("click", () => {
        state.paymentMethod = btn.dataset.payment;
        document.querySelectorAll("#paymentOptions .option-pill").forEach((b) => b.classList.toggle("active", b === btn));
      });
    });

    el("sendOrderBtn").addEventListener("click", submitOrder);
  }

  async function submitOrder() {
    const branchCode = el("branchSelect").value;
    const customerName = el("customerName").value.trim();
    const customerPhone = el("customerPhone").value.trim();
    const deliveryAddress = el("deliveryAddress").value.trim();

    if (!branchCode) return showToast("Por favor selecciona una sucursal.", true);
    if (state.cart.length === 0) return showToast("Tu pedido está vacío.", true);
    if (state.deliveryType === "delivery" && !deliveryAddress) return showToast("Por favor escribe tu dirección de entrega.", true);
    if (!state.paymentMethod) return showToast("Por favor selecciona un método de pago.", true);
    if (!customerName || customerName.length < 2) return showToast("Por favor escribe tu nombre.", true);
    if (!customerPhone || customerPhone.replace(/\D/g, "").length < 6) return showToast("Por favor escribe un teléfono válido.", true);

    const payload = {
      branch_code: branchCode,
      delivery_type: state.deliveryType,
      delivery_address: state.deliveryType === "delivery" ? deliveryAddress : null,
      payment_method: state.paymentMethod,
      customer_name: customerName,
      customer_phone: customerPhone,
      items: state.cart.map((item) => ({
        sku: item.sku,
        quantity: item.quantity,
        addon_skus: item.addons.map((a) => a.sku),
        notes: item.notes || null,
      })),
    };

    const btn = el("sendOrderBtn");
    btn.disabled = true;
    btn.textContent = "Enviando...";
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
      el("cartDrawerBackdrop").hidden = true;
      showToast(`✓ Pedido ${data.order_code} listo. Abriendo WhatsApp...`);
      window.location.href = data.whatsapp_url;
    } catch (err) {
      showToast(err.message || "Error enviando el pedido.", true);
    } finally {
      btn.disabled = false;
      btn.textContent = "📲 Enviar Pedido a WhatsApp";
    }
  }

  document.addEventListener("DOMContentLoaded", init);
})();
