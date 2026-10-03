/**
 * Farmhouse Link — Operación de Sucursal (Fase 4, quinto bloque)
 *
 * Vista de tablet: botones grandes, sucursal fija por usuario/dispositivo (sin selector), pensada
 * para tocar y listo. Recibir mercancía, contar y registrar merma reusan los modales que ya
 * existen en Inventario — esta pantalla solo linkea a /inventario?open=... — porque reconstruir
 * esos formularios acá hubiera sido duplicar lo que ya funciona. Solicitar insumos, transferir y
 * reportar incidencia son formularios nuevos y chicos, porque esas tres no tenían pantalla propia
 * todavía (solo backend, de este mismo bloque del plan).
 */
document.addEventListener('DOMContentLoaded', async () => {
  const $ = (id) => document.getElementById(id);
  const esc = (s) => utils.escapeHtml(s ?? '');

  let branchId = null;
  let itemSearchTimer = null;
  let itemSearchSeq = 0;

  FarmhouseShell.initTheme();
  FarmhouseShell.initLogout({ redirectTo: '/' });

  function openModal(id) { $(id).hidden = false; }
  function closeModal(id) { $(id).hidden = true; }

  window.addEventListener('auth:unauthorized', () => { window.location.href = '/'; });

  // Un doble toque en "Enviar" creaba la solicitud, el traslado o la incidencia dos veces: el
  // botón queda bloqueado hasta que responde el servidor.
  function guardSubmit(form, handler) {
    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      const btn = form.querySelector('[type="submit"]');
      if (btn && btn.disabled) return;
      if (btn) btn.disabled = true;
      try {
        await handler(e);
      } finally {
        if (btn) btn.disabled = false;
      }
    });
  }

  document.querySelectorAll('[data-close]').forEach((btn) => {
    btn.addEventListener('click', () => btn.closest('.tablet-modal-overlay').hidden = true);
  });
  document.querySelectorAll('.tablet-modal-overlay').forEach((overlay) => {
    overlay.addEventListener('click', (e) => { if (e.target === overlay) overlay.hidden = true; });
  });

  // ---- Los tres que reusan Inventario ----
  $('btnGoShipment').addEventListener('click', () => { window.location.href = '/inventario?open=shipment'; });
  $('btnGoCount').addEventListener('click', () => { window.location.href = '/inventario?open=count'; });
  $('btnGoWaste').addEventListener('click', () => { window.location.href = '/inventario?open=waste'; });
  $('btnGoPrep').addEventListener('click', () => { window.location.href = '/prep'; });
  $('btnGoConsumo').addEventListener('click', () => { window.location.href = '/consumo'; });

  // ---- Solicitar insumos ----
  // Ligada al catálogo cuando se elige de la lista: así la solicitud entra al pedido sugerido
  // de Abastecimiento con su cantidad. Si el insumo no está, se manda como texto libre.
  let requestPicked = null;   // { id, name, unit }
  function setRequestPicked(item) {
    requestPicked = item;
    $('requestItemId').value = item ? item.id : '';
    $('requestQtyCatalog').hidden = !item;
    $('requestQtyFree').hidden = !!item;
    $('requestQuantity').required = !!item;
    $('requestQtyUnit').textContent = item && item.unit ? `(en ${item.unit})` : '';
    $('requestItemHint').textContent = item ? 'Del catálogo: entra al pedido sugerido.' : 'Elige de la lista para que entre al pedido. Si no aparece, escríbelo igual.';
  }
  $('btnOpenRequest').addEventListener('click', () => {
    $('requestForm').reset();
    setRequestPicked(null);
    $('requestItemResults').hidden = true;
    $('requestError').style.display = 'none';
    openModal('modalRequest');
  });
  let requestSearchTimer = null;
  let requestSearchSeq = 0;
  $('requestItemName').addEventListener('input', () => {
    const q = $('requestItemName').value.trim();
    if (requestPicked && q !== requestPicked.name) setRequestPicked(null);
    clearTimeout(requestSearchTimer);
    if (q.length < 2) { $('requestItemResults').hidden = true; return; }
    requestSearchTimer = setTimeout(async () => {
      const seq = ++requestSearchSeq;
      try {
        const items = await api.get(`/inventory/items?q=${encodeURIComponent(q)}&limit=8`);
        if (seq !== requestSearchSeq) return;
        const box = $('requestItemResults');
        if (!items.length) { box.hidden = true; return; }
        box.innerHTML = items.map((i) => `<button type="button" class="tablet-autocomplete-row" data-id="${i.id}" data-name="${esc(i.name)}" data-unit="${esc(i.unit || '')}">${esc(i.name)} <small>${esc(i.unit || '')}</small></button>`).join('');
        box.hidden = false;
      } catch (e) { /* búsqueda silenciosa: se puede mandar como texto */ }
    }, 200);
  });
  $('requestItemResults').addEventListener('click', (e) => {
    const row = e.target.closest('.tablet-autocomplete-row');
    if (!row) return;
    $('requestItemName').value = row.dataset.name;
    $('requestItemResults').hidden = true;
    setRequestPicked({ id: Number(row.dataset.id), name: row.dataset.name, unit: row.dataset.unit });
    $('requestQuantity').focus();
  });

  guardSubmit($('requestForm'), async () => {
    const errorBox = $('requestError');
    errorBox.style.display = 'none';
    try {
      const qty = requestPicked ? Number($('requestQuantity').value) : null;
      await api.post('/ops/requests', {
        branch_id: branchId,
        item_name: $('requestItemName').value.trim(),
        inventory_item_id: requestPicked ? requestPicked.id : null,
        quantity: requestPicked && qty > 0 ? String(qty) : null,
        quantity_hint: requestPicked ? (qty > 0 ? `${qty} ${requestPicked.unit || ''}`.trim() : null) : ($('requestQuantityHint').value.trim() || null),
        notes: $('requestNotes').value.trim() || null,
      });
      closeModal('modalRequest');
      utils.showToast('Solicitud enviada.', 'success');
    } catch (err) {
      errorBox.textContent = err.message || 'No se pudo enviar la solicitud.';
      errorBox.style.display = 'block';
    }
  });

  // ---- Transferir ----
  $('btnOpenTransfer').addEventListener('click', async () => {
    $('transferForm').reset();
    $('transferError').style.display = 'none';
    $('transferItemId').value = '';
    $('transferItemResults').hidden = true;
    try {
      const branches = await api.get('/branches/');
      $('transferToBranch').innerHTML = branches
        .filter((b) => b.id !== branchId)
        .map((b) => `<option value="${b.id}">${esc(b.name)}</option>`)
        .join('');
    } catch (err) {
      utils.showToast('No se pudieron cargar las sucursales.', 'error');
    }
    openModal('modalTransfer');
  });

  $('transferItemSearch').addEventListener('input', () => {
    const q = $('transferItemSearch').value.trim();
    $('transferItemId').value = '';
    clearTimeout(itemSearchTimer);
    if (q.length < 2) { $('transferItemResults').hidden = true; return; }
    itemSearchTimer = setTimeout(async () => {
      const seq = ++itemSearchSeq;
      try {
        const items = await api.get(`/inventory/items?q=${encodeURIComponent(q)}`);
        // Una búsqueda anterior que responde tarde no pisa la lista de la búsqueda actual.
        if (seq !== itemSearchSeq) return;
        const box = $('transferItemResults');
        if (!items.length) { box.hidden = true; return; }
        box.innerHTML = items.map((i) => `<button type="button" class="tablet-autocomplete-row" data-id="${i.id}" data-name="${esc(i.name)}">${esc(i.name)}</button>`).join('');
        box.hidden = false;
        box.querySelectorAll('.tablet-autocomplete-row').forEach((row) => {
          row.addEventListener('click', () => {
            $('transferItemId').value = row.dataset.id;
            $('transferItemSearch').value = row.dataset.name;
            box.hidden = true;
          });
        });
      } catch (err) { /* búsqueda silenciosa: no bloquea el resto del formulario */ }
    }, 250);
  });

  guardSubmit($('transferForm'), async () => {
    const errorBox = $('transferError');
    errorBox.style.display = 'none';
    const itemId = $('transferItemId').value;
    if (!itemId) {
      errorBox.textContent = 'Elegí un insumo de la lista.';
      errorBox.style.display = 'block';
      return;
    }
    try {
      await api.post('/transfers/', {
        from_branch_id: branchId,
        to_branch_id: Number($('transferToBranch').value),
        items: [{ inventory_item_id: Number(itemId), quantity: $('transferQuantity').value }],
        notes: $('transferNotes').value.trim() || null,
      });
      closeModal('modalTransfer');
      utils.showToast('Traslado pedido.', 'success');
    } catch (err) {
      errorBox.textContent = err.message || 'No se pudo pedir el traslado.';
      errorBox.style.display = 'block';
    }
  });

  // ---- Reportar incidencia ----
  $('btnOpenIncident').addEventListener('click', () => {
    $('incidentForm').reset();
    $('incidentError').style.display = 'none';
    openModal('modalIncident');
  });

  guardSubmit($('incidentForm'), async () => {
    const errorBox = $('incidentError');
    errorBox.style.display = 'none';
    try {
      await api.post('/ops/incidents', {
        branch_id: branchId,
        title: $('incidentTitle').value.trim(),
        severity: $('incidentSeverity').value,
        description: $('incidentDescription').value.trim() || null,
      });
      closeModal('modalIncident');
      utils.showToast('Incidencia reportada.', 'success');
    } catch (err) {
      errorBox.textContent = err.message || 'No se pudo reportar la incidencia.';
      errorBox.style.display = 'block';
    }
  });

  // ==========================================================================
  // Arranque
  // ==========================================================================
  const existingUser = await auth.checkSession();
  if (!existingUser) {
    window.location.href = '/';
    return;
  }

  branchId = existingUser.branch_id;
  $('tabletGate').hidden = true;
  $('tabletMain').hidden = false;
  FarmhouseShell.fillUserHeader({ nameId: 'tabletAgentName', roleId: 'tabletAgentRole', avatarId: 'tabletAgentAvatar' }, existingUser);

  if (!branchId) {
    // Esta vista es para un dispositivo/usuario de UNA sucursal fija. Un rol global (admin,
    // supervisor sin sucursal) no tiene una sucursal que fijar acá — usa Inventario directo.
    $('tabletBranchBadge').textContent = 'Sin sucursal fija';
    $('tabletHeaderScope').textContent = 'Esta vista es para dispositivos de una sucursal. Usá Inventario para elegir sucursal.';
    // Solo se bloquean los formularios de acá, que necesitan una sucursal fija. Recibir,
    // contar, merma y Prep abren pantallas que ya dejan elegir sucursal a un usuario global —
    // antes se bloqueaban todos y Prep quedaba sin ninguna entrada para admins.
    ['btnOpenRequest', 'btnOpenTransfer', 'btnOpenIncident'].forEach((id) => { if ($(id)) $(id).disabled = true; });
    utils.renderIcons();
    return;
  }

  const branchName = existingUser.branch ? existingUser.branch.name : `Sucursal #${branchId}`;
  $('tabletBranchBadge').textContent = branchName;
  $('tabletHeaderScope').textContent = `Sucursal fija: ${branchName}`;

  utils.renderIcons();
});
