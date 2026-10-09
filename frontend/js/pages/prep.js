/**
 * Farmhouse Link — Preparación del día (antes "Prep de Bowls")
 *
 * Calcado de la hoja de papel que ya usan en cocina: una lista por sucursal (insumos agrupados
 * por sección, cada uno con su cantidad ideal) que se llena varias veces al día, una vez por
 * revisión. Las revisiones no son fijas (10am/3pm/8pm en el papel, pero "Congelador Grande"/
 * "Congelador chico" para los kits de smoothie) — cada lista define las suyas.
 * En el backend siguen llamándose plantilla, checkpoint y par: aquí solo cambian los textos.
 *
 * Editar la lista (agregar/sacar insumos, cambiar cantidades ideales) es de supervisor/admin; un
 * agente solo anota las cantidades del día. El "pegar varias líneas" existe porque transcribir
 * 80+ insumos a mano, fila por fila, es exactamente el tipo de fricción que hace que nadie use
 * la pantalla nueva.
 */
document.addEventListener('DOMContentLoaded', async () => {
  const $ = (id) => document.getElementById(id);
  const esc = (s) => utils.escapeHtml(s ?? '');

  let user = null;
  let branchId = null;
  let branches = [];
  let canEdit = false;
  let template = null;       // detalle completo (con items) de la lista activa
  let activeCheckpoint = null;
  let existingEntries = {};  // template_item_id -> {on_hand, note} de la revisión activa, si ya se había llenado hoy
  // Lo escrito y no guardado: avisa antes de perderlo (cambiar de revisión, de sucursal o salir).
  let checkDirty = false;
  let templateDirty = false;
  let leaving = false;

  FarmhouseShell.initTheme();
  FarmhouseShell.initLogout({ redirectTo: '/' });
  // Sesión vencida: volver al login en vez de quedarse en una página que solo tira errores.
  window.addEventListener('auth:unauthorized', () => { window.location.href = '/'; });

  // ==========================================================================
  // Confirmación dentro de la página (el confirm() del navegador sale chico y con el nombre del
  // sitio arriba; este usa los mismos botones grandes del resto).
  // ==========================================================================
  function askConfirm({ title, message, ok = 'Sí, continuar', cancel = 'Cancelar', danger = false }) {
    return new Promise((resolve) => {
      const wrap = document.createElement('div');
      wrap.className = 'modal-backdrop active prep-ask';
      wrap.innerHTML = `
        <div class="modal-card modal-sm" role="alertdialog" aria-modal="true" aria-labelledby="prepAskTitle" aria-describedby="prepAskMsg">
          <div class="modal-header"><h3 id="prepAskTitle">${esc(title)}</h3></div>
          <div class="modal-body"><p class="prep-ask-msg" id="prepAskMsg">${esc(message)}</p></div>
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

  const isDirty = () => checkDirty || templateDirty;
  function dirtyMessage() {
    if (checkDirty && templateDirty) return 'Tienes cantidades y cambios en la lista sin guardar.';
    if (templateDirty) return 'Tienes cambios en la lista sin guardar.';
    return `Tienes cantidades sin guardar${activeCheckpoint ? ` de la revisión ${activeCheckpoint}` : ''}.`;
  }

  // Deshabilita el botón mientras corre la acción (y dice qué hace): un doble toque ya no crea
  // dos listas ni manda las cantidades dos veces.
  async function withButtonBusy(button, action, busyText = 'Guardando…') {
    if (button.disabled) return;
    const label = button.innerHTML;
    button.disabled = true;
    if (busyText) button.textContent = busyText;
    try {
      await action();
    } finally {
      button.disabled = false;
      button.innerHTML = label;
      utils.renderIcons();
    }
  }

  // ==========================================================================
  // Carga de la lista
  // ==========================================================================
  function showEmpty(message, { create = false, retry = false } = {}) {
    $('prepWithTemplate').hidden = true;
    $('prepEmptyMessage').textContent = message;
    $('btnCreateTemplate').hidden = !create;
    $('btnRetryLoad').hidden = !retry;
    $('prepEmptyState').hidden = false;
  }

  async function loadTemplateForBranch() {
    $('prepEmptyState').hidden = true;
    $('prepWithTemplate').hidden = true;
    checkDirty = false;
    templateDirty = false;
    try {
      const list = await api.get(`/prep/templates?branch_id=${branchId}`);
      if (!list.length) {
        template = null;
        showEmpty(canEdit
          ? 'Todavía no hay una lista de preparación para esta sucursal. Créala y agrega lo que se prepara.'
          : 'Todavía no hay una lista de preparación para esta sucursal. Pide a un encargado que la cree.', { create: canEdit });
        return;
      }
      template = await api.get(`/prep/templates/${list[0].id}`);
      $('prepWithTemplate').hidden = false;
      $('tabPlantilla').hidden = !canEdit;
      renderCheckpointChips();
      renderItemSections();
      renderTemplateEditor();
    } catch (err) {
      // Antes solo salía un aviso y la pantalla quedaba en blanco, sin forma de reintentar.
      showEmpty(`No se pudo cargar la lista de preparación. ${err.message || 'Revisa la conexión.'}`, { retry: true });
    }
  }
  $('btnRetryLoad').addEventListener('click', () => withButtonBusy($('btnRetryLoad'), loadTemplateForBranch, 'Cargando…'));

  $('btnCreateTemplate').addEventListener('click', () => withButtonBusy($('btnCreateTemplate'), async () => {
    try {
      const created = await api.post('/prep/templates', {
        branch_id: branchId, name: 'Bowls', checkpoints: ['10am', '3pm', '8pm'], items: [],
      });
      template = created;
      utils.showToast('Lista creada. Ahora agrega los insumos en «Editar la lista».', 'success');
      $('prepEmptyState').hidden = true;
      $('prepWithTemplate').hidden = false;
      $('tabPlantilla').hidden = !canEdit;
      renderCheckpointChips();
      renderItemSections();
      renderTemplateEditor();
      setTab('plantilla');
    } catch (err) {
      utils.showToast(`No se pudo crear la lista. ${err.message || 'Prueba otra vez.'}`, 'error');
    }
  }, 'Creando…'));

  // ==========================================================================
  // Pestañas
  // ==========================================================================
  function setTab(tab) {
    document.querySelectorAll('.prep-tab').forEach((b) => b.classList.toggle('active', b.dataset.tab === tab));
    $('viewLlenar').hidden = tab !== 'llenar';
    $('viewPlantilla').hidden = tab !== 'plantilla';
  }
  document.querySelectorAll('.prep-tab').forEach((b) => b.addEventListener('click', () => setTab(b.dataset.tab)));

  // ==========================================================================
  // Anotar cantidades
  // ==========================================================================
  function renderCheckpointChips() {
    const row = $('checkpointRow');
    row.innerHTML = template.checkpoints.map((cp) => `
      <button type="button" class="prep-chip${cp === activeCheckpoint ? ' active' : ''}" data-checkpoint="${esc(cp)}">${esc(cp)}</button>
    `).join('');
    row.querySelectorAll('.prep-chip').forEach((chip) => {
      chip.addEventListener('click', async () => {
        const cp = chip.dataset.checkpoint;
        if (cp === activeCheckpoint) return;
        if (checkDirty) {
          const ok = await askConfirm({
            title: `¿Cambiar a ${cp}?`,
            message: `${dirtyMessage()} Si cambias de revisión se pierden.`,
            ok: 'Cambiar sin guardar', cancel: 'Quedarme', danger: true,
          });
          if (!ok) return;
        }
        selectCheckpoint(cp);
      });
    });
    if (!activeCheckpoint && template.checkpoints.length) selectCheckpoint(template.checkpoints[0]);
    updateSubmitLabel();
  }

  function updateSubmitLabel() {
    $('btnSubmitCheck').textContent = activeCheckpoint ? `Guardar revisión de ${activeCheckpoint}` : 'Guardar';
  }

  async function selectCheckpoint(checkpoint) {
    activeCheckpoint = checkpoint;
    existingEntries = {};
    checkDirty = false;
    updateSubmitLabel();
    document.querySelectorAll('.prep-chip').forEach((c) => c.classList.toggle('active', c.dataset.checkpoint === checkpoint));
    const templateId = template.id;
    const entries = {};
    try {
      const checks = await api.get(`/prep/templates/${templateId}/checks`);
      const existing = checks.find((c) => c.checkpoint === checkpoint);
      if (existing) {
        existing.entries.forEach((e) => { entries[e.template_item_id] = e; });
      }
    } catch (err) { /* si falla, se llena en blanco — no bloquea la lista */ }
    // Tocar 10am y enseguida 3pm: la respuesta de 10am llegaba después y pintaba (y guardaba)
    // sus cantidades como si fueran las de 3pm. Si el usuario ya eligió otro, se descarta.
    if (checkpoint !== activeCheckpoint || !template || template.id !== templateId) return;
    existingEntries = entries;
    renderItemSections();
  }

  function renderItemSections() {
    const container = $('prepSections');
    if (!template.items.length) {
      container.innerHTML = `<p class="prep-empty-hint">Esta lista todavía no tiene insumos.${canEdit ? ' Agrégalos en «Editar la lista».' : ''}</p>`;
      return;
    }
    const bySection = new Map();
    template.items.forEach((item) => {
      if (!bySection.has(item.section)) bySection.set(item.section, []);
      bySection.get(item.section).push(item);
    });

    container.innerHTML = [...bySection.entries()].map(([section, items]) => `
      <div class="prep-section">
        <p class="prep-section-title">${esc(section)}</p>
        ${items.map((item) => {
          const existing = existingEntries[item.id];
          const value = existing ? existing.on_hand : '';
          const ideal = item.par_target != null && item.par_target !== ''
            ? `Ideal: ${item.par_target}${item.unit_label ? ' ' + item.unit_label : ''}`
            : (item.unit_label || '');
          return `
            <div class="prep-item-row" data-item-id="${item.id}">
              <div class="prep-item-info">
                <strong>${esc(item.name)}</strong>
                ${ideal ? `<small>${esc(ideal)}</small>` : ''}
                ${item.notes ? `<small class="prep-item-note">${esc(item.notes)}</small>` : ''}
              </div>
              <input type="number" class="prep-item-input" min="0" step="0.01" inputmode="decimal" enterkeyhint="next" value="${esc(value)}" placeholder="0" aria-label="Cuánto hay de ${esc(item.name)}">
            </div>
          `;
        }).join('')}
      </div>
    `).join('');
  }

  $('prepSections').addEventListener('input', (e) => { if (e.target.matches('.prep-item-input')) checkDirty = true; });
  // Enter baja al siguiente renglón: se llena de arriba a abajo sin tocar la pantalla.
  $('prepSections').addEventListener('keydown', (e) => {
    if (e.key !== 'Enter' || !e.target.matches('.prep-item-input')) return;
    e.preventDefault();
    const all = [...$('prepSections').querySelectorAll('.prep-item-input')];
    const next = all[all.indexOf(e.target) + 1];
    if (next) { next.focus(); next.select(); } else e.target.blur();
  });

  $('btnSubmitCheck').addEventListener('click', () => withButtonBusy($('btnSubmitCheck'), async () => {
    if (!template || !activeCheckpoint) return;
    const entries = [];
    let negativo = false;
    document.querySelectorAll('#prepSections .prep-item-row').forEach((row) => {
      const value = row.querySelector('.prep-item-input').value.trim();
      if (value === '') return;
      if (Number.isNaN(Number(value)) || Number(value) < 0) negativo = true;
      entries.push({ template_item_id: Number(row.dataset.itemId), on_hand: value });
    });
    if (negativo) {
      utils.showToast('Hay una cantidad que no es válida: usa números de 0 en adelante.', 'error');
      return;
    }
    if (!entries.length) {
      utils.showToast('Escribe al menos una cantidad antes de guardar.', 'error');
      return;
    }
    try {
      await api.post(`/prep/templates/${template.id}/checks`, { checkpoint: activeCheckpoint, entries });
      checkDirty = false;
      utils.showToast(`Revisión de ${activeCheckpoint} guardada (${entries.length} insumo${entries.length === 1 ? '' : 's'}).`, 'success');
    } catch (err) {
      utils.showToast(`No se guardó la revisión. ${err.message || 'Prueba otra vez.'}`, 'error');
    }
  }));

  // ==========================================================================
  // Editar la lista (supervisor/admin)
  // ==========================================================================
  let editCheckpoints = [];
  let editItems = [];

  function renderTemplateEditor() {
    editCheckpoints = [...template.checkpoints];
    editItems = template.items.map((i) => ({ ...i }));
    templateDirty = false;
    setTemplateError('');
    renderCheckpointEditRow();
    renderItemEditRows();
  }

  function setTemplateError(msg) {
    $('templateError').textContent = msg;
    $('templateError').hidden = !msg;
  }

  function renderCheckpointEditRow() {
    $('checkpointEditRow').innerHTML = editCheckpoints.map((cp, idx) => `
      <span class="prep-checkpoint-edit-item">
        <input type="text" value="${esc(cp)}" data-idx="${idx}" class="prep-checkpoint-input" maxlength="60" enterkeyhint="next" aria-label="Nombre de la revisión ${idx + 1}">
        <button type="button" data-remove-cp="${idx}" aria-label="Quitar la revisión ${esc(cp)}"><i data-lucide="x"></i></button>
      </span>
    `).join('');
    utils.renderIcons();
    $('checkpointEditRow').querySelectorAll('.prep-checkpoint-input').forEach((input) => {
      input.addEventListener('input', () => { editCheckpoints[Number(input.dataset.idx)] = input.value; templateDirty = true; });
    });
    $('checkpointEditRow').querySelectorAll('[data-remove-cp]').forEach((btn) => {
      btn.addEventListener('click', () => {
        editCheckpoints.splice(Number(btn.dataset.removeCp), 1);
        templateDirty = true;
        renderCheckpointEditRow();
      });
    });
  }
  $('btnAddCheckpoint').addEventListener('click', () => {
    editCheckpoints.push('');
    templateDirty = true;
    renderCheckpointEditRow();
    const inputs = $('checkpointEditRow').querySelectorAll('.prep-checkpoint-input');
    const last = inputs[inputs.length - 1];
    if (last) { last.placeholder = 'Ej: 12pm'; last.focus(); }
  });

  // Cada celda lleva su nombre (data-label): en la tablet parada y en el celular la tabla se
  // vuelve una tarjeta por insumo y el encabezado de columnas no se ve.
  function renderItemEditRows() {
    $('itemEditRows').innerHTML = editItems.map((item, idx) => `
      <tr data-idx="${idx}">
        <td data-label="Sección"><input type="text" class="ei-section" value="${esc(item.section)}" maxlength="80" enterkeyhint="next" aria-label="Sección" placeholder="Ej: Base"></td>
        <td data-label="Nombre"><input type="text" class="ei-name" value="${esc(item.name)}" maxlength="120" enterkeyhint="next" aria-label="Nombre" placeholder="Ej: Kale picado"></td>
        <td data-label="Unidad"><input type="text" class="ei-unit" value="${esc(item.unit_label || '')}" maxlength="40" enterkeyhint="next" aria-label="Unidad" placeholder="Ej: cambro L"></td>
        <td data-label="Cantidad ideal"><input type="number" step="0.01" min="0" inputmode="decimal" enterkeyhint="next" class="ei-par" value="${esc(item.par_target ?? '')}" aria-label="Cantidad ideal" placeholder="Ej: 3"></td>
        <td data-label="Notas"><input type="text" class="ei-notes" value="${esc(item.notes || '')}" maxlength="200" enterkeyhint="next" aria-label="Notas (opcional)" placeholder="Opcional"></td>
        <td class="prep-del-cell"><button type="button" class="prep-row-delete" data-remove-item="${idx}" aria-label="Quitar ${esc(item.name || 'este insumo')}"><i data-lucide="trash-2"></i><span>Quitar</span></button></td>
      </tr>
    `).join('');
    utils.renderIcons();
    $('itemEditRows').querySelectorAll('tr').forEach((row) => {
      const idx = Number(row.dataset.idx);
      const bind = (cls, field) => row.querySelector(cls).addEventListener('input', (e) => {
        editItems[idx][field] = e.target.value;
        e.target.classList.remove('is-invalid');
        templateDirty = true;
      });
      bind('.ei-section', 'section');
      bind('.ei-name', 'name');
      bind('.ei-unit', 'unit_label');
      bind('.ei-par', 'par_target');
      bind('.ei-notes', 'notes');
    });
    $('itemEditRows').querySelectorAll('[data-remove-item]').forEach((btn) => {
      btn.addEventListener('click', () => {
        editItems.splice(Number(btn.dataset.removeItem), 1);
        templateDirty = true;
        renderItemEditRows();
      });
    });
  }

  $('btnAddItemRow').addEventListener('click', () => {
    // La sección del último renglón se repite: casi siempre se cargan varios seguidos de la misma.
    const prev = editItems[editItems.length - 1];
    editItems.push({ section: prev ? prev.section : '', name: '', unit_label: '', par_target: null, notes: '' });
    templateDirty = true;
    renderItemEditRows();
    const rows = $('itemEditRows').querySelectorAll('tr');
    const last = rows[rows.length - 1];
    if (last) last.querySelector(prev && prev.section ? '.ei-name' : '.ei-section').focus();
  });

  $('btnBulkPaste').addEventListener('click', () => { $('bulkPasteBox').hidden = !$('bulkPasteBox').hidden; });

  $('btnApplyBulkPaste').addEventListener('click', () => {
    const raw = $('bulkPasteInput').value.trim();
    if (!raw) { utils.showToast('Pega al menos una línea.', 'error'); return; }
    let added = 0;
    let skipped = 0;
    raw.split('\n').forEach((line) => {
      if (!line.trim()) return;
      const parts = line.split('|').map((p) => p.trim());
      if (parts.length < 2 || !parts[0] || !parts[1]) { skipped += 1; return; } // sección + nombre son el mínimo
      editItems.push({
        section: parts[0], name: parts[1], unit_label: parts[2] || '',
        par_target: parts[3] ? parts[3] : null, notes: parts[4] || '',
      });
      added += 1;
    });
    if (!added) {
      utils.showToast('No se pudo leer ninguna línea: cada una necesita al menos «Sección | Nombre».', 'error');
      return;
    }
    $('bulkPasteInput').value = '';
    $('bulkPasteBox').hidden = true;
    templateDirty = true;
    renderItemEditRows();
    utils.showToast(`${added} insumo${added === 1 ? '' : 's'} agregado${added === 1 ? '' : 's'}${skipped ? ` (${skipped} línea${skipped === 1 ? '' : 's'} sin sección o nombre no se agregó)` : ''}. Recuerda tocar «Guardar la lista».`, 'success');
  });

  $('btnSaveTemplate').addEventListener('click', () => withButtonBusy($('btnSaveTemplate'), async () => {
    setTemplateError('');
    const cleanCheckpoints = editCheckpoints.map((c) => c.trim()).filter(Boolean);
    if (!cleanCheckpoints.length) {
      setTemplateError('La lista necesita al menos una revisión (por ejemplo 10am).');
      utils.showToast('Falta al menos una revisión.', 'error');
      return;
    }
    // Un renglón totalmente vacío se descarta sin preguntar. Uno a medias (sin sección o sin
    // nombre) antes también se descartaba en silencio y se perdía: ahora se marca y no se guarda.
    const rows = $('itemEditRows').querySelectorAll('tr');
    let invalid = 0;
    let firstInvalid = null;
    const keep = [];
    editItems.forEach((i, idx) => {
      const section = (i.section || '').trim();
      const name = (i.name || '').trim();
      const unit = (i.unit_label || '').trim();
      const notes = (i.notes || '').trim();
      const par = i.par_target == null ? '' : String(i.par_target).trim();
      if (!section && !name && !unit && !notes && !par) return;
      const row = rows[idx];
      const marks = [];
      if (!section) marks.push('.ei-section');
      if (!name) marks.push('.ei-name');
      if (par !== '' && (Number.isNaN(Number(par)) || Number(par) < 0)) marks.push('.ei-par');
      if (marks.length) {
        invalid += 1;
        marks.forEach((sel) => {
          const input = row && row.querySelector(sel);
          if (input) { input.classList.add('is-invalid'); if (!firstInvalid) firstInvalid = input; }
        });
        return;
      }
      keep.push({
        // El id viaja para que el servidor actualice el insumo en su lugar y el historial de
        // revisiones siga apuntando a él; los insumos nuevos no tienen id.
        id: i.id ?? null,
        section, name,
        unit_label: unit || null,
        par_target: par === '' ? null : par,
        notes: notes || null,
      });
    });
    if (invalid) {
      const msg = `Hay ${invalid} renglón${invalid === 1 ? '' : 'es'} incompleto${invalid === 1 ? '' : 's'} (marcado${invalid === 1 ? '' : 's'} en rojo): falta la sección, el nombre o la cantidad ideal no es un número.`;
      setTemplateError(msg);
      utils.showToast(msg, 'error');
      if (firstInvalid) { firstInvalid.scrollIntoView({ block: 'center', behavior: 'smooth' }); firstInvalid.focus({ preventScroll: true }); }
      return;
    }
    // Quitar insumos que ya estaban borra su renglón de la lista: se confirma con nombres.
    const keptIds = new Set(keep.map((i) => i.id).filter((id) => id != null));
    const removed = template.items.filter((i) => !keptIds.has(i.id));
    if (removed.length) {
      const names = removed.slice(0, 5).map((i) => i.name).join(', ') + (removed.length > 5 ? ` y ${removed.length - 5} más` : '');
      const ok = await askConfirm({
        title: `¿Quitar ${removed.length} insumo${removed.length === 1 ? '' : 's'} de la lista?`,
        message: `Se quita${removed.length === 1 ? '' : 'n'}: ${names}. Las revisiones ya guardadas no se borran.`,
        ok: 'Sí, guardar', cancel: 'Revisar', danger: true,
      });
      if (!ok) return;
    }
    try {
      template = await api.put(`/prep/templates/${template.id}`, {
        branch_id: branchId, name: template.name, checkpoints: cleanCheckpoints, items: keep,
      });
      activeCheckpoint = null;
      checkDirty = false;
      renderCheckpointChips();
      renderItemSections();
      renderTemplateEditor();
      utils.showToast('Lista guardada.', 'success');
    } catch (err) {
      const msg = `No se guardó la lista. ${err.message || 'Prueba otra vez.'}`;
      setTemplateError(msg);
      utils.showToast(msg, 'error');
    }
  }));

  // ==========================================================================
  // No perder lo escrito al salir
  // ==========================================================================
  window.addEventListener('beforeunload', (e) => {
    if (leaving || !isDirty()) return;
    e.preventDefault();
    e.returnValue = '';
  });
  document.addEventListener('click', async (e) => {
    const a = e.target.closest('a[href]');
    if (!a || a.target === '_blank' || !isDirty()) return;
    e.preventDefault();
    const ok = await askConfirm({
      title: '¿Salir sin guardar?',
      message: `${dirtyMessage()} Si sales ahora se pierden.`,
      ok: 'Salir sin guardar', cancel: 'Quedarme', danger: true,
    });
    if (!ok) return;
    leaving = true;
    window.location.href = a.href;
  });

  // ==========================================================================
  // Arranque
  // ==========================================================================
  user = await auth.checkSession();
  if (!user) { window.location.href = '/'; return; }

  $('prepGate').hidden = true;
  $('prepMain').hidden = false;
  FarmhouseShell.fillUserHeader({ nameId: 'prepAgentName', roleId: 'prepAgentRole', avatarId: 'prepAgentAvatar' }, user);
  canEdit = user.role !== 'agent';

  if (user.branch_id) {
    branchId = user.branch_id;
    const branchName = user.branch ? user.branch.name : `Sucursal #${branchId}`;
    $('prepHeaderScope').textContent = `Sucursal fija: ${branchName}`;
    await loadTemplateForBranch();
  } else {
    try {
      branches = (await api.get('/branches/')).filter((b) => b.active !== false && b.code !== 'CAT');
      const select = $('branchSelect');
      select.innerHTML = branches.map((b) => `<option value="${b.id}">${esc(b.name)}</option>`).join('');
      $('branchPicker').hidden = false;
      const scope = () => {
        const b = branches.find((x) => x.id === branchId);
        $('prepHeaderScope').textContent = b ? b.name : 'Elige una sucursal.';
      };
      select.addEventListener('change', async () => {
        if (isDirty()) {
          const ok = await askConfirm({
            title: '¿Cambiar de sucursal?',
            message: `${dirtyMessage()} Si cambias de sucursal se pierden.`,
            ok: 'Cambiar sin guardar', cancel: 'Quedarme', danger: true,
          });
          if (!ok) { select.value = branchId; return; }
        }
        branchId = Number(select.value);
        activeCheckpoint = null;
        scope();
        loadTemplateForBranch();
      });
      branchId = branches[0]?.id || null;
      scope();
      if (branchId) await loadTemplateForBranch();
      else showEmpty('No hay sucursales activas.');
    } catch (err) {
      showEmpty(`No se pudieron cargar las sucursales. ${err.message || 'Revisa la conexión.'}`);
      utils.showToast('No se pudieron cargar las sucursales.', 'error');
    }
  }

  utils.renderIcons();
});
