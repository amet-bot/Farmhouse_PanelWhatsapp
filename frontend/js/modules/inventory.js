/**
 * Farmhouse - Inventario y Abastecimiento (Cargamentos)
 * Primer módulo real de este sistema (ver plan): alta de cargamentos por sucursal + historial.
 * Merma y Gasto por sucursal se muestran como "Pronto" en esta misma página, no como pestañas
 * separadas todavía.
 */

document.addEventListener('DOMContentLoaded', async () => {
  const gate = document.getElementById('invGate');
  const main = document.getElementById('invMain');
  const btnThemeToggle = document.getElementById('btnThemeToggle');
  const themeIconSlot = document.getElementById('themeIconSlot');
  const themeLabel = document.querySelector('#btnThemeToggle .theme-label');
  const btnLogout = document.getElementById('btnLogout');
  const branchFixedBadge = document.getElementById('branchFixedBadge');
  const branchSelect = document.getElementById('branchSelect');
  const supplierInput = document.getElementById('supplierInput');
  const supplierSuggestions = document.getElementById('supplierSuggestions');
  const notesInput = document.getElementById('notesInput');
  const linesContainer = document.getElementById('shipmentLines');
  const btnAddLine = document.getElementById('btnAddLine');
  const btnSubmitShipment = document.getElementById('btnSubmitShipment');
  const shipmentError = document.getElementById('shipmentError');
  const shipmentFeedback = document.getElementById('shipmentFeedback');
  const lineTemplate = document.getElementById('shipmentLineTemplate');
  const colBranch = document.getElementById('colBranch');
  const shipmentsTable = document.getElementById('shipmentsTable');
  const shipmentsTableBody = document.getElementById('shipmentsTableBody');
  const shipmentsHistoryEmpty = document.getElementById('shipmentsHistoryEmpty');
  const shipmentsCount = document.getElementById('shipmentsCount');

  let fixedBranchId = null;
  let isGlobalScope = false;
  let selectedSupplierId = '';

  // ---- Tema ----
  function applyTheme(theme) {
    document.documentElement.setAttribute('data-theme', theme);
    localStorage.setItem('fh_theme', theme);
    if (themeIconSlot) themeIconSlot.innerHTML = `<i data-lucide="${theme === 'dark' ? 'sun' : 'moon'}"></i>`;
    if (themeLabel) themeLabel.textContent = theme === 'dark' ? 'Claro' : 'Oscuro';
    utils.renderIcons();
  }
  applyTheme(localStorage.getItem('fh_theme') || 'light');
  btnThemeToggle?.addEventListener('click', () => {
    const current = document.documentElement.getAttribute('data-theme');
    applyTheme(current === 'dark' ? 'light' : 'dark');
  });

  btnLogout?.addEventListener('click', async () => {
    await auth.logout();
    window.location.href = '/';
  });

  // Sesión expirada a mitad de uso: vuelve al hub, que tiene su propia pantalla de login.
  window.addEventListener('auth:unauthorized', () => {
    window.location.href = '/';
  });

  // ---- Mensajes ----
  function showError(msg) {
    shipmentFeedback.style.display = 'none';
    shipmentError.textContent = msg;
    shipmentError.style.display = 'block';
  }
  function showFeedback(msg) {
    shipmentError.style.display = 'none';
    shipmentFeedback.textContent = msg;
    shipmentFeedback.style.display = 'block';
  }
  function clearMessages() {
    shipmentError.style.display = 'none';
    shipmentFeedback.style.display = 'none';
  }

  // ---- Autocomplete de ítems (calcado del de direcciones en menu_app.js, sin AbortController:
  // api.js no expone `signal`, así que se usa un contador de petición por fila en su lugar) ----
  function createLineRow() {
    const frag = lineTemplate.content.cloneNode(true);
    const row = frag.querySelector('.inv-line-row');
    const itemInput = row.querySelector('.inv-item-input');
    const suggestBox = row.querySelector('.inv-item-suggestions');
    const unitLabel = row.querySelector('.inv-line-unit');
    const removeBtn = row.querySelector('.inv-line-remove');

    itemInput.dataset.itemId = '';
    itemInput._reqId = 0;

    function hideSuggestions() {
      suggestBox.hidden = true;
      suggestBox.innerHTML = '';
    }

    function selectItem(item) {
      itemInput.value = item.name;
      itemInput.dataset.itemId = String(item.id);
      unitLabel.textContent = item.unit ? `Unidad: ${item.unit}` : '';
      hideSuggestions();
    }

    function renderSuggestions(results, query) {
      const trimmed = query.trim();
      const exactMatch = results.some((r) => r.name.trim().toLowerCase() === trimmed.toLowerCase());
      let html = results.map((r, i) =>
        `<button type="button" class="inv-item-suggestion" data-idx="${i}">${utils.escapeHtml(r.name)} <small>(${utils.escapeHtml(r.unit)})</small></button>`
      ).join('');
      if (!exactMatch) {
        html += `<button type="button" class="inv-item-suggestion inv-item-suggestion-create" data-create="1">+ Crear "${utils.escapeHtml(trimmed)}"</button>`;
      }
      suggestBox.innerHTML = html;
      suggestBox.hidden = false;
      suggestBox.querySelectorAll('.inv-item-suggestion').forEach((btn) => {
        btn.addEventListener('click', async () => {
          if (btn.dataset.create) {
            const unit = window.prompt(`¿En qué unidad se mide "${trimmed}"? (kg, lb, unidad, caja...)`);
            if (!unit || !unit.trim()) return;
            try {
              const created = await api.post('/inventory/items', { name: trimmed, unit: unit.trim() });
              selectItem(created);
            } catch (err) {
              showError(err.message || 'No se pudo crear el ítem.');
            }
            return;
          }
          selectItem(results[Number(btn.dataset.idx)]);
        });
      });
    }

    async function fetchSuggestions(query) {
      if (!query || query.trim().length < 3) { hideSuggestions(); return; }
      const reqId = ++itemInput._reqId;
      try {
        const results = await api.get(`/inventory/items?q=${encodeURIComponent(query.trim())}`);
        if (reqId !== itemInput._reqId) return; // respuesta vieja, ya no aplica
        renderSuggestions(results, query);
      } catch (err) {
        if (reqId === itemInput._reqId) hideSuggestions();
      }
    }

    itemInput.addEventListener('input', () => {
      itemInput.dataset.itemId = '';
      unitLabel.textContent = '';
      clearTimeout(itemInput._debounce);
      const query = itemInput.value;
      itemInput._debounce = setTimeout(() => fetchSuggestions(query), 350);
    });
    itemInput.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') hideSuggestions();
    });

    removeBtn.addEventListener('click', () => {
      if (linesContainer.children.length > 1) {
        row.remove();
      } else {
        itemInput.value = '';
        itemInput.dataset.itemId = '';
        unitLabel.textContent = '';
        row.querySelector('.inv-line-qty').value = '';
        row.querySelector('.inv-line-cost').value = '';
      }
    });

    linesContainer.appendChild(row);
  }

  // Un solo listener delegado para cerrar sugerencias al hacer clic afuera (en vez de uno por fila).
  document.addEventListener('click', (e) => {
    document.querySelectorAll('.inv-item-suggestions').forEach((box) => {
      const container = box.closest('.inv-line-row') || box.closest('.inv-supplier-wrap');
      if (container && !container.contains(e.target)) {
        box.hidden = true;
        box.innerHTML = '';
      }
    });
  });

  btnAddLine?.addEventListener('click', () => createLineRow());

  // ---- Autocomplete de proveedor (calcado del de ítems, pero a nivel de página: solo hay un
  // proveedor por cargamento, no una fila por línea) ----
  function hideSupplierSuggestions() {
    supplierSuggestions.hidden = true;
    supplierSuggestions.innerHTML = '';
  }

  function selectSupplier(supplier) {
    supplierInput.value = supplier.name;
    selectedSupplierId = String(supplier.id);
    hideSupplierSuggestions();
  }

  function renderSupplierSuggestions(results, query) {
    const trimmed = query.trim();
    const exactMatch = results.some((r) => r.name.trim().toLowerCase() === trimmed.toLowerCase());
    let html = results.map((r, i) =>
      `<button type="button" class="inv-item-suggestion" data-idx="${i}">${utils.escapeHtml(r.name)}</button>`
    ).join('');
    if (!exactMatch) {
      html += `<button type="button" class="inv-item-suggestion inv-item-suggestion-create" data-create="1">+ Crear "${utils.escapeHtml(trimmed)}"</button>`;
    }
    supplierSuggestions.innerHTML = html;
    supplierSuggestions.hidden = false;
    supplierSuggestions.querySelectorAll('.inv-item-suggestion').forEach((btn) => {
      btn.addEventListener('click', async () => {
        if (btn.dataset.create) {
          try {
            const created = await api.post('/inventory/suppliers', { name: trimmed });
            selectSupplier(created);
          } catch (err) {
            showError(err.message || 'No se pudo crear el proveedor.');
          }
          return;
        }
        selectSupplier(results[Number(btn.dataset.idx)]);
      });
    });
  }

  let supplierReqId = 0;
  async function fetchSupplierSuggestions(query) {
    if (!query || query.trim().length < 3) { hideSupplierSuggestions(); return; }
    const reqId = ++supplierReqId;
    try {
      const results = await api.get(`/inventory/suppliers?q=${encodeURIComponent(query.trim())}`);
      if (reqId !== supplierReqId) return;
      renderSupplierSuggestions(results, query);
    } catch (err) {
      if (reqId === supplierReqId) hideSupplierSuggestions();
    }
  }

  let supplierDebounce;
  supplierInput?.addEventListener('input', () => {
    selectedSupplierId = '';
    clearTimeout(supplierDebounce);
    const query = supplierInput.value;
    supplierDebounce = setTimeout(() => fetchSupplierSuggestions(query), 350);
  });
  supplierInput?.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') hideSupplierSuggestions();
  });

  function resetForm() {
    supplierInput.value = '';
    selectedSupplierId = '';
    notesInput.value = '';
    linesContainer.innerHTML = '';
    createLineRow();
  }

  // ---- Contexto de sucursal ----
  async function resolveBranchContext(user) {
    if (user.branch_id) {
      fixedBranchId = user.branch_id;
      isGlobalScope = false;
      branchFixedBadge.hidden = false;
      branchFixedBadge.textContent = user.branch ? user.branch.name : `Sucursal #${user.branch_id}`;
      branchSelect.hidden = true;
    } else {
      isGlobalScope = true;
      branchFixedBadge.hidden = true;
      branchSelect.hidden = false;
      colBranch.hidden = false;
      try {
        const branches = await api.get('/branches/');
        branchSelect.innerHTML = branches.map((b) => `<option value="${b.id}">${utils.escapeHtml(b.name)}</option>`).join('');
      } catch (err) {
        showError('No se pudieron cargar las sucursales.');
      }
    }
  }

  function getSelectedBranchId() {
    if (fixedBranchId) return fixedBranchId;
    return branchSelect.value ? Number(branchSelect.value) : null;
  }

  // ---- Envío del formulario ----
  btnSubmitShipment?.addEventListener('click', async () => {
    clearMessages();
    const branchId = getSelectedBranchId();
    if (!branchId) { showError('Selecciona una sucursal.'); return; }

    const rows = Array.from(linesContainer.querySelectorAll('.inv-line-row'));
    const items = [];
    for (const row of rows) {
      const itemInput = row.querySelector('.inv-item-input');
      const qtyInput = row.querySelector('.inv-line-qty');
      const costInput = row.querySelector('.inv-line-cost');
      const itemId = itemInput.dataset.itemId;
      const qty = qtyInput.value;
      if (!itemId && !qty) continue; // fila vacía, se ignora
      if (!itemId) { showError('Elegí un ítem de la lista (o creá uno nuevo) en cada fila con cantidad.'); return; }
      if (!qty || Number(qty) <= 0) { showError('Cada ítem necesita una cantidad mayor a 0.'); return; }
      items.push({
        inventory_item_id: Number(itemId),
        quantity: qty,
        unit_cost: costInput.value ? costInput.value : null,
      });
    }
    if (!items.length) { showError('Agrega al menos un ítem.'); return; }

    const supplierText = supplierInput.value.trim();
    if (supplierText && !selectedSupplierId) {
      showError('Elegí un proveedor de la lista (o creá uno nuevo), o dejá el campo vacío.');
      return;
    }

    btnSubmitShipment.disabled = true;
    btnSubmitShipment.textContent = 'Registrando...';
    try {
      await api.post('/inventory/shipments', {
        branch_id: branchId,
        supplier_id: selectedSupplierId ? Number(selectedSupplierId) : null,
        notes: notesInput.value.trim() || null,
        items,
      });
      showFeedback('Cargamento registrado correctamente.');
      resetForm();
      await loadShipments();
    } catch (err) {
      showError(err.message || 'No se pudo registrar el cargamento.');
    } finally {
      btnSubmitShipment.disabled = false;
      btnSubmitShipment.textContent = 'Registrar cargamento';
    }
  });

  // ---- Historial ----
  async function loadShipments() {
    try {
      const shipments = await api.get('/inventory/shipments');
      renderShipments(shipments);
    } catch (err) {
      showError(err.message || 'No se pudo cargar el historial.');
    }
  }

  function renderShipments(shipments) {
    if (!shipments.length) {
      shipmentsHistoryEmpty.hidden = false;
      shipmentsTable.hidden = true;
      shipmentsCount.hidden = true;
      return;
    }
    shipmentsHistoryEmpty.hidden = true;
    shipmentsTable.hidden = false;
    shipmentsCount.hidden = false;
    shipmentsCount.textContent = shipments.length === 1 ? '1 cargamento' : `${shipments.length} cargamentos`;
    shipmentsTableBody.innerHTML = shipments.map((s) => {
      const itemsText = s.items.map((i) => `${i.item_name} (${Number(i.quantity)} ${i.unit})`).join(', ');
      const totalText = s.total_cost != null ? `$${Number(s.total_cost).toFixed(2)}` : '—';
      const branchCell = isGlobalScope ? `<td>${utils.escapeHtml(s.branch_name)}</td>` : '';
      return `<tr>
        <td>${utils.formatDateTime(s.received_at)}</td>
        ${branchCell}
        <td>${utils.escapeHtml(s.supplier_name || '—')}</td>
        <td>${utils.escapeHtml(itemsText)}</td>
        <td class="inv-total-cell">${totalText}</td>
        <td>${utils.escapeHtml(s.received_by_name)}</td>
      </tr>`;
    }).join('');
  }

  // ---- Arranque ----
  const existingUser = await auth.checkSession();
  if (!existingUser) {
    window.location.href = '/';
    return;
  }

  gate.hidden = true;
  main.hidden = false;
  document.getElementById('invAgentName').textContent = existingUser.name;
  document.getElementById('invAgentRole').textContent = `${existingUser.role.toUpperCase()}${existingUser.branch ? ' • ' + existingUser.branch.name : ''}`;
  document.getElementById('invAgentAvatar').textContent = utils.getInitials(existingUser.name);

  await resolveBranchContext(existingUser);
  createLineRow();
  await loadShipments();
  utils.renderIcons();
});
