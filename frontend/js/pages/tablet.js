/**
 * Farmhouse Link — Operación de Sucursal (Fase 4, quinto bloque)
 *
 * Vista de tablet: botones grandes, sucursal fija por usuario/dispositivo (sin selector), pensada
 * para tocar y listo. Recibir mercancía y contar reusan los modales que ya existen en Inventario
 * — esta pantalla solo linkea a /inventario?open=... — porque reconstruir esos formularios aquí
 * hubiera sido duplicar lo que ya funciona. Solicitar insumos, enviar a otra sucursal y reportar
 * incidencia son formularios nuevos y chicos, porque esas tres no tenían pantalla propia
 * todavía (solo backend, de este mismo bloque del plan).
 */
document.addEventListener('DOMContentLoaded', async () => {
  const $ = (id) => document.getElementById(id);
  const esc = (s) => utils.escapeHtml(s ?? '');

  let branchId = null;
  let branchName = '';
  let itemSearchTimer = null;
  let itemSearchSeq = 0;

  FarmhouseShell.initTheme();
  FarmhouseShell.initLogout({ redirectTo: '/' });

  window.addEventListener('auth:unauthorized', () => { window.location.href = '/'; });

  // ==========================================================================
  // Confirmación dentro de la página: el confirm() del navegador sale chico, con el nombre del
  // sitio arriba y a veces en inglés; este usa los mismos botones grandes del resto.
  // ==========================================================================
  function askConfirm({ title, message, ok = 'Sí, continuar', cancel = 'Cancelar', danger = false }) {
    return new Promise((resolve) => {
      const wrap = document.createElement('div');
      wrap.className = 'modal-backdrop active tablet-ask';
      wrap.innerHTML = `
        <div class="modal-card modal-sm" role="alertdialog" aria-modal="true" aria-labelledby="tabletAskTitle" aria-describedby="tabletAskMsg">
          <div class="modal-header"><h3 id="tabletAskTitle">${esc(title)}</h3></div>
          <div class="modal-body"><p class="tablet-ask-msg" id="tabletAskMsg">${esc(message)}</p></div>
          <div class="modal-footer">
            <button type="button" class="inv-secondary-btn" data-ans="0">${esc(cancel)}</button>
            <button type="button" class="${danger ? 'inv-danger-btn' : 'inv-primary-btn'}" data-ans="1">${esc(ok)}</button>
          </div>
        </div>`;
      const done = (answer) => {
        document.removeEventListener('keydown', onKey, true);
        wrap.remove();
        resolve(answer);
      };
      const onKey = (e) => { if (e.key === 'Escape') { e.stopPropagation(); done(false); } };
      wrap.addEventListener('click', (e) => {
        if (e.target === wrap) { done(false); return; }
        const b = e.target.closest('[data-ans]');
        if (b) done(b.dataset.ans === '1');
      });
      document.addEventListener('keydown', onKey, true);
      document.body.appendChild(wrap);
      wrap.querySelector('[data-ans="0"]').focus();
    });
  }

  // ==========================================================================
  // Modales: abrir, cerrar y no perder lo escrito por un toque fuera de la ventana
  // ==========================================================================
  const dirty = new Set();   // ids de los modales con algo escrito sin enviar

  function openModal(id) {
    dirty.delete(id);
    $(id).hidden = false;
  }
  function closeModal(id) {
    dirty.delete(id);
    $(id).hidden = true;
  }
  // En la tablet es fácil tocar fuera de la ventana sin querer: si hay algo escrito, se pregunta.
  async function tryClose(id) {
    if (dirty.has(id)) {
      const ok = await askConfirm({
        title: '¿Cerrar sin enviar?',
        message: 'Se pierde lo que escribiste en este formulario.',
        ok: 'Sí, cerrar', cancel: 'Seguir escribiendo', danger: true,
      });
      if (!ok) return;
    }
    closeModal(id);
  }

  document.querySelectorAll('.tablet-modal-overlay').forEach((overlay) => {
    overlay.addEventListener('click', (e) => { if (e.target === overlay) tryClose(overlay.id); });
    overlay.querySelectorAll('[data-close]').forEach((btn) => btn.addEventListener('click', () => tryClose(overlay.id)));
    const form = overlay.querySelector('form');
    if (form) form.addEventListener('input', () => dirty.add(overlay.id));
  });
  document.addEventListener('keydown', (e) => {
    if (e.key !== 'Escape' || document.querySelector('.tablet-ask')) return;
    const open = document.querySelector('.tablet-modal-overlay:not([hidden])');
    if (open) tryClose(open.id);
  });
  window.addEventListener('beforeunload', (e) => {
    const open = [...document.querySelectorAll('.tablet-modal-overlay:not([hidden])')].some((o) => dirty.has(o.id));
    if (open) { e.preventDefault(); e.returnValue = ''; }
  });

  // Un toque fuera de la lista de resultados la cierra.
  document.addEventListener('click', (e) => {
    document.querySelectorAll('.tablet-autocomplete:not([hidden])').forEach((box) => {
      if (!box.parentElement.contains(e.target)) box.hidden = true;
    });
  });

  // Un doble toque en "Enviar" creaba la solicitud, el traslado o la incidencia dos veces: el
  // botón queda bloqueado (y dice "Enviando…") hasta que responde el servidor.
  function guardSubmit(form, handler) {
    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      const btn = form.querySelector('[type="submit"]');
      if (btn && btn.disabled) return;
      const label = btn ? btn.textContent : '';
      if (btn) { btn.disabled = true; if (btn.dataset.busy) btn.textContent = btn.dataset.busy; }
      try {
        await handler(e);
      } finally {
        if (btn) { btn.disabled = false; btn.textContent = label; }
      }
    });
  }

  function showFormError(boxId, message) {
    const box = $(boxId);
    box.textContent = message;
    box.style.display = 'block';
    box.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
  }

  // Fila de "no se encontró" dentro de la lista de resultados (antes la lista solo desaparecía
  // y no se sabía si estaba buscando, si falló o si no existía).
  function showNoResults(box, text) {
    box.innerHTML = `<div class="tablet-autocomplete-empty">${text}</div>`;
    box.hidden = false;
  }

  // ---- Las que abren otra pantalla ----
  // from=operacion: Inventario vuelve aquí al guardar o cancelar, y su flecha apunta aquí.
  $('btnGoShipment').addEventListener('click', () => { window.location.href = '/inventario?open=shipment&from=operacion'; });
  $('btnGoCount').addEventListener('click', () => { window.location.href = '/inventario?open=count&from=operacion'; });
  $('btnGoWaste').addEventListener('click', () => { window.location.href = '/merma'; });   // merma rápida: pantalla aparte
  $('btnGoPrep').addEventListener('click', () => { window.location.href = '/prep'; });
  $('btnGoConsumo').addEventListener('click', () => { window.location.href = '/consumo'; });

  // ==========================================================================
  // Solicitar insumos
  // ==========================================================================
  // Ligada al catálogo cuando se elige de la lista: así la solicitud entra al pedido sugerido
  // de Abastecimiento con su cantidad. Si el insumo no está, se manda como texto libre.
  let requestPicked = null;   // { id, name, unit }
  function setRequestPicked(item) {
    requestPicked = item;
    $('requestItemId').value = item ? item.id : '';
    $('requestQtyCatalog').hidden = !item;
    $('requestQtyFree').hidden = !!item;
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
        if (!items.length) { showNoResults(box, `No se encontró «${esc(q)}» en el catálogo. Puedes enviarlo escrito así.`); return; }
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
    const name = $('requestItemName').value.trim();
    if (!name) { showFormError('requestError', 'Escribe qué hace falta.'); $('requestItemName').focus(); return; }
    const qty = requestPicked ? Number($('requestQuantity').value) : null;
    if (requestPicked && !(qty > 0)) { showFormError('requestError', 'Escribe cuánto hace falta (un número mayor que 0).'); $('requestQuantity').focus(); return; }
    try {
      await api.post('/ops/requests', {
        branch_id: branchId,
        item_name: name,
        inventory_item_id: requestPicked ? requestPicked.id : null,
        quantity: requestPicked && qty > 0 ? String(qty) : null,
        quantity_hint: requestPicked ? (qty > 0 ? `${qty} ${requestPicked.unit || ''}`.trim() : null) : ($('requestQuantityHint').value.trim() || null),
        notes: $('requestNotes').value.trim() || null,
      });
      closeModal('modalRequest');
      utils.showToast(`Solicitud enviada: ${name}.`, 'success');
    } catch (err) {
      showFormError('requestError', err.message || 'No se pudo enviar la solicitud. Prueba otra vez.');
    }
  });

  // ==========================================================================
  // Enviar a otra sucursal (traslado). El origen es siempre la sucursal de esta tablet.
  // ==========================================================================
  let transferPicked = null;   // { id, name, unit }
  function setTransferPicked(item) {
    transferPicked = item;
    $('transferItemId').value = item ? item.id : '';
    const unit = item && item.unit ? item.unit : '';
    $('transferQtyUnit').textContent = unit ? `(en ${unit})` : '';
    $('transferQtyUnitTag').textContent = unit;
    $('transferQtyUnitTag').hidden = !unit;
    updateTransferSummary();
  }
  // Resumen en palabras antes de tocar "Enviar": qué, cuánto, de dónde y a dónde.
  function updateTransferSummary() {
    const box = $('transferSummary');
    const sel = $('transferToBranch');
    const to = sel.value ? sel.options[sel.selectedIndex].textContent : '';
    const qty = Number($('transferQuantity').value);
    if (!transferPicked || !(qty > 0) || !to) { box.hidden = true; return; }
    box.innerHTML = `Vas a enviar <strong>${esc(String(qty))} ${esc(transferPicked.unit || '')} de ${esc(transferPicked.name)}</strong> de ${esc(branchName)} a <strong>${esc(to)}</strong>.`;
    box.hidden = false;
  }

  $('btnOpenTransfer').addEventListener('click', async () => {
    const tile = $('btnOpenTransfer');
    if (tile.disabled) return;
    // Si las sucursales no cargan, no se abre un formulario con la lista de destino vacía.
    tile.disabled = true;
    let branches;
    try {
      branches = (await api.get('/branches/')).filter((b) => b.id !== branchId && b.active !== false && b.code !== 'CAT');
    } catch (err) {
      utils.showToast(`No se pudieron cargar las sucursales. ${err.message || ''} Prueba otra vez.`.trim(), 'error');
      return;
    } finally {
      tile.disabled = false;
    }
    if (!branches.length) {
      utils.showToast('No hay otra sucursal a la que enviar.', 'info');
      return;
    }
    $('transferForm').reset();
    $('transferError').style.display = 'none';
    $('transferItemResults').hidden = true;
    $('transferToBranch').innerHTML = (branches.length > 1 ? '<option value="">Elige la sucursal…</option>' : '')
      + branches.map((b) => `<option value="${b.id}">${esc(b.name)}</option>`).join('');
    setTransferPicked(null);
    openModal('modalTransfer');
  });

  $('transferToBranch').addEventListener('change', updateTransferSummary);
  $('transferQuantity').addEventListener('input', updateTransferSummary);

  $('transferItemSearch').addEventListener('input', () => {
    const q = $('transferItemSearch').value.trim();
    if (transferPicked && q !== transferPicked.name) setTransferPicked(null);
    clearTimeout(itemSearchTimer);
    if (q.length < 2) { $('transferItemResults').hidden = true; return; }
    itemSearchTimer = setTimeout(async () => {
      const seq = ++itemSearchSeq;
      const box = $('transferItemResults');
      try {
        const items = await api.get(`/inventory/items?q=${encodeURIComponent(q)}&limit=12`);
        // Una búsqueda anterior que responde tarde no pisa la lista de la búsqueda actual.
        if (seq !== itemSearchSeq) return;
        if (!items.length) { showNoResults(box, `No se encontró «${esc(q)}». Prueba con otra palabra.`); return; }
        box.innerHTML = items.map((i) => `<button type="button" class="tablet-autocomplete-row" data-id="${i.id}" data-name="${esc(i.name)}" data-unit="${esc(i.unit || '')}">${esc(i.name)} <small>${esc(i.unit || '')}</small></button>`).join('');
        box.hidden = false;
      } catch (err) {
        if (seq === itemSearchSeq) showNoResults(box, 'No se pudo buscar. Revisa la conexión y vuelve a escribir.');
      }
    }, 250);
  });
  $('transferItemResults').addEventListener('click', (e) => {
    const row = e.target.closest('.tablet-autocomplete-row');
    if (!row) return;
    $('transferItemSearch').value = row.dataset.name;
    $('transferItemResults').hidden = true;
    setTransferPicked({ id: Number(row.dataset.id), name: row.dataset.name, unit: row.dataset.unit });
    $('transferQuantity').focus();
  });

  guardSubmit($('transferForm'), async () => {
    $('transferError').style.display = 'none';
    const toId = Number($('transferToBranch').value);
    const qty = Number($('transferQuantity').value);
    if (!toId) { showFormError('transferError', 'Elige la sucursal a la que lo envías.'); $('transferToBranch').focus(); return; }
    if (!transferPicked) { showFormError('transferError', 'Elige un insumo de la lista.'); $('transferItemSearch').focus(); return; }
    if (!(qty > 0)) { showFormError('transferError', 'Escribe cuánto envías (un número mayor que 0).'); $('transferQuantity').focus(); return; }
    try {
      await api.post('/transfers/', {
        from_branch_id: branchId,
        to_branch_id: toId,
        items: [{ inventory_item_id: transferPicked.id, quantity: String(qty) }],
        notes: $('transferNotes').value.trim() || null,
      });
      const to = $('transferToBranch').options[$('transferToBranch').selectedIndex].textContent;
      closeModal('modalTransfer');
      utils.showToast(`Traslado a ${to} registrado. Queda pendiente hasta que se despache.`, 'success');
    } catch (err) {
      showFormError('transferError', err.message || 'No se pudo registrar el traslado. Prueba otra vez.');
    }
  });

  // ==========================================================================
  // Reportar incidencia
  // ==========================================================================
  $('btnOpenIncident').addEventListener('click', () => {
    $('incidentForm').reset();
    $('incidentError').style.display = 'none';
    openModal('modalIncident');
  });

  guardSubmit($('incidentForm'), async () => {
    $('incidentError').style.display = 'none';
    const title = $('incidentTitle').value.trim();
    if (!title) { showFormError('incidentError', 'Escribe qué pasó.'); $('incidentTitle').focus(); return; }
    const sev = $('incidentForm').querySelector('input[name="incidentSeverity"]:checked');
    try {
      await api.post('/ops/incidents', {
        branch_id: branchId,
        title,
        severity: sev ? sev.value : 'media',
        description: $('incidentDescription').value.trim() || null,
      });
      closeModal('modalIncident');
      utils.showToast('Incidencia reportada. Ya les llegó el aviso a los encargados.', 'success');
    } catch (err) {
      showFormError('incidentError', err.message || 'No se pudo reportar la incidencia. Prueba otra vez.');
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
    // supervisor sin sucursal) no tiene una sucursal que fijar aquí — usa Inventario directo.
    $('tabletBranchBadge').textContent = 'Sin sucursal fija';
    $('tabletHeaderScope').textContent = 'Esta vista es para dispositivos de una sucursal.';
    // Solo se bloquean los formularios de aquí, que necesitan una sucursal fija. Recibir,
    // contar, merma y Preparación abren pantallas que ya dejan elegir sucursal a un usuario
    // global — antes se bloqueaban todos y Preparación quedaba sin ninguna entrada para admins.
    ['btnOpenRequest', 'btnOpenTransfer', 'btnOpenIncident'].forEach((id) => { if ($(id)) $(id).disabled = true; });
    // Un botón apagado sin explicación parece roto: se dice por qué, a la vista.
    $('tabletScopeNote').textContent = 'Solicitar insumos, Enviar a otra sucursal y Reportar incidencia están apagados porque tu usuario no tiene una sucursal fija. Los demás botones te dejan elegir la sucursal adentro.';
    $('tabletScopeNote').hidden = false;
    utils.renderIcons();
    return;
  }

  branchName = existingUser.branch ? existingUser.branch.name : `Sucursal #${branchId}`;
  $('tabletBranchBadge').textContent = branchName;
  $('tabletHeaderScope').textContent = `Sucursal fija: ${branchName}`;
  $('transferIntro').textContent = `Sale de tu sucursal (${branchName}) hacia la que elijas. Se descuenta de tu inventario cuando se despacha.`;

  utils.renderIcons();
});
