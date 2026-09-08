/**
 * Directorio persistente de personas que han contactado a Farmhouse.
 * Las exportaciones se descargan desde el mismo servidor; este módulo no envía datos a terceros.
 */
const contactsModule = {
  contacts: [],
  searchTimer: null,

  async open() {
    document.getElementById('modalContactsList')?.classList.add('active');
    await this.load();
  },

  close() {
    document.getElementById('modalContactsList')?.classList.remove('active');
  },

  async load(query = '') {
    const tbody = document.getElementById('contactsTableBody');
    if (tbody) tbody.innerHTML = '<tr><td colspan="6" class="contacts-empty">Cargando contactos...</td></tr>';
    try {
      const suffix = query.trim() ? `?query=${encodeURIComponent(query.trim())}` : '';
      this.contacts = await api.get(`/contacts/directory${suffix}`);
      this.render();
    } catch (error) {
      if (tbody) tbody.innerHTML = `<tr><td colspan="6" class="contacts-empty contacts-error">${utils.escapeHtml(error.message)}</td></tr>`;
    }
  },

  render() {
    const tbody = document.getElementById('contactsTableBody');
    if (!tbody) return;

    const total = this.contacts.length;
    const returning = this.contacts.filter(contact => contact.conversation_count > 1).length;
    const withOrders = this.contacts.filter(contact => contact.order_count > 0).length;
    const archived = this.contacts.reduce((sum, contact) => sum + Number(contact.archived_conversation_count || 0), 0);
    document.getElementById('contactStatTotal').textContent = total;
    document.getElementById('contactStatReturning').textContent = returning;
    document.getElementById('contactStatOrders').textContent = withOrders;
    document.getElementById('contactStatArchived').textContent = archived;

    if (!total) {
      tbody.innerHTML = '<tr><td colspan="6" class="contacts-empty">Todavía no hay contactos que mostrar.</td></tr>';
      return;
    }

    tbody.innerHTML = this.contacts.map(contact => {
      const isReturning = Number(contact.conversation_count || 0) > 1;
      const typeLabel = Number(contact.order_count || 0) > 0
        ? 'Cliente con pedidos'
        : (isReturning ? 'Cliente recurrente' : 'Contacto nuevo');
      const typeClass = Number(contact.order_count || 0) > 0 ? 'order' : (isReturning ? 'returning' : 'new');
      const activeConversationId = contact.latest_active_conversation_id;
      const action = activeConversationId
        ? `<button class="btn-sm-action" onclick="contactsModule.openConversation(${Number(activeConversationId)})"><i data-lucide="message-circle"></i> Abrir chat</button>`
        : '<span class="contact-archived-label"><i data-lucide="archive"></i> Archivado</span>';
      const archivedText = contact.archived_conversation_count
        ? `<small>${Number(contact.archived_conversation_count)} archivada${Number(contact.archived_conversation_count) === 1 ? '' : 's'}</small>`
        : '';

      return `
        <tr>
          <td>
            <div class="contact-person-cell">
              <span class="contact-mini-avatar" style="background:${utils.getAvatarColor(contact.name)}">${utils.escapeHtml(utils.getInitials(contact.name))}</span>
              <div><strong>${utils.escapeHtml(contact.name || 'Cliente')}</strong><small>${utils.escapeHtml(contact.phone || '-')}</small></div>
            </div>
          </td>
          <td>${utils.escapeHtml(utils.formatDateTime(contact.last_interaction) || '-')}</td>
          <td>${utils.escapeHtml(contact.latest_branch_name || 'Sin asignar')}</td>
          <td><strong>${Number(contact.conversation_count || 0)} chat${Number(contact.conversation_count || 0) === 1 ? '' : 's'}</strong>${archivedText}<small>${Number(contact.order_count || 0)} pedido${Number(contact.order_count || 0) === 1 ? '' : 's'}</small></td>
          <td><span class="contact-type-badge ${typeClass}">${utils.escapeHtml(typeLabel)}</span></td>
          <td>${action}</td>
        </tr>`;
    }).join('');
    utils.renderIcons();
  },

  setupSearch() {
    const input = document.getElementById('contactsSearchInput');
    if (!input || input.dataset.ready === 'true') return;
    input.dataset.ready = 'true';
    input.addEventListener('input', () => {
      clearTimeout(this.searchTimer);
      this.searchTimer = setTimeout(() => this.load(input.value), 300);
    });
  },

  async openConversation(conversationId) {
    this.close();
    await chatModule.loadConversation(conversationId);
  },

  async download(endpoint, fallbackFilename) {
    try {
      const headers = { 'X-Requested-With': 'XMLHttpRequest' };
      const deviceId = api.getDeviceId();
      if (deviceId) headers['X-Device-ID'] = deviceId;
      const response = await fetch(`${api.baseUrl}${endpoint}`, {
        credentials: 'include',
        headers,
      });
      if (!response.ok) {
        let detail = `Error HTTP ${response.status}`;
        try { detail = api.parseErrorDetail((await response.json()).detail); } catch (_) {}
        throw new Error(detail);
      }
      const blob = await response.blob();
      const disposition = response.headers.get('content-disposition') || '';
      const match = disposition.match(/filename="?([^";]+)"?/i);
      const filename = match ? match[1] : fallbackFilename;
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = filename;
      document.body.appendChild(link);
      link.click();
      link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      utils.showToast(`✓ Archivo ${filename} descargado.`, 'success');
    } catch (error) {
      utils.showToast(`No se pudo exportar: ${error.message}`, 'error');
    }
  },

  exportContacts() {
    return this.download('/contacts/export.csv', 'contactos-farmhouse.csv');
  },

  exportChats() {
    return this.download('/contacts/chats.json', 'conversaciones-farmhouse.json');
  },
};
