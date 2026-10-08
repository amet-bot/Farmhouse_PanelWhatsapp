/**
 * Farmhouse WhatsApp Center - Etiquetas de cliente
 *
 * Una palabra con color que el equipo le pone al contacto ("VIP", "Corporativo", "Reclamo"…).
 * Se ve en cada fila de la lista de chats y en el panel del cliente, donde también se cambian
 * (selector con casillas → PUT /tags/contacts/{id}). Crear/editar/borrar etiquetas pide el
 * permiso tags.manage (supervisores y admin); ponerlas, tags.assign (todos los agentes).
 */

const tagsModule = {
  tags: [],
  PALETTE: ['#16a34a', '#2563eb', '#7c3aed', '#db2777', '#ea580c', '#f59e0b', '#0891b2', '#64748b'],
  pickerOpen: false,

  canManage() {
    const user = auth.getUser();
    return !!(user && (user.permissions || []).includes('tags.manage'));
  },

  canAssign() {
    const user = auth.getUser();
    return !!(user && (user.permissions || []).includes('tags.assign'));
  },

  async init() {
    await this.load();
    this.bindPicker();
  },

  async load() {
    try {
      this.tags = await api.get('/tags/');
    } catch (e) {
      console.error('Error cargando etiquetas:', e);
      this.tags = [];
    }
    if (document.getElementById('modalTags')?.classList.contains('active')) this.renderTable();
  },

  /** Pastillas pequeñas para la lista de chats (máximo `max`, el resto como "+n"). */
  pillsHtml(tags, max = 2) {
    if (!tags || !tags.length) return '';
    const shown = tags.slice(0, max);
    const rest = tags.length - shown.length;
    return shown.map(t => this.pillHtml(t, 'conv-tag')).join('') +
      (rest > 0 ? `<span class="conv-tag conv-tag-more" title="${utils.escapeHtml(tags.slice(max).map(x => x.name).join(', '))}">+${rest}</span>` : '');
  },

  pillHtml(t, cls = 'tag-pill') {
    const color = /^#[0-9a-f]{6}$/i.test(t.color || '') ? t.color : '#64748b';
    return `<span class="${cls}" style="color:${color};border-color:${color}55;background:${color}1a">${utils.escapeHtml(t.name)}</span>`;
  },

  // ---------------------------------------------------------------- panel del cliente

  /** Pinta las etiquetas del contacto abierto junto a la pastilla de sucursal (#detailTags). */
  renderContactTags(contact) {
    const box = document.getElementById('detailContactTags');
    const btn = document.getElementById('btnEditContactTags');
    if (!box) return;
    const tags = (contact && contact.tags) || [];
    box.innerHTML = tags.map(t => this.pillHtml(t)).join('') || '<span class="muted-text u-xs">Sin etiquetas</span>';
    if (btn) btn.style.display = contact && contact.id && this.canAssign() ? '' : 'none';
    if (this.pickerOpen) this.renderPicker();
  },

  clearContactTags() {
    const box = document.getElementById('detailContactTags');
    if (box) box.innerHTML = '';
    const btn = document.getElementById('btnEditContactTags');
    if (btn) btn.style.display = 'none';
    this.closePicker();
  },

  currentContact() {
    return (typeof chatModule !== 'undefined' && chatModule.currentConversation && chatModule.currentConversation.contact) || null;
  },

  bindPicker() {
    const btn = document.getElementById('btnEditContactTags');
    const pop = document.getElementById('tagPickerPopover');
    if (!btn || !pop || btn.dataset.bound) return;
    btn.dataset.bound = '1';
    btn.addEventListener('click', (e) => { e.stopPropagation(); this.pickerOpen ? this.closePicker() : this.openPicker(); });
    pop.addEventListener('click', (e) => e.stopPropagation());
    pop.addEventListener('change', (e) => {
      const cb = e.target.closest('input[type="checkbox"][data-tag-id]');
      if (cb) this.applyFromPicker();
    });
    pop.addEventListener('click', (e) => {
      if (e.target.closest('[data-open-tags-admin]')) { this.closePicker(); this.open(); }
    });
    document.addEventListener('click', () => { if (this.pickerOpen) this.closePicker(); });
    document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && this.pickerOpen) this.closePicker(); });
  },

  openPicker() {
    const pop = document.getElementById('tagPickerPopover');
    if (!pop || !this.currentContact()) return;
    this.pickerOpen = true;
    pop.hidden = false;
    this.renderPicker();
  },

  closePicker() {
    const pop = document.getElementById('tagPickerPopover');
    if (pop) pop.hidden = true;
    this.pickerOpen = false;
  },

  renderPicker() {
    const pop = document.getElementById('tagPickerPopover');
    const contact = this.currentContact();
    if (!pop || !contact) return;
    const mine = new Set((contact.tags || []).map(t => t.id));
    if (!this.tags.length) {
      pop.innerHTML = `<div class="tag-pick-empty">Todavía no hay etiquetas.${this.canManage() ? ' <button type="button" class="link-btn" data-open-tags-admin="1">Crear la primera</button>' : ' Pídele al supervisor que las cree.'}</div>`;
      return;
    }
    pop.innerHTML = this.tags.map(t => `
      <label class="tag-pick-row">
        <input type="checkbox" data-tag-id="${t.id}" ${mine.has(t.id) ? 'checked' : ''} />
        <span class="tag-pick-dot" style="background:${utils.escapeHtml(t.color)}"></span>
        <span>${utils.escapeHtml(t.name)}</span>
      </label>`).join('') +
      (this.canManage() ? `<button type="button" class="link-btn tag-pick-admin" data-open-tags-admin="1"><i data-lucide="settings-2"></i> Administrar etiquetas</button>` : '');
    utils.renderIcons();
  },

  async applyFromPicker() {
    const pop = document.getElementById('tagPickerPopover');
    const contact = this.currentContact();
    if (!pop || !contact) return;
    const ids = [...pop.querySelectorAll('input[data-tag-id]:checked')].map(cb => Number(cb.dataset.tagId));
    try {
      const updated = await api.put(`/tags/contacts/${contact.id}`, { tag_ids: ids });
      contact.tags = updated.tags || [];
      this.renderContactTags(contact);
      // La fila de la lista también cambia sin esperar el refresco de 6 s.
      if (typeof conversationsModule !== 'undefined') {
        (conversationsModule.conversations || []).forEach(c => { if (c.contact && c.contact.id === contact.id) c.contact.tags = contact.tags; });
        conversationsModule.renderList();
      }
    } catch (e) {
      utils.showToast(e.message, 'error');
      this.renderPicker();
    }
  },

  // ---------------------------------------------------------------- administración (modal)

  open() {
    const modal = document.getElementById('modalTags');
    if (!modal) return;
    this.resetForm();
    this.renderTable();
    modal.classList.add('active');
  },

  close() {
    document.getElementById('modalTags')?.classList.remove('active');
  },

  renderTable() {
    const tbody = document.getElementById('tagsTableBody');
    if (!tbody) return;
    if (!this.tags.length) {
      tbody.innerHTML = `<tr><td class="u-empty-cell" colspan="3">Todavía no hay etiquetas. Crea la primera con el formulario de arriba (por ejemplo: VIP, Corporativo, Reclamo, Frecuente).</td></tr>`;
      return;
    }
    tbody.innerHTML = this.tags.map(t => `
      <tr>
        <td>${this.pillHtml(t)}</td>
        <td><span class="tag-pick-dot" style="background:${utils.escapeHtml(t.color)};display:inline-block;vertical-align:middle;margin-right:6px"></span><span class="u-mono">${utils.escapeHtml(t.color)}</span></td>
        <td class="u-nowrap">
          <button type="button" class="btn-sm-action" data-tag-edit="${t.id}"><i data-lucide="pencil"></i> Editar</button>
          <button type="button" class="btn-sm-action delete-action" data-tag-del="${t.id}"><i data-lucide="trash-2"></i></button>
        </td>
      </tr>`).join('');
    utils.renderIcons();
  },

  renderPalette(selected) {
    const box = document.getElementById('tagColorPalette');
    if (!box) return;
    box.innerHTML = this.PALETTE.map(c => `<button type="button" class="tag-swatch${c === selected ? ' is-selected' : ''}" data-color="${c}" style="background:${c}" aria-label="${c}"></button>`).join('');
    document.getElementById('tagColor').value = selected;
  },

  resetForm() {
    const form = document.getElementById('formTag');
    if (!form) return;
    form.reset();
    document.getElementById('tagId').value = '';
    this.renderPalette(this.PALETTE[0]);
    document.getElementById('tagFormTitle').textContent = 'Nueva etiqueta';
    document.getElementById('btnTagCancel').style.display = 'none';
  },

  edit(id) {
    const t = this.tags.find(x => x.id === id);
    if (!t) return;
    document.getElementById('tagId').value = t.id;
    document.getElementById('tagName').value = t.name;
    this.renderPalette(this.PALETTE.includes(t.color) ? t.color : t.color);
    document.getElementById('tagFormTitle').textContent = `Editar «${t.name}»`;
    document.getElementById('btnTagCancel').style.display = '';
    document.getElementById('tagName').focus();
  },

  async save() {
    const id = document.getElementById('tagId').value;
    const data = { name: document.getElementById('tagName').value.trim(), color: document.getElementById('tagColor').value };
    if (!data.name) { utils.showToast('Escribe el nombre de la etiqueta.', 'error'); return; }
    try {
      if (id) await api.put(`/tags/${id}`, data);
      else await api.post('/tags/', data);
      utils.showToast(id ? 'Etiqueta guardada.' : `Etiqueta «${data.name}» creada.`, 'success');
      this.resetForm();
      await this.load();
      this.renderTable();
      // Los clientes abiertos y la lista muestran el nombre/color nuevo.
      if (typeof conversationsModule !== 'undefined') conversationsModule.loadConversations();
      const c = this.currentContact();
      if (c) this.renderContactTags(c);
    } catch (e) {
      utils.showToast(e.message, 'error');
    }
  },

  async remove(id) {
    const t = this.tags.find(x => x.id === id);
    if (!t || !confirm(`¿Borrar la etiqueta «${t.name}»? Se le quita a todos los clientes que la tengan.`)) return;
    try {
      await api.delete(`/tags/${id}`);
      utils.showToast('Etiqueta borrada.', 'info');
      await this.load();
      this.renderTable();
      if (typeof conversationsModule !== 'undefined') conversationsModule.loadConversations();
      const c = this.currentContact();
      if (c) { c.tags = (c.tags || []).filter(x => x.id !== id); this.renderContactTags(c); }
    } catch (e) {
      utils.showToast(e.message, 'error');
    }
  },

  bindModal() {
    const modal = document.getElementById('modalTags');
    if (!modal || modal.dataset.bound) return;
    modal.dataset.bound = '1';
    document.getElementById('closeModalTags')?.addEventListener('click', () => this.close());
    document.getElementById('btnTagCancel')?.addEventListener('click', () => this.resetForm());
    document.getElementById('formTag')?.addEventListener('submit', (e) => { e.preventDefault(); this.save(); });
    document.getElementById('tagColorPalette')?.addEventListener('click', (e) => {
      const sw = e.target.closest('[data-color]');
      if (sw) this.renderPalette(sw.dataset.color);
    });
    document.getElementById('tagsTableBody')?.addEventListener('click', (e) => {
      const ed = e.target.closest('[data-tag-edit]');
      if (ed) return this.edit(Number(ed.dataset.tagEdit));
      const del = e.target.closest('[data-tag-del]');
      if (del) return this.remove(Number(del.dataset.tagDel));
    });
  },
};
