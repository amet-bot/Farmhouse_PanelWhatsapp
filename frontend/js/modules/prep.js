/**
 * Farmhouse Link — Prep de Bowls
 *
 * Calcado de la hoja de papel que ya usan en cocina: una plantilla por sucursal (ítems agrupados
 * por sección, cada uno con un par objetivo) y un checklist que se llena varias veces al día por
 * checkpoint. Los checkpoints no son fijos (10am/3pm/8pm en el papel, pero "Congelador Grande"/
 * "Congelador chico" para los kits de smoothie) — cada plantilla define los suyos.
 *
 * Editar la plantilla (agregar/sacar ítems, cambiar pars) es de supervisor/admin; un agente solo
 * llena el checklist del día. El "pegar en bloque" existe porque transcribir 80+ ítems a mano,
 * fila por fila, es exactamente el tipo de fricción que hace que nadie use la pantalla nueva.
 */
document.addEventListener('DOMContentLoaded', async () => {
  const $ = (id) => document.getElementById(id);
  const esc = (s) => utils.escapeHtml(s ?? '');

  let user = null;
  let branchId = null;
  let canEdit = false;
  let template = null;       // detalle completo (con items) de la plantilla activa
  let activeCheckpoint = null;
  let existingEntries = {};  // template_item_id -> {on_hand, note} del checkpoint activo, si ya se había llenado hoy

  FarmhouseShell.initTheme();
  FarmhouseShell.initLogout({ redirectTo: '/' });
  // Sesión vencida: volver al login en vez de quedarse en una página que solo tira errores.
  window.addEventListener('auth:unauthorized', () => { window.location.href = '/'; });

  // ==========================================================================
  // Carga de la plantilla
  // ==========================================================================
  async function loadTemplateForBranch() {
    $('prepEmptyState').hidden = true;
    $('prepWithTemplate').hidden = true;
    try {
      const list = await api.get(`/prep/templates?branch_id=${branchId}`);
      if (!list.length) {
        template = null;
        $('prepEmptyState').hidden = false;
        $('btnCreateTemplate').hidden = !canEdit;
        return;
      }
      template = await api.get(`/prep/templates/${list[0].id}`);
      $('prepWithTemplate').hidden = false;
      $('tabPlantilla').hidden = !canEdit;
      renderCheckpointChips();
      renderItemSections();
      renderTemplateEditor();
    } catch (err) {
      utils.showToast('No se pudo cargar la plantilla de prep.', 'error');
    }
  }

  $('btnCreateTemplate').addEventListener('click', () => withButtonBusy($('btnCreateTemplate'), async () => {
    try {
      const created = await api.post('/prep/templates', {
        branch_id: branchId, name: 'Bowls', checkpoints: ['10am', '3pm', '8pm'], items: [],
      });
      template = created;
      utils.showToast('Plantilla creada. Agregá los ítems en la pestaña Plantilla.', 'success');
      $('prepEmptyState').hidden = true;
      $('prepWithTemplate').hidden = false;
      $('tabPlantilla').hidden = !canEdit;
      renderCheckpointChips();
      renderItemSections();
      renderTemplateEditor();
      setTab('plantilla');
    } catch (err) {
      utils.showToast(err.message || 'No se pudo crear la plantilla.', 'error');
    }
  }));

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
  // Llenar checklist
  // ==========================================================================
  function renderCheckpointChips() {
    const row = $('checkpointRow');
    row.innerHTML = template.checkpoints.map((cp) => `
      <button type="button" class="prep-chip${cp === activeCheckpoint ? ' active' : ''}" data-checkpoint="${esc(cp)}">${esc(cp)}</button>
    `).join('');
    row.querySelectorAll('.prep-chip').forEach((chip) => {
      chip.addEventListener('click', () => selectCheckpoint(chip.dataset.checkpoint));
    });
    if (!activeCheckpoint && template.checkpoints.length) selectCheckpoint(template.checkpoints[0]);
  }

  async function selectCheckpoint(checkpoint) {
    activeCheckpoint = checkpoint;
    existingEntries = {};
    document.querySelectorAll('.prep-chip').forEach((c) => c.classList.toggle('active', c.dataset.checkpoint === checkpoint));
    const templateId = template.id;
    const entries = {};
    try {
      const checks = await api.get(`/prep/templates/${templateId}/checks`);
      const existing = checks.find((c) => c.checkpoint === checkpoint);
      if (existing) {
        existing.entries.forEach((e) => { entries[e.template_item_id] = e; });
      }
    } catch (err) { /* si falla, se llena en blanco — no bloquea el checklist */ }
    // Tocar 10am y enseguida 3pm: la respuesta de 10am llegaba después y pintaba (y guardaba)
    // sus cantidades como si fueran las de 3pm. Si el usuario ya eligió otro, se descarta.
    if (checkpoint !== activeCheckpoint || !template || template.id !== templateId) return;
    existingEntries = entries;
    renderItemSections();
  }

  // Deshabilita el botón mientras corre la acción: un doble toque ya no crea dos plantillas ni
  // manda el checklist dos veces.
  async function withButtonBusy(button, action) {
    if (button.disabled) return;
    button.disabled = true;
    try {
      await action();
    } finally {
      button.disabled = false;
    }
  }

  function renderItemSections() {
    const container = $('prepSections');
    if (!template.items.length) {
      container.innerHTML = '<p class="prep-empty-hint">Esta plantilla todavía no tiene ítems.</p>';
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
          const par = item.par_target != null ? `Par: ${item.par_target}${item.unit_label ? ' ' + esc(item.unit_label) : ''}` : (item.unit_label || '');
          return `
            <div class="prep-item-row" data-item-id="${item.id}">
              <div class="prep-item-info">
                <strong>${esc(item.name)}</strong>
                <small>${esc(par)}</small>
                ${item.notes ? `<small class="prep-item-note">${esc(item.notes)}</small>` : ''}
              </div>
              <input type="number" class="prep-item-input" min="0" step="0.01" value="${value}" placeholder="0">
            </div>
          `;
        }).join('')}
      </div>
    `).join('');
  }

  $('btnSubmitCheck').addEventListener('click', () => withButtonBusy($('btnSubmitCheck'), async () => {
    if (!template || !activeCheckpoint) return;
    const entries = [];
    document.querySelectorAll('#prepSections .prep-item-row').forEach((row) => {
      const value = row.querySelector('.prep-item-input').value;
      if (value !== '') entries.push({ template_item_id: Number(row.dataset.itemId), on_hand: value });
    });
    if (!entries.length) {
      utils.showToast('Ingresá al menos una cantidad.', 'error');
      return;
    }
    try {
      await api.post(`/prep/templates/${template.id}/checks`, { checkpoint: activeCheckpoint, entries });
      utils.showToast(`Checklist de ${activeCheckpoint} guardado.`, 'success');
    } catch (err) {
      utils.showToast(err.message || 'No se pudo guardar el checklist.', 'error');
    }
  }));

  // ==========================================================================
  // Editor de plantilla (supervisor/admin)
  // ==========================================================================
  let editCheckpoints = [];
  let editItems = [];

  function renderTemplateEditor() {
    editCheckpoints = [...template.checkpoints];
    editItems = template.items.map((i) => ({ ...i }));
    renderCheckpointEditRow();
    renderItemEditRows();
  }

  function renderCheckpointEditRow() {
    $('checkpointEditRow').innerHTML = editCheckpoints.map((cp, idx) => `
      <span class="prep-checkpoint-edit-item">
        <input type="text" value="${esc(cp)}" data-idx="${idx}" class="prep-checkpoint-input">
        <button type="button" data-remove-cp="${idx}" aria-label="Quitar checkpoint" title="Quitar checkpoint"><i data-lucide="x"></i></button>
      </span>
    `).join('');
    utils.renderIcons();
    $('checkpointEditRow').querySelectorAll('.prep-checkpoint-input').forEach((input) => {
      input.addEventListener('input', () => { editCheckpoints[Number(input.dataset.idx)] = input.value; });
    });
    $('checkpointEditRow').querySelectorAll('[data-remove-cp]').forEach((btn) => {
      btn.addEventListener('click', () => {
        editCheckpoints.splice(Number(btn.dataset.removeCp), 1);
        renderCheckpointEditRow();
      });
    });
  }
  $('btnAddCheckpoint').addEventListener('click', () => { editCheckpoints.push('Nuevo checkpoint'); renderCheckpointEditRow(); });

  function renderItemEditRows() {
    $('itemEditRows').innerHTML = editItems.map((item, idx) => `
      <tr data-idx="${idx}">
        <td><input type="text" class="ei-section" value="${esc(item.section)}"></td>
        <td><input type="text" class="ei-name" value="${esc(item.name)}"></td>
        <td><input type="text" class="ei-unit" value="${esc(item.unit_label || '')}"></td>
        <td><input type="number" step="0.01" min="0" class="ei-par" value="${esc(item.par_target ?? '')}"></td>
        <td><input type="text" class="ei-notes" value="${esc(item.notes || '')}"></td>
        <td><button type="button" class="prep-row-delete" data-remove-item="${idx}" aria-label="Quitar ítem" title="Quitar ítem"><i data-lucide="trash-2"></i></button></td>
      </tr>
    `).join('');
    utils.renderIcons();
    $('itemEditRows').querySelectorAll('tr').forEach((row) => {
      const idx = Number(row.dataset.idx);
      row.querySelector('.ei-section').addEventListener('input', (e) => { editItems[idx].section = e.target.value; });
      row.querySelector('.ei-name').addEventListener('input', (e) => { editItems[idx].name = e.target.value; });
      row.querySelector('.ei-unit').addEventListener('input', (e) => { editItems[idx].unit_label = e.target.value; });
      row.querySelector('.ei-par').addEventListener('input', (e) => { editItems[idx].par_target = e.target.value; });
      row.querySelector('.ei-notes').addEventListener('input', (e) => { editItems[idx].notes = e.target.value; });
    });
    $('itemEditRows').querySelectorAll('[data-remove-item]').forEach((btn) => {
      btn.addEventListener('click', () => {
        editItems.splice(Number(btn.dataset.removeItem), 1);
        renderItemEditRows();
      });
    });
  }

  $('btnAddItemRow').addEventListener('click', () => {
    editItems.push({ section: '', name: '', unit_label: '', par_target: null, notes: '' });
    renderItemEditRows();
  });

  $('btnBulkPaste').addEventListener('click', () => { $('bulkPasteBox').hidden = !$('bulkPasteBox').hidden; });

  $('btnApplyBulkPaste').addEventListener('click', () => {
    const raw = $('bulkPasteInput').value.trim();
    if (!raw) return;
    let added = 0;
    raw.split('\n').forEach((line) => {
      const parts = line.split('|').map((p) => p.trim());
      if (parts.length < 2 || !parts[0] || !parts[1]) return; // sección + nombre son el mínimo
      editItems.push({
        section: parts[0], name: parts[1], unit_label: parts[2] || '',
        par_target: parts[3] ? parts[3] : null, notes: parts[4] || '',
      });
      added += 1;
    });
    $('bulkPasteInput').value = '';
    $('bulkPasteBox').hidden = true;
    renderItemEditRows();
    utils.showToast(`${added} ítem(s) agregado(s). Recordá "Guardar plantilla".`, 'success');
  });

  $('btnSaveTemplate').addEventListener('click', () => withButtonBusy($('btnSaveTemplate'), async () => {
    const cleanCheckpoints = editCheckpoints.map((c) => c.trim()).filter(Boolean);
    if (!cleanCheckpoints.length) {
      utils.showToast('Necesitás al menos un checkpoint.', 'error');
      return;
    }
    const cleanItems = editItems
      .filter((i) => i.section.trim() && i.name.trim())
      .map((i) => ({
        // El id viaja para que el servidor actualice el ítem en su lugar y el historial de
        // checklists siga apuntando a él; los ítems nuevos no tienen id.
        id: i.id ?? null,
        section: i.section.trim(), name: i.name.trim(),
        unit_label: i.unit_label ? i.unit_label.trim() : null,
        par_target: i.par_target === '' || i.par_target == null ? null : i.par_target,
        notes: i.notes ? i.notes.trim() : null,
      }));
    try {
      template = await api.put(`/prep/templates/${template.id}`, {
        branch_id: branchId, name: template.name, checkpoints: cleanCheckpoints, items: cleanItems,
      });
      activeCheckpoint = null;
      renderCheckpointChips();
      renderItemSections();
      renderTemplateEditor();
      utils.showToast('Plantilla guardada.', 'success');
    } catch (err) {
      utils.showToast(err.message || 'No se pudo guardar la plantilla.', 'error');
    }
  }));

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
      const branches = await api.get('/branches/');
      const select = $('branchSelect');
      select.innerHTML = branches.map((b) => `<option value="${b.id}">${esc(b.name)}</option>`).join('');
      select.hidden = false;
      select.addEventListener('change', () => {
        branchId = Number(select.value);
        activeCheckpoint = null;
        loadTemplateForBranch();
      });
      branchId = branches[0]?.id || null;
      $('prepHeaderScope').textContent = 'Elegí una sucursal.';
      if (branchId) await loadTemplateForBranch();
    } catch (err) {
      utils.showToast('No se pudieron cargar las sucursales.', 'error');
    }
  }

  utils.renderIcons();
});
