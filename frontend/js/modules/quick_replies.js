/**
 * Farmhouse WhatsApp Center - Respuestas rápidas configurables
 *
 * Antes eran 4 textos fijos en chat.js. Ahora viven en el servidor (/quick-replies): globales o
 * por sucursal, con un /atajo. El agente las usa de dos formas:
 *   - las pastillas sobre el cuadro de texto (#quickReplyRow), o
 *   - escribiendo "/" en el cuadro: aparece una lista filtrable; Enter o clic inserta el texto.
 * Siempre solo RELLENAN el cuadro; el agente revisa y pulsa Enviar. En el cuerpo se reemplazan
 * {nombre}, {sucursal} y {menu} con los datos de la conversación abierta.
 *
 * Administrar (crear/editar/borrar) pide el permiso quick_replies.manage (supervisores y admin);
 * el supervisor de sucursal solo ve y toca las de su sucursal.
 */

const quickRepliesModule = {
  items: [],
  // Popover "/": índice resaltado y lista filtrada actual.
  popoverOpen: false,
  filtered: [],
  highlight: 0,
  MAX_PILLS: 6,
  // Atajos a los que apuntaban los botones viejos (chat.js#insertQuickReply, Acciones rápidas).
  LEGACY_KINDS: { menu: 'menu', order: 'pedido', hours: 'horario', human: 'asesor' },

  canManage() {
    const user = auth.getUser();
    return !!(user && (user.permissions || []).includes('quick_replies.manage'));
  },

  async init() {
    await this.load();
    this.attachComposer();
  },

  async load() {
    try {
      const q = this.canManage() ? '?include_inactive=1' : '';
      this.items = await api.get(`/quick-replies/${q}`);
    } catch (e) {
      console.error('Error cargando respuestas rápidas:', e);
      this.items = [];
    }
    this.renderPills();
    if (document.getElementById('modalQuickReplies')?.classList.contains('active')) this.renderTable();
  },

  active() {
    return this.items.filter(r => r.active);
  },

  /** Reemplaza {nombre}, {sucursal} y {menu} con la conversación abierta. */
  fill(body) {
    const conv = (typeof chatModule !== 'undefined' && chatModule.currentConversation) || {};
    const contact = conv.contact || {};
    const branch = conv.branch || {};
    const firstName = (contact.name || '').trim().split(/\s+/)[0] || '';
    return String(body || '')
      .replace(/\{nombre\}/gi, firstName)
      .replace(/\{sucursal\}/gi, branch.name || '')
      .replace(/\{menu\}/gi, `${window.location.origin}/menu`)
      // "Hola , ¿cómo estás?" si no hay nombre: se limpia el espacio sobrante antes de la coma.
      .replace(/\s+,/g, ',');
  },

  insert(reply) {
    const input = document.getElementById('messageInput');
    if (!reply || !input) return;
    const text = this.fill(reply.body);
    input.value = text;
    input.focus();
    input.setSelectionRange(text.length, text.length);
    input.dispatchEvent(new Event('input', { bubbles: true }));
    this.closePopover();
  },

  insertByShortcut(shortcut) {
    const r = this.active().find(x => x.shortcut === shortcut);
    if (r) this.insert(r);
    return !!r;
  },

  /** Compatibilidad con los botones viejos: menu / order / hours / human. */
  insertLegacy(kind) {
    const shortcut = this.LEGACY_KINDS[kind] || kind;
    if (this.insertByShortcut(shortcut)) return true;
    // Si el admin borró esa respuesta, el botón al menos hace algo razonable.
    const fallback = {
      menu: `Claro, te comparto nuestro menú para que veas todos los productos disponibles 😊\n${window.location.origin}/menu`,
      order: '¿Me confirmas tu nombre o número de pedido para revisar el estado?',
      hours: 'Nuestro horario es de Lunes a Domingo, 8:00 AM a 9:30 PM (Vía Porras y Obarrio abren desde las 6:00 AM). ¿Te comparto la dirección de la sucursal más cercana?',
      human: 'Con gusto te comunico con un asesor para que te ayude personalmente.',
    }[kind];
    if (fallback) this.insert({ body: fallback });
    return !!fallback;
  },

  // ---------------------------------------------------------------- pastillas sobre el cuadro

  renderPills() {
    const row = document.getElementById('quickReplyRow');
    if (!row) return;
    const list = this.active().slice(0, this.MAX_PILLS);
    const rest = this.active().length - list.length;
    row.innerHTML = list.map(r => `
      <button type="button" class="quick-reply-pill" data-shortcut="${utils.escapeHtml(r.shortcut)}" title="/${utils.escapeHtml(r.shortcut)}">
        <i data-lucide="zap"></i> ${utils.escapeHtml(r.title)}
      </button>`).join('')
      + (rest > 0 ? `<button type="button" class="quick-reply-pill quick-reply-more" data-open-popover="1" title="Escribe / en el mensaje para ver todas">+${rest} más</button>` : '');
    row.style.display = list.length ? '' : 'none';
    utils.renderIcons();
  },

  // ---------------------------------------------------------------- popover con "/"

  attachComposer() {
    const input = document.getElementById('messageInput');
    const pop = document.getElementById('quickReplyPopover');
    const row = document.getElementById('quickReplyRow');
    if (!input || !pop || input.dataset.quickRepliesBound) return;
    input.dataset.quickRepliesBound = '1';

    row?.addEventListener('click', (e) => {
      const more = e.target.closest('[data-open-popover]');
      if (more) { input.focus(); this.openPopover(''); return; }
      const pill = e.target.closest('.quick-reply-pill[data-shortcut]');
      if (pill) this.insertByShortcut(pill.dataset.shortcut);
    });

    input.addEventListener('input', () => {
      const v = input.value;
      // Solo cuando TODO el cuadro es "/algo" en una línea: así "/" dentro de una URL no abre nada.
      if (v.startsWith('/') && !v.includes('\n') && v.length <= 41) this.openPopover(v.slice(1));
      else this.closePopover();
    });

    // Captura: va ANTES del onkeydown de chat.js (que envía con Enter) y lo frena si el popover
    // está abierto, para que Enter elija la respuesta en vez de mandar "/hor" al cliente.
    input.addEventListener('keydown', (e) => {
      if (!this.popoverOpen) return;
      if (e.key === 'ArrowDown') { e.preventDefault(); this.move(1); }
      else if (e.key === 'ArrowUp') { e.preventDefault(); this.move(-1); }
      else if (e.key === 'Enter' || e.key === 'Tab') {
        if (this.filtered.length) {
          e.preventDefault(); e.stopImmediatePropagation();
          this.insert(this.filtered[this.highlight]);
        } else if (e.key === 'Enter') {
          // Sin coincidencias: no mandar "/xyz" al cliente por error.
          e.preventDefault(); e.stopImmediatePropagation();
          utils.showToast('No hay una respuesta rápida con ese atajo.', 'info');
        }
      } else if (e.key === 'Escape') { e.preventDefault(); this.closePopover(); }
    }, true);

    pop.addEventListener('mousedown', (e) => e.preventDefault()); // no robar el foco del cuadro
    pop.addEventListener('click', (e) => {
      const item = e.target.closest('[data-reply-id]');
      if (!item) return;
      const r = this.items.find(x => x.id === Number(item.dataset.replyId));
      if (r) this.insert(r);
    });
    input.addEventListener('blur', () => setTimeout(() => this.closePopover(), 120));
  },

  openPopover(query) {
    const pop = document.getElementById('quickReplyPopover');
    if (!pop) return;
    const q = (query || '').toLowerCase().trim();
    this.filtered = this.active().filter(r => !q || r.shortcut.includes(q) || r.title.toLowerCase().includes(q));
    this.highlight = 0;
    this.popoverOpen = true;
    pop.hidden = false;
    this.renderPopover(q);
  },

  renderPopover(q) {
    const pop = document.getElementById('quickReplyPopover');
    if (!pop) return;
    if (!this.filtered.length) {
      pop.innerHTML = `<div class="qr-pop-empty">Sin respuestas para «/${utils.escapeHtml(q || '')}».${this.canManage() ? ' Créala en Administración → Respuestas rápidas.' : ''}</div>`;
      return;
    }
    pop.innerHTML = `<div class="qr-pop-hint">↑↓ para moverte · Enter para insertar · Esc para cerrar</div>` +
      this.filtered.map((r, i) => `
      <div class="qr-pop-item${i === this.highlight ? ' is-active' : ''}" data-reply-id="${r.id}">
        <div class="qr-pop-top"><span class="qr-pop-shortcut">/${utils.escapeHtml(r.shortcut)}</span><span class="qr-pop-title">${utils.escapeHtml(r.title)}</span></div>
        <div class="qr-pop-body">${utils.escapeHtml(this.fill(r.body))}</div>
      </div>`).join('');
    pop.querySelector('.is-active')?.scrollIntoView({ block: 'nearest' });
  },

  move(delta) {
    if (!this.filtered.length) return;
    this.highlight = (this.highlight + delta + this.filtered.length) % this.filtered.length;
    const pop = document.getElementById('quickReplyPopover');
    pop?.querySelectorAll('.qr-pop-item').forEach((el, i) => el.classList.toggle('is-active', i === this.highlight));
    pop?.querySelector('.is-active')?.scrollIntoView({ block: 'nearest' });
  },

  closePopover() {
    const pop = document.getElementById('quickReplyPopover');
    if (pop) pop.hidden = true;
    this.popoverOpen = false;
  },

  // ---------------------------------------------------------------- administración (modal)

  open() {
    const modal = document.getElementById('modalQuickReplies');
    if (!modal) return;
    this.resetForm();
    this.renderTable();
    modal.classList.add('active');
  },

  close() {
    document.getElementById('modalQuickReplies')?.classList.remove('active');
  },

  branchName(id) {
    if (id == null) return 'Todas las sucursales';
    const b = (typeof branchesModule !== 'undefined' ? branchesModule.branches : []).find(x => x.id === id);
    return b ? b.name : `Sucursal ${id}`;
  },

  renderTable() {
    const tbody = document.getElementById('quickRepliesTableBody');
    if (!tbody) return;
    if (!this.items.length) {
      tbody.innerHTML = `<tr><td colspan="4" style="text-align:center;padding:24px;color:var(--text-muted)">Todavía no hay respuestas rápidas. Crea la primera con el formulario de arriba.</td></tr>`;
      return;
    }
    tbody.innerHTML = this.items.map(r => `
      <tr${r.active ? '' : ' style="opacity:.55"'}>
        <td><span class="qr-shortcut-chip">/${utils.escapeHtml(r.shortcut)}</span><div style="font-weight:600;margin-top:3px">${utils.escapeHtml(r.title)}</div></td>
        <td class="qr-body-cell" title="${utils.escapeHtml(r.body)}">${utils.escapeHtml(r.body)}</td>
        <td style="white-space:nowrap">${utils.escapeHtml(this.branchName(r.branch_id))}${r.active ? '' : '<div class="dev-badge offline" style="margin-top:4px;display:inline-block">Inactiva</div>'}</td>
        <td style="white-space:nowrap">
          <button type="button" class="btn-sm-action" data-qr-edit="${r.id}"><i data-lucide="pencil"></i> Editar</button>
          <button type="button" class="btn-sm-action delete-action" data-qr-del="${r.id}"><i data-lucide="trash-2"></i></button>
        </td>
      </tr>`).join('');
    utils.renderIcons();
  },

  fillBranchSelect() {
    const sel = document.getElementById('qrBranch');
    if (!sel) return;
    const user = auth.getUser() || {};
    const global = user.role === 'admin' || (user.role === 'supervisor' && !user.branch_id);
    const branches = typeof branchesModule !== 'undefined' ? branchesModule.branches : [];
    if (global) {
      sel.innerHTML = '<option value="">Todas las sucursales</option>' + branches.map(b => `<option value="${b.id}">${utils.escapeHtml(b.name)}</option>`).join('');
      sel.disabled = false;
    } else {
      sel.innerHTML = `<option value="${user.branch_id}">${utils.escapeHtml(this.branchName(user.branch_id))}</option>`;
      sel.disabled = true;
    }
  },

  resetForm() {
    const form = document.getElementById('formQuickReply');
    if (!form) return;
    form.reset();
    document.getElementById('qrId').value = '';
    document.getElementById('qrActive').checked = true;
    this.fillBranchSelect();
    document.getElementById('qrFormTitle').textContent = 'Nueva respuesta rápida';
    document.getElementById('btnQrCancel').style.display = 'none';
  },

  edit(id) {
    const r = this.items.find(x => x.id === id);
    if (!r) return;
    this.fillBranchSelect();
    document.getElementById('qrId').value = r.id;
    document.getElementById('qrShortcut').value = r.shortcut;
    document.getElementById('qrTitle').value = r.title;
    document.getElementById('qrBody').value = r.body;
    document.getElementById('qrBranch').value = r.branch_id == null ? '' : String(r.branch_id);
    document.getElementById('qrActive').checked = !!r.active;
    document.getElementById('qrFormTitle').textContent = `Editar /${r.shortcut}`;
    document.getElementById('btnQrCancel').style.display = '';
    document.getElementById('qrShortcut').focus();
  },

  async save() {
    const id = document.getElementById('qrId').value;
    const branchVal = document.getElementById('qrBranch').value;
    const data = {
      shortcut: document.getElementById('qrShortcut').value.trim(),
      title: document.getElementById('qrTitle').value.trim(),
      body: document.getElementById('qrBody').value.trim(),
      branch_id: branchVal === '' ? null : Number(branchVal),
      active: document.getElementById('qrActive').checked,
    };
    if (!data.shortcut || !data.title || !data.body) {
      utils.showToast('Completa atajo, título y mensaje.', 'error');
      return;
    }
    try {
      if (id) await api.put(`/quick-replies/${id}`, data);
      else await api.post('/quick-replies/', data);
      utils.showToast(id ? 'Respuesta rápida guardada.' : `Respuesta rápida creada: /${data.shortcut}`, 'success');
      this.resetForm();
      await this.load();
    } catch (e) {
      utils.showToast(e.message, 'error');
    }
  },

  async remove(id) {
    const r = this.items.find(x => x.id === id);
    if (!r || !confirm(`¿Borrar la respuesta rápida /${r.shortcut} («${r.title}»)?`)) return;
    try {
      await api.delete(`/quick-replies/${id}`);
      utils.showToast('Respuesta rápida borrada.', 'info');
      await this.load();
    } catch (e) {
      utils.showToast(e.message, 'error');
    }
  },

  bindModal() {
    const modal = document.getElementById('modalQuickReplies');
    if (!modal || modal.dataset.bound) return;
    modal.dataset.bound = '1';
    document.getElementById('closeModalQuickReplies')?.addEventListener('click', () => this.close());
    document.getElementById('btnQrCancel')?.addEventListener('click', () => this.resetForm());
    document.getElementById('formQuickReply')?.addEventListener('submit', (e) => { e.preventDefault(); this.save(); });
    document.getElementById('quickRepliesTableBody')?.addEventListener('click', (e) => {
      const ed = e.target.closest('[data-qr-edit]');
      if (ed) return this.edit(Number(ed.dataset.qrEdit));
      const del = e.target.closest('[data-qr-del]');
      if (del) return this.remove(Number(del.dataset.qrDel));
    });
  },
};
