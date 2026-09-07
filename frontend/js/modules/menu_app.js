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
  const CART_SYNC_DEBOUNCE_MS = 300;
  const PANAMA_CITY_BOUNDS = { south: 8.9, west: -79.66, north: 9.13, east: -79.36 };

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
    deliveryType: "delivery",
    paymentMethod: null,
    fulfillmentType: "asap",
    scheduledFor: null,
    deliveryLatitude: null,
    deliveryLongitude: null,
    deliveryDistanceKm: null,
    deliveryFee: 0,
    deliveryInCity: true,
    map: null,
    customerMarker: null,
    modal: {
      product: null,
      tabAddons: { warm: [], cold: [], flat: [] },
      addonMode: null, // 'premiums' | 'flat'
      selectedSizeSku: null,
      selectedAddonQty: new Map(), // sku -> cantidad de ese adicional (0 = no seleccionado)
      quantity: 1,
    },
  };

  const el = (id) => document.getElementById(id);
  const escapeHtml = (str) => String(str ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
  const money = (n) => `$${Number(n || 0).toFixed(2)}`;
  // El backend cuenta ocurrencias repetidas del mismo SKU en addon_skus para saber la cantidad
  // de ese adicional (ver services/order_pricing.price_cart_items), así que un adicional con
  // quantity=2 se manda como ese SKU repetido 2 veces en la lista plana.
  const flattenAddonSkus = (addons) => addons.flatMap((a) => Array(a.quantity || 1).fill(a.sku));
  function getCombinedDeliveryAddress() {
    return el("deliveryAddress") ? el("deliveryAddress").value.trim() : "";
  }
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
        fetch("/api/menu/items?t=" + Date.now()),
        fetch("/api/branches/?t=" + Date.now()),
      ]);
      const menuData = await menuRes.json();
      state.branches = branchesRes.ok ? await branchesRes.json() : [];
      state.tabs = menuData.tabs || [];
      state.activeTabKey = state.tabs[0] ? state.tabs[0].key : null;

      applyInitialBranch();
      initializeDeliveryMap();
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

    const orderBranches = state.branches.filter((b) => b.accepts_delivery && b.latitude != null && b.longitude != null);
    if (orderBranches.length === 0) return;

    let matched = null;
    if (state.branchCode) {
      matched = orderBranches.find(b =>
        b.code.toUpperCase() === state.branchCode.toUpperCase() ||
        String(b.id) === String(state.branchCode) ||
        b.name.toLowerCase().includes(state.branchCode.toLowerCase())
      );
    }

    if (!matched && orderBranches[0]) {
      matched = orderBranches[0];
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
      select.innerHTML = orderBranches.map(
        (b) => `<option value="${escapeHtml(b.code)}" ${b.code === state.branchCode ? "selected" : ""}>${escapeHtml(b.name)}</option>`
      ).join("");

      select.addEventListener("change", (e) => {
        const selectedCode = e.target.value;
        const b = orderBranches.find(x => x.code === selectedCode);
        if (b) {
          state.branchCode = b.code;
          state.branchName = b.name;
          setBranchLabels(b.name);
          showToast(`Sucursal actualizada: ${b.name}`);
          updateDeliveryQuote();
          scheduleCartSync();
        }
      });
    }
  }

  function applyCustomerInfoUI() {
    // El nombre/teléfono llegan pre-rellenados desde WhatsApp (query params de la URL), pero el
    // campo siempre queda editable: el cliente puede corregirlos si el pedido es para otra persona
    // o si su nombre de WhatsApp no es el que quiere usar.
    const inpName = el("customerName");
    const inpPhone = el("customerPhone");
    if (inpName && state.customerName) inpName.value = state.customerName;
    if (inpPhone && state.customerPhone) inpPhone.value = state.customerPhone;
    updateCheckoutStatus();
  }

  // ===================== MAPA Y CÁLCULO DE DELIVERY =====================

  function haversineKm(lat1, lon1, lat2, lon2) {
    const toRad = (v) => v * Math.PI / 180;
    const dLat = toRad(lat2 - lat1);
    const dLon = toRad(lon2 - lon1);
    const a = Math.sin(dLat / 2) ** 2 + Math.cos(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.sin(dLon / 2) ** 2;
    return 6371.0088 * 2 * Math.asin(Math.sqrt(a));
  }

  function feeForDistance(km) {
    if (km < 2) return 5;
    if (km <= 5) return 10;
    return 15;
  }

  function isInPanamaCity(lat, lng) {
    return lat >= PANAMA_CITY_BOUNDS.south && lat <= PANAMA_CITY_BOUNDS.north &&
      lng >= PANAMA_CITY_BOUNDS.west && lng <= PANAMA_CITY_BOUNDS.east;
  }

  function physicalBranches() {
    return state.branches.filter((b) => b.accepts_delivery && b.latitude != null && b.longitude != null);
  }

  function initializeDeliveryMap() {
    if (!window.L || !el("deliveryMap") || state.map) return;
    state.map = L.map("deliveryMap", { zoomControl: true }).setView([8.995, -79.515], 12);
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 19,
      attribution: "&copy; OpenStreetMap",
    }).addTo(state.map);

    const bounds = [];
    physicalBranches().forEach((branch) => {
      const point = [Number(branch.latitude), Number(branch.longitude)];
      bounds.push(point);
      L.marker(point, { title: `Farmhouse ${branch.name}` })
        .addTo(state.map)
        .bindPopup(`<strong>Farmhouse ${escapeHtml(branch.name)}</strong><br>${escapeHtml(branch.address || "Sucursal")}`);
    });
    if (bounds.length) state.map.fitBounds(bounds, { padding: [24, 24] });

    state.map.on("click", (event) => setDeliveryPin(event.latlng.lat, event.latlng.lng, true));
  }

  function setDeliveryPin(lat, lng, reverseAddress) {
    state.deliveryLatitude = Number(lat);
    state.deliveryLongitude = Number(lng);
    state.deliveryInCity = isInPanamaCity(state.deliveryLatitude, state.deliveryLongitude);

    if (state.map && window.L && !state.customerMarker) {
      state.customerMarker = L.marker([lat, lng], { draggable: true, title: "Tu ubicación" }).addTo(state.map);
      state.customerMarker.on("dragend", (event) => {
        const point = event.target.getLatLng();
        setDeliveryPin(point.lat, point.lng, true);
      });
    } else if (state.customerMarker) {
      state.customerMarker.setLatLng([lat, lng]);
    }
    if (state.customerMarker) state.customerMarker.bindPopup("<strong>Tu ubicación de entrega</strong>").openPopup();
    if (state.map) state.map.panTo([lat, lng]);
    updateDeliveryQuote();
    openCheckoutStep(2);
    if (reverseAddress) reverseGeocodePin(lat, lng);
    scheduleCartSync();
  }

  async function reverseGeocodePin(lat, lng) {
    try {
      const response = await fetch(`https://nominatim.openstreetmap.org/reverse?format=json&lat=${lat}&lon=${lng}&zoom=18&addressdetails=1`);
      if (!response.ok) return;
      const data = await response.json();
      if (data.display_name && el("deliveryAddress")) {
        el("deliveryAddress").value = data.display_name;
        updateCheckoutStatus();
      }
    } catch (error) { /* El pin sigue siendo válido aunque falle el nombre de la calle. */ }
  }

  async function searchDeliveryAddress() {
    const input = el("deliveryAddress");
    const query = input ? input.value.trim() : "";
    if (!query) return showToast("Escribe una dirección para buscarla.", true);
    const button = el("btnSearchAddress");
    if (button) button.disabled = true;
    try {
      const response = await fetch(`https://nominatim.openstreetmap.org/search?format=json&limit=1&countrycodes=pa&q=${encodeURIComponent(query)}`);
      const results = response.ok ? await response.json() : [];
      if (!results.length) return showToast("No encontramos esa dirección. Puedes tocar el punto directamente en el mapa.", true);
      setDeliveryPin(Number(results[0].lat), Number(results[0].lon), false);
      input.value = results[0].display_name || query;
      updateCheckoutStatus();
    } catch (error) {
      showToast("No se pudo buscar ahora. Puedes tocar el punto directamente en el mapa.", true);
    } finally {
      if (button) button.disabled = false;
    }
  }

  function updateDeliveryQuote() {
    const quote = el("deliveryQuote");
    const optionPrice = el("deliveryOptionPrice");
    const selected = state.branches.find((b) => b.code === state.branchCode);
    if (!selected || state.deliveryLatitude == null || state.deliveryLongitude == null) {
      state.deliveryDistanceKm = null;
      state.deliveryFee = 0;
      if (optionPrice) optionPrice.textContent = "Selecciona tu pin";
      if (quote) quote.textContent = "Selecciona una ubicación para ver la distancia y el costo.";
      updateTotals();
      return;
    }
    if (!state.deliveryInCity) {
      state.deliveryDistanceKm = null;
      state.deliveryFee = 0;
      if (optionPrice) optionPrice.textContent = "Fuera de cobertura";
      if (quote) {
        quote.className = "delivery-quote error";
        quote.textContent = "Lamentablemente no hacemos entregas fuera de Ciudad de Panamá. Puedes elegir retiro gratis en cualquiera de nuestras sucursales.";
      }
      if (state.deliveryType === "delivery") setDeliveryType("pickup");
      updateTotals();
      return;
    }
    const km = haversineKm(Number(selected.latitude), Number(selected.longitude), state.deliveryLatitude, state.deliveryLongitude);
    state.deliveryDistanceKm = km;
    state.deliveryFee = feeForDistance(km);
    if (optionPrice) optionPrice.textContent = money(state.deliveryFee);
    if (quote) {
      quote.className = "delivery-quote";
      quote.textContent = `${km.toFixed(2)} km desde Farmhouse ${selected.name}. Delivery: ${money(state.deliveryFee)}.`;
    }
    updateTotals();
  }

  function openCheckoutStep(number) {
    document.querySelectorAll(".checkout-step").forEach((step) => {
      step.open = Number(step.dataset.step) === number;
    });
    if (number === 1 && state.map) setTimeout(() => state.map.invalidateSize(), 80);
  }

  function getCheckoutStatus() {
    const address = el("deliveryAddress") ? el("deliveryAddress").value.trim() : "";
    const name = el("customerName") ? el("customerName").value.trim() : "";
    const phone = el("customerPhone") ? el("customerPhone").value.replace(/\D/g, "") : "";
    const deliveryReady = state.deliveryType === "pickup" || (
      Boolean(address) && state.deliveryLatitude != null && state.deliveryLongitude != null && state.deliveryInCity
    );
    const branchReady = Boolean(state.branchCode);
    const steps = [
      { complete: deliveryReady, message: "Indica la dirección y marca el punto exacto en el mapa." },
      { complete: branchReady && (state.deliveryType === "pickup" || deliveryReady), message: !branchReady ? "Selecciona la sucursal de tu pedido." : "Elige retiro en sucursal o completa la ubicación para delivery." },
      { complete: state.fulfillmentType === "asap" || Boolean(state.scheduledFor), message: "Indica cuándo quieres recibir tu pedido." },
      { complete: Boolean(state.paymentMethod), message: "Selecciona un método de pago." },
      { complete: Boolean(name) && phone.length >= 7, message: !name ? "Escribe tu nombre completo." : "Escribe un número de teléfono válido." },
    ];
    const firstMissing = steps.findIndex((step) => !step.complete);
    return { steps, firstMissing, ready: state.cart.length > 0 && firstMissing === -1 };
  }

  function updateCheckoutStatus() {
    const status = getCheckoutStatus();
    const completed = status.steps.filter((step) => step.complete).length;
    const progress = el("checkoutProgressBar");
    const hint = el("checkoutMissingHint");
    const button = el("sendOrderBtn");
    const buttonLabel = el("sendOrderLabel");

    document.querySelectorAll(".checkout-step").forEach((step, index) => {
      step.classList.toggle("step-complete", status.steps[index]?.complete === true);
    });
    if (progress) progress.style.width = `${(completed / status.steps.length) * 100}%`;
    if (button) button.disabled = !status.ready;
    if (buttonLabel) buttonLabel.textContent = status.ready ? "Confirmar pedido por WhatsApp" : "Completa tus datos para continuar";
    if (hint) {
      hint.classList.toggle("ready", status.ready);
      hint.textContent = status.ready
        ? "Todo listo. Revisaremos contigo el pedido por WhatsApp."
        : state.cart.length === 0
          ? "Agrega al menos un producto para continuar."
          : `Falta: ${status.steps[status.firstMissing].message}`;
    }
  }

  function setDeliveryType(type) {
    state.deliveryType = type;
    document.querySelectorAll(".delivery-option").forEach((button) => button.classList.toggle("active", button.dataset.delivery === type));
    updateTotals();
    updateCheckoutStatus();
  }

  // ===================== CATEGORÍAS Y PRODUCTOS (SCROLL CONTINUO) =====================

  let scrollObserver = null;

  function setupScrollSpy() {
    if (scrollObserver) {
      scrollObserver.disconnect();
    }
    const sections = document.querySelectorAll(".menu-category-section[data-tab]");
    if (!sections.length || !("IntersectionObserver" in window)) return;

    scrollObserver = new IntersectionObserver((entries) => {
      const visible = entries.filter((e) => e.isIntersecting);
      if (visible.length > 0) {
        visible.sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top);
        const activeTabKey = visible[0].target.dataset.tab;
        if (activeTabKey && activeTabKey !== state.activeTabKey) {
          state.activeTabKey = activeTabKey;
          updateActivePillUI(activeTabKey);
        }
      }
    }, {
      root: null,
      rootMargin: "-120px 0px -60% 0px",
      threshold: 0
    });

    sections.forEach((sec) => scrollObserver.observe(sec));
  }

  function updateActivePillUI(tabKey) {
    const nav = el("categoryPills");
    if (!nav) return;
    nav.querySelectorAll(".category-pill").forEach((btn) => {
      const isActive = btn.dataset.tab === tabKey;
      btn.classList.toggle("active", isActive);
      if (isActive) {
        btn.scrollIntoView({ behavior: "smooth", inline: "center", block: "nearest" });
      }
    });
  }

  function renderCategoryPills() {
    const nav = el("categoryPills");
    if (!nav) return;
    nav.innerHTML = state.tabs.map((tab) => `
      <button type="button" class="category-pill ${tab.key === state.activeTabKey ? "active" : ""}" data-tab="${tab.key}">
        ${escapeHtml(stripLeadingEmoji(tab.label))}
      </button>
    `).join("");

    nav.querySelectorAll(".category-pill").forEach((btn) => {
      btn.addEventListener("click", (e) => {
        e.preventDefault();
        const tabKey = btn.dataset.tab;
        state.activeTabKey = tabKey;
        updateActivePillUI(tabKey);

        if (state.searchQuery) {
          state.searchQuery = "";
          const searchInp = el("searchInput");
          if (searchInp) searchInp.value = "";
          renderProducts();
        }

        const sec = el(`section-${tabKey}`);
        if (sec) {
          const headerOffset = 135;
          const elementPosition = sec.getBoundingClientRect().top;
          const offsetPosition = elementPosition + window.pageYOffset - headerOffset;
          window.scrollTo({
            top: offsetPosition,
            behavior: "smooth"
          });
        }
      });
    });
  }

  function renderProductCardHtml(p, tabKey, globalIdx) {
    const price = p.sizes[0] ? p.sizes[0].price : 0;
    return `
      <button class="product-card" data-idx="${globalIdx}" data-tab="${tabKey}" type="button" aria-label="${escapeHtml(p.title)}, ${p.has_sizes ? "desde " : ""}${money(price)}">
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
  }

  function renderProducts() {
    const grid = el("productsGrid");
    const emptyState = el("emptyState");
    if (!grid) return;

    const query = state.searchQuery.trim().toLowerCase();

    // Modo búsqueda
    if (query) {
      if (scrollObserver) scrollObserver.disconnect();
      const all = state.tabs.flatMap((t) => t.products.map((p) => ({ ...p, _tabKey: t.key })));
      const filtered = all.filter((p) => p.title.toLowerCase().includes(query) || (p.description || "").toLowerCase().includes(query));

      if (filtered.length === 0) {
        grid.innerHTML = "";
        if (emptyState) emptyState.hidden = false;
        return;
      }
      if (emptyState) emptyState.hidden = true;

      grid.innerHTML = `
        <div class="menu-category-section search-results-section">
          <div class="menu-category-header">
            <h2 class="menu-category-title">Resultados de búsqueda</h2>
            <span class="menu-category-count">${filtered.length} producto${filtered.length === 1 ? "" : "s"}</span>
          </div>
          <div class="category-products-grid">
            ${filtered.map((p, idx) => renderProductCardHtml(p, p._tabKey, idx)).join("")}
          </div>
        </div>
      `;

      grid.querySelectorAll(".product-card").forEach((card) => {
        card.addEventListener("click", () => {
          const idx = Number(card.dataset.idx);
          const prod = filtered[idx];
          if (prod) openProductModal(prod);
        });
      });
      return;
    }

    // Modo Scroll Continuo por Categorías
    if (emptyState) emptyState.hidden = true;
    const allProductsFlat = [];

    const sectionsHtml = state.tabs.map((tab) => {
      if (!tab.products || tab.products.length === 0) return "";
      const cardsHtml = tab.products.map((p) => {
        const globalIdx = allProductsFlat.length;
        allProductsFlat.push({ ...p, _tabKey: tab.key });
        return renderProductCardHtml(p, tab.key, globalIdx);
      }).join("");

      return `
        <section class="menu-category-section" id="section-${tab.key}" data-tab="${tab.key}">
          <div class="menu-category-header">
            <h2 class="menu-category-title">${escapeHtml(stripLeadingEmoji(tab.label))}</h2>
            <span class="menu-category-count">${tab.products.length} producto${tab.products.length === 1 ? "" : "s"}</span>
          </div>
          <div class="category-products-grid">
            ${cardsHtml}
          </div>
        </section>
      `;
    }).join("");

    grid.innerHTML = sectionsHtml;

    grid.querySelectorAll(".product-card").forEach((card) => {
      card.addEventListener("click", () => {
        const idx = Number(card.dataset.idx);
        const prod = allProductsFlat[idx];
        if (prod) openProductModal(prod);
      });
    });

    setupScrollSpy();
  }

  // ===================== MODAL DE PERSONALIZACIÓN =====================

  function openProductModal(product) {
    const tab = state.tabs.find((t) => t.key === (product._tabKey || state.activeTabKey));
    state.modal.product = product;
    state.modal.tabAddons = (tab && tab.addons) ? tab.addons : { warm: [], cold: [], flat: [] };
    state.modal.addonMode = (tab && tab.addon_mode) ? tab.addon_mode : null;
    state.modal.selectedSizeSku = product.sizes[0] ? product.sizes[0].sku : null;
    state.modal.selectedAddonQty = new Map();
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

  const MAX_ADDON_QTY = 10;

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
      <div class="addon-item" data-sku="${escapeHtml(a.sku)}">
        <div class="addon-item-info">
          <span class="addon-name">${escapeHtml(a.title)}</span>
          <span class="addon-price">+${money(a.price)} c/u</span>
        </div>
        <div class="qty-stepper qty-stepper-sm">
          <button type="button" class="btn-addon-dec" data-sku="${escapeHtml(a.sku)}" aria-label="Restar ${escapeHtml(a.title)}">−</button>
          <span class="addon-qty-value" data-sku="${escapeHtml(a.sku)}">0</span>
          <button type="button" class="btn-addon-inc" data-sku="${escapeHtml(a.sku)}" aria-label="Sumar ${escapeHtml(a.title)}">+</button>
        </div>
      </div>
    `).join("");

    list.querySelectorAll(".btn-addon-inc").forEach((btn) => {
      btn.addEventListener("click", () => {
        const sku = btn.dataset.sku;
        const current = state.modal.selectedAddonQty.get(sku) || 0;
        if (current >= MAX_ADDON_QTY) return;
        state.modal.selectedAddonQty.set(sku, current + 1);
        updateAddonQtyUI(list, sku);
        updateModalPrice();
      });
    });
    list.querySelectorAll(".btn-addon-dec").forEach((btn) => {
      btn.addEventListener("click", () => {
        const sku = btn.dataset.sku;
        const current = state.modal.selectedAddonQty.get(sku) || 0;
        if (current <= 0) return;
        const next = current - 1;
        if (next <= 0) state.modal.selectedAddonQty.delete(sku);
        else state.modal.selectedAddonQty.set(sku, next);
        updateAddonQtyUI(list, sku);
        updateModalPrice();
      });
    });
  }

  function updateAddonQtyUI(list, sku) {
    const qty = state.modal.selectedAddonQty.get(sku) || 0;
    list.querySelectorAll(".addon-qty-value").forEach((valueEl) => {
      if (valueEl.dataset.sku === sku) valueEl.textContent = String(qty);
    });
    list.querySelectorAll(".addon-item").forEach((item) => {
      if (item.dataset.sku === sku) item.classList.toggle("active", qty > 0);
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
    state.modal.selectedAddonQty.forEach((qty, sku) => {
      const a = allAddons.find((x) => x.sku === sku);
      if (a) price += a.price * qty;
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
    updateCheckoutStatus();
    if (state.map) {
      setTimeout(() => {
        state.map.invalidateSize();
        if (state.deliveryLatitude == null) {
          const bounds = physicalBranches().map((branch) => [Number(branch.latitude), Number(branch.longitude)]);
          if (bounds.length) state.map.fitBounds(bounds, { padding: [30, 30] });
        }
      }, 100);
    }
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
    const addons = Array.from(state.modal.selectedAddonQty.entries()).map(([sku, qty]) => {
      const a = allAddons.find((x) => x.sku === sku);
      return { sku: a.sku, title: a.title, price: a.price, quantity: qty };
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
      const addSum = it.addons.reduce((s, a) => s + a.price * (a.quantity || 1), 0);
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
    if (el("summaryItemCount")) el("summaryItemCount").textContent = `${totalQty} producto${totalQty === 1 ? "" : "s"}`;

    if (!list) return;

    if (state.cart.length === 0) {
      list.innerHTML = `<div class="cart-empty">Tu pedido está vacío. Elige tus platos favoritos del menú.</div>`;
    } else {
      list.innerHTML = state.cart.map((it, idx) => {
        const itemAddTotal = it.addons.reduce((s, a) => s + a.price * (a.quantity || 1), 0);
        const itemLineTotal = (it.unit_price + itemAddTotal) * it.quantity;
        return `
          <div class="cart-item">
            <div class="cart-item-header">
              <span class="cart-item-title">${escapeHtml(it.title)}${it.size_label ? ` (${escapeHtml(it.size_label)})` : ""}</span>
              <span class="cart-item-price">${money(itemLineTotal)}</span>
            </div>
            ${it.addons.length ? `<div class="cart-item-addons">${it.addons.map((a) => `+ ${a.quantity > 1 ? `${a.quantity}x ` : ""}${escapeHtml(a.title)} (${money(a.price * (a.quantity || 1))})`).join("<br>")}</div>` : ""}
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
    updateCheckoutStatus();
  }

  function updateTotals() {
    const subtotal = state.cart.reduce((acc, it) => {
      const addSum = it.addons.reduce((s, a) => s + a.price * (a.quantity || 1), 0);
      return acc + (it.unit_price + addSum) * it.quantity;
    }, 0);
    const isDelivery = state.deliveryType === "delivery";
    const deliveryDisplay = isDelivery && state.deliveryLatitude != null ? money(state.deliveryFee) : "$0.00";
    const finalTotal = subtotal + (isDelivery ? state.deliveryFee : 0);

    if (el("totalSubtotal")) el("totalSubtotal").textContent = money(subtotal);
    if (el("totalDelivery")) el("totalDelivery").textContent = deliveryDisplay;
    if (el("totalFinal")) el("totalFinal").textContent = money(finalTotal);
    updateCheckoutStatus();
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
    const deliveryAddress = getCombinedDeliveryAddress();

    const payload = {
      session: state.sessionToken,
      branch_code: branchCode || null,
      delivery_type: state.deliveryType,
      delivery_address: state.deliveryType === "delivery" ? (deliveryAddress || null) : null,
      delivery_building: el("deliveryBuilding") ? (el("deliveryBuilding").value.trim() || null) : null,
      delivery_unit: el("deliveryUnit") ? (el("deliveryUnit").value.trim() || null) : null,
      delivery_reference: el("deliveryReference") ? (el("deliveryReference").value.trim() || null) : null,
      delivery_latitude: state.deliveryType === "delivery" ? state.deliveryLatitude : null,
      delivery_longitude: state.deliveryType === "delivery" ? state.deliveryLongitude : null,
      payment_method: state.paymentMethod || null,
      fulfillment_type: state.fulfillmentType,
      scheduled_for: state.fulfillmentType === "scheduled" ? state.scheduledFor : null,
      items: state.cart.map((item) => ({
        sku: item.sku,
        quantity: item.quantity,
        addon_skus: flattenAddonSkus(item.addons),
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
        if (btn.dataset.delivery === "delivery" && state.deliveryLatitude == null) {
          openCheckoutStep(1);
          return showToast("Primero marca tu ubicación exacta en el mapa.", true);
        }
        if (btn.dataset.delivery === "delivery" && !state.deliveryInCity) {
          return showToast("Esa ubicación está fuera del área de delivery. Elige retiro en sucursal.", true);
        }
        setDeliveryType(btn.dataset.delivery);
        openCheckoutStep(3);
        scheduleCartSync();
      });
    });

    const btnDetectGps = el("btnDetectGps");
    if (btnDetectGps) {
      btnDetectGps.addEventListener("click", handleDetectGps);
    }

    const btnSearchAddress = el("btnSearchAddress");
    if (btnSearchAddress) btnSearchAddress.addEventListener("click", searchDeliveryAddress);

    const deliveryAddressInput = el("deliveryAddress");
    if (deliveryAddressInput) {
      deliveryAddressInput.addEventListener("input", () => {
        updateCheckoutStatus();
        scheduleCartSync();
      });
      deliveryAddressInput.addEventListener("keydown", (event) => {
        if (event.key === "Enter") { event.preventDefault(); searchDeliveryAddress(); }
      });
    }

    ["deliveryBuilding", "deliveryUnit", "deliveryReference"].forEach((id) => {
      const input = el(id);
      if (input) input.addEventListener("input", () => scheduleCartSync());
    });

    ["customerName", "customerPhone"].forEach((id) => {
      const input = el(id);
      if (input) input.addEventListener("input", updateCheckoutStatus);
    });

    document.querySelectorAll(".fulfillment-option").forEach((btn) => {
      btn.addEventListener("click", () => {
        state.fulfillmentType = btn.dataset.fulfillment;
        document.querySelectorAll(".fulfillment-option").forEach((b) => b.classList.toggle("active", b === btn));
        if (el("scheduledTimeWrap")) el("scheduledTimeWrap").hidden = state.fulfillmentType !== "scheduled";
        if (state.fulfillmentType === "asap") {
          state.scheduledFor = null;
          openCheckoutStep(4);
        }
        updateCheckoutStatus();
        scheduleCartSync();
      });
    });

    const scheduledInput = el("scheduledFor");
    if (scheduledInput) {
      const minimum = new Date(Date.now() + 30 * 60000);
      scheduledInput.min = new Date(minimum.getTime() - minimum.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
      scheduledInput.addEventListener("change", () => {
        state.scheduledFor = scheduledInput.value ? new Date(scheduledInput.value).toISOString() : null;
        if (state.scheduledFor) openCheckoutStep(4);
        updateCheckoutStatus();
        scheduleCartSync();
      });
    }

    document.querySelectorAll("#paymentOptions .option-pill").forEach((btn) => {
      btn.addEventListener("click", () => {
        state.paymentMethod = btn.dataset.payment;
        document.querySelectorAll("#paymentOptions .option-pill").forEach((b) => b.classList.toggle("active", b === btn));
        openCheckoutStep(5);
        updateCheckoutStatus();
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
          addressText = parts.join(", ");
        }
      }
    } catch (e) {
      // Si tarda o falla, se continúa con el enlace directo
    }

    if (!addressText) {
      addressText = `Ubicación seleccionada: ${lat}, ${lng}`;
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

    setDeliveryPin(Number(lat), Number(lng), false);
  }

  // ===================== ENVÍO DEL PEDIDO =====================

  async function submitOrder() {
    const branchSelect = el("branchSelect");
    const branchCode = state.branchCode || (branchSelect ? branchSelect.value : "");
    const customerName = (el("customerName") && el("customerName").value.trim()) || state.customerName || "Cliente Farmhouse";
    const customerPhone = (el("customerPhone") && el("customerPhone").value.trim()) || state.customerPhone || "507";
    const deliveryAddressMain = el("deliveryAddress") ? el("deliveryAddress").value.trim() : "";
    const deliveryAddress = getCombinedDeliveryAddress();

    if (!branchCode) return showToast("Por favor selecciona una sucursal.", true);
    if (state.cart.length === 0) return showToast("Tu pedido está vacío.", true);
    if (state.deliveryType === "delivery" && !deliveryAddressMain) return showToast("Por favor indica tu ubicación de entrega.", true);
    if (state.deliveryType === "delivery" && state.deliveryLatitude == null) return showToast("Marca el punto exacto de entrega en el mapa.", true);
    if (state.deliveryType === "delivery" && !state.deliveryInCity) return showToast("No realizamos delivery fuera de Ciudad de Panamá. Puedes elegir retiro gratis.", true);
    if (state.fulfillmentType === "scheduled" && !state.scheduledFor) return showToast("Selecciona la fecha y hora del pedido.", true);
    if (!state.paymentMethod) return showToast("Por favor selecciona un método de pago.", true);

    const payload = {
      branch_code: branchCode,
      delivery_type: state.deliveryType,
      delivery_address: state.deliveryType === "delivery" ? deliveryAddress : null,
      delivery_building: el("deliveryBuilding") ? (el("deliveryBuilding").value.trim() || null) : null,
      delivery_unit: el("deliveryUnit") ? (el("deliveryUnit").value.trim() || null) : null,
      delivery_reference: el("deliveryReference") ? (el("deliveryReference").value.trim() || null) : null,
      delivery_latitude: state.deliveryType === "delivery" ? state.deliveryLatitude : null,
      delivery_longitude: state.deliveryType === "delivery" ? state.deliveryLongitude : null,
      payment_method: state.paymentMethod,
      fulfillment_type: state.fulfillmentType,
      scheduled_for: state.fulfillmentType === "scheduled" ? state.scheduledFor : null,
      customer_name: customerName,
      customer_phone: customerPhone,
      origin_wa: state.originWaNumber || null,
      items: state.cart.map((item) => ({
        sku: item.sku,
        quantity: item.quantity,
        addon_skus: flattenAddonSkus(item.addons),
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
      updateCheckoutStatus();
    }
  }

  document.addEventListener("DOMContentLoaded", init);
})();
