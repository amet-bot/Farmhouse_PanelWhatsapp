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

  FarmhouseShell.initTheme();
  FarmhouseShell.initLogout({ redirectTo: '/' });

  function openModal(id) { $(id).hidden = false; }
  function closeModal(id) { $(id).hidden = true; }

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

  // ---- Solicitar insumos ----
  $('btnOpenRequest').addEventListener('click', () => {
    $('requestForm').reset();
    $('requestError').style.display = 'none';
    openModal('modalRequest');
  });

  $('requestForm').addEventListener('submit', async (e) => {
    e.preventDefault();
    const errorBox = $('requestError');
    errorBox.style.display = 'none';
    try {
      await api.post('/ops/requests', {
        branch_id: branchId,
        item_name: $('requestItemName').value.trim(),
        quantity_hint: $('requestQuantityHint').value.trim() || null,
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
      try {
        const items = await api.get(`/inventory/items?q=${encodeURIComponent(q)}`);
        const box = $('transferItemResults');
        if (!items.length) { box.hidden = true; return; }
        box.innerHTML = items.map((i) => `<div class="tablet-autocomplete-row" data-id="${i.id}" data-name="${esc(i.name)}">${esc(i.name)}</div>`).join('');
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

  $('transferForm').addEventListener('submit', async (e) => {
    e.preventDefault();
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

  $('incidentForm').addEventListener('submit', async (e) => {
    e.preventDefault();
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
    document.querySelectorAll('.tablet-tile').forEach((btn) => { btn.disabled = true; });
    utils.renderIcons();
    return;
  }

  const branchName = existingUser.branch ? existingUser.branch.name : `Sucursal #${branchId}`;
  $('tabletBranchBadge').textContent = branchName;
  $('tabletHeaderScope').textContent = `Sucursal fija: ${branchName}`;

  utils.renderIcons();
});
