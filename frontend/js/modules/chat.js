/**
 * Farmhouse WhatsApp Center - Módulo de Chat y Mensajería
 * Protección estricta contra XSS en mensajes y notas (Punto 2)
 */

const chatModule = {
  currentConversation: null,
  isInternalNote: false,

  async loadConversation(convId) {
    try {
      this.currentConversation = await api.get(`/conversations/${convId}`);
      this.renderHeader();
      this.renderMessages();
      this.renderOrderPanel();
      this.setupComposer();
    } catch (e) {
      console.error('Error cargando conversación:', e);
      utils.showToast(`Error: ${e.message}`, 'error');
    }
  },

  renderEmpty() {
    this.currentConversation = null;
    const header = document.getElementById('chatHeader');
    const messages = document.getElementById('chatMessages');
    const composer = document.getElementById('chatComposer');

    if (header) {
      header.style.display = 'none';
      header.innerHTML = '';
    }
    if (messages) {
      messages.innerHTML = `
        <div class="empty-state">
          <div class="empty-icon"><i data-lucide="sprout"></i></div>
          <p>Bandeja de atención Farmhouse</p>
          <span style="font-size:12px;color:var(--text-muted)">Selecciona una conversación para comenzar a responder</span>
        </div>
      `;
    }
    if (composer) composer.style.display = 'none';
    utils.renderIcons();
  },

  renderHeader() {
    const header = document.getElementById('chatHeader');
    if (!header || !this.currentConversation) return;

    header.style.display = 'flex';
    const conv = this.currentConversation;
    const contact = conv.contact || { name: 'Cliente', phone: '' };
    const branch = conv.branch || { name: 'Sin sucursal (pendiente)', color: '#94a3b8' };
    const user = auth.getUser();
    const assignedName = conv.assigned_user ? conv.assigned_user.name : 'Sin asignar';

    let actionBtnHtml = '';
    if (!conv.assigned_user_id) {
      actionBtnHtml = `<button class="btn-primary" id="btnTakeConv" onclick="chatModule.takeConversation()"><i data-lucide="user-check"></i> <span>Tomar Conversación</span></button>`;
    } else if (user && user.id === conv.assigned_user_id) {
      actionBtnHtml = `<button class="btn-sm-action" style="color:var(--green);border-color:var(--green-border);display:inline-flex;align-items:center;gap:4px"><i data-lucide="check"></i> <span>En atención por ti</span></button>`;
    } else {
      actionBtnHtml = `<span style="font-size:12px;color:var(--text-muted)">Atendido por: <strong>${utils.escapeHtml(assignedName)}</strong></span>`;
    }

    let deleteBtnHtml = '';
    if (user && (user.role === 'admin' || user.role === 'supervisor')) {
      deleteBtnHtml = `<button class="btn-sm-action" style="color:#ef4444;border-color:#ef4444" onclick="chatModule.deleteConversation()" title="Eliminar conversación definitivamente"><i data-lucide="trash-2"></i> <span>Eliminar</span></button>`;
    }

    header.innerHTML = `
      <button class="btn-mobile-back" id="btnMobileBack" aria-label="Volver a conversaciones" title="Volver">
        <i data-lucide="chevron-left"></i>
      </button>
      <div class="avatar-circle" id="chatAvatar" style="background:${utils.getAvatarColor(contact.name)}22; color:${utils.getAvatarColor(contact.name)}; border-color:${utils.getAvatarColor(contact.name)}55">${utils.escapeHtml(utils.getInitials(contact.name))}</div>
      <div class="meta">
        <strong id="chatName">${utils.escapeHtml(contact.name)}</strong>
        <span id="chatPhone">${utils.escapeHtml(contact.phone)}</span>
      </div>
      <div class="spacer"></div>
      <button class="header-chip branch" id="chipBranch" style="border-color:${utils.escapeHtml(branch.color || '#16a34a')}">
        ● ${utils.escapeHtml(branch.name)}
      </button>
      <button class="header-chip progress" id="chipStatus">● ${utils.escapeHtml((conv.status || 'abierto').toUpperCase())} ▾</button>
      <button class="header-chip btn-toggle-details" id="btnToggleDetail" title="Ver información del cliente / pedido" aria-label="Detalles de conversación">
        <i data-lucide="panel-right"></i>
      </button>
      <div class="chat-header-actions">
        ${actionBtnHtml}
        <button class="btn-header-more" id="btnHeaderMore" aria-label="Más acciones" title="Más acciones">
          <i data-lucide="more-vertical"></i>
        </button>
        <div class="chat-secondary-actions" id="chatSecondaryActions">
          <button class="btn-sm-action" onclick="chatModule.openTransferModal()" title="Transferir a otra sucursal"><i data-lucide="repeat"></i> Transferir</button>
          <button class="btn-sm-action" onclick="chatModule.closeConversation()" title="Cerrar conversación"><i data-lucide="check-circle-2"></i> Resolver</button>
          ${deleteBtnHtml}
        </div>
      </div>
    `;

    // Conectar botón de volver en móvil
    const btnBack = document.getElementById('btnMobileBack');
    if (btnBack) {
      btnBack.addEventListener('click', () => {
        document.getElementById('workspaceContainer')?.classList.remove('show-chat');
      });
    }

    // Conectar menú "más acciones" (⋮) que en móvil agrupa Transferir/Resolver/Eliminar
    const btnMore = document.getElementById('btnHeaderMore');
    if (btnMore) {
      btnMore.addEventListener('click', (e) => {
        e.stopPropagation();
        const menu = document.getElementById('chatSecondaryActions');
        if (!menu) return;
        // position: fixed calculado desde el botón, para escapar del recorte por
        // overflow-x:auto del encabezado (el menú no cabría si fuera absolute).
        const rect = btnMore.getBoundingClientRect();
        menu.style.top = `${rect.bottom + 6}px`;
        menu.style.right = `${window.innerWidth - rect.right}px`;
        menu.classList.toggle('open');
      });
    }
    // Listener global (una sola vez) para cerrar el menú al tocar fuera de él
    if (!this._secondaryActionsOutsideClickBound) {
      this._secondaryActionsOutsideClickBound = true;
      document.addEventListener('click', (e) => {
        const menu = document.getElementById('chatSecondaryActions');
        const trigger = document.getElementById('btnHeaderMore');
        if (menu && menu.classList.contains('open') && !menu.contains(e.target) && e.target !== trigger) {
          menu.classList.remove('open');
        }
      });
    }

    // Conectar botón de panel deslizable en tablet / móvil
    const btnToggle = document.getElementById('btnToggleDetail');
    if (btnToggle) {
      btnToggle.addEventListener('click', () => {
        document.querySelector('.panel-details')?.classList.toggle('active');
      });
    }

    utils.renderIcons();
  },

  renderMessages() {
    const container = document.getElementById('chatMessages');
    if (!container || !this.currentConversation) return;

    container.innerHTML = '';
    const messages = this.currentConversation.messages || [];
    const user = auth.getUser();
    const canDeleteMessages = user && (user.role === 'admin' || user.role === 'supervisor');

    if (messages.length === 0) {
      container.innerHTML = '<div style="text-align:center;padding:40px;color:var(--text-muted)">No hay mensajes anteriores en esta conversación.</div>';
      return;
    }

    messages.forEach(msg => {
      const msgDiv = document.createElement('div');
      const isOutgoing = msg.direction === 'outgoing';
      const isInternal = msg.is_internal || false;

      if (isInternal) {
        msgDiv.className = 'msg-bubble msg-internal';
      } else if (isOutgoing) {
        msgDiv.className = 'msg-bubble msg-out';
      } else {
        msgDiv.className = 'msg-bubble msg-in';
      }

      const timeStr = utils.formatTime(msg.created_at);
      const senderLabel = isInternal
        ? '<i data-lucide="lock" style="width:12px;height:12px;display:inline-block;vertical-align:middle"></i> Nota Interna de Agente'
        : (isOutgoing ? 'Farmhouse Panamá' : (this.currentConversation.contact?.name || 'Cliente'));

      let statusBadge = '';
      if (isOutgoing && !isInternal) {
        if (msg.status === 'pending') {
          statusBadge = '<span class="msg-status" title="Enviando..."><i data-lucide="clock" style="width:12px;height:12px;display:inline-block"></i></span>';
        } else if (msg.status === 'sent') {
          statusBadge = '<span class="msg-status" title="Enviado a WhatsApp"><i data-lucide="check" style="width:12px;height:12px;display:inline-block"></i></span>';
        } else if (msg.status === 'delivered') {
          statusBadge = '<span class="msg-status" title="Entregado al cliente"><i data-lucide="check-check" style="width:12px;height:12px;display:inline-block"></i></span>';
        } else if (msg.status === 'read') {
          statusBadge = '<span class="msg-status" style="color:#0284c7" title="Leído por el cliente"><i data-lucide="check-check" style="width:12px;height:12px;display:inline-block"></i></span>';
        } else if (msg.status === 'failed') {
          statusBadge = `<button class="btn-retry-msg" onclick="chatModule.retryMessage(${msg.id})" title="Error al enviar: ${utils.escapeHtml(msg.error_detail || 'Fallo de entrega')}" style="background:none;border:none;color:#ef4444;font-size:11px;cursor:pointer;display:inline-flex;align-items:center;gap:3px;margin-left:4px"><i data-lucide="alert-circle" style="width:12px;height:12px"></i> Reintentar</button>`;
        }
      }

      let mediaHtml = '';
      const isMediaPlaceholder = msg.content && (
        msg.content === '📷 Imagen' ||
        msg.content === '📷 Foto' ||
        msg.content === '[image]' ||
        msg.content === '🎥 Video' ||
        msg.content === '[video]' ||
        msg.content === '🎵 Audio' ||
        msg.content === '[audio]'
      );

      if (msg.media_url) {
        const mediaSrc = api.resolveMediaUrl(msg.media_url);
        const isImage = msg.media_type === 'image' || msg.media_type === 'sticker' ||
          /\.(jpg|jpeg|png|webp|gif)$/i.test(msg.media_url);

        if (isImage) {
          mediaHtml = `
            <div class="msg-media-container" style="margin-top:6px">
              <img src="${utils.escapeHtml(mediaSrc)}" 
                   alt="Imagen adjunta" 
                   class="msg-media-image" 
                   loading="lazy"
                   style="max-width:280px;max-height:280px;border-radius:8px;display:block;cursor:zoom-in;object-fit:cover;border:1px solid rgba(255,255,255,0.1);transition:transform 0.2s ease, box-shadow 0.2s ease" 
                   onclick="chatModule.openLightbox('${utils.escapeHtml(mediaSrc)}')"
                   onmouseover="this.style.transform='scale(1.02)';this.style.boxShadow='0 8px 16px rgba(0,0,0,0.3)'"
                   onmouseout="this.style.transform='scale(1)';this.style.boxShadow='none'">
            </div>`;
        } else if (msg.media_type === 'video' || /\.(mp4|webm|mov)$/i.test(msg.media_url)) {
          mediaHtml = `<video controls style="max-width:280px;border-radius:8px;margin-top:6px;display:block"><source src="${utils.escapeHtml(mediaSrc)}" type="${utils.escapeHtml(msg.media_mime_type || 'video/mp4')}"></video>`;
        } else if (msg.media_type === 'audio' || /\.(mp3|ogg|wav|m4a)$/i.test(msg.media_url)) {
          mediaHtml = `<audio controls style="margin-top:6px;display:block"><source src="${utils.escapeHtml(mediaSrc)}" type="${utils.escapeHtml(msg.media_mime_type || 'audio/mpeg')}"></audio>`;
        } else {
          mediaHtml = `<a href="${utils.escapeHtml(mediaSrc)}" target="_blank" rel="noopener" style="display:inline-flex;align-items:center;gap:4px;margin-top:6px;font-size:12px;color:var(--green)"><i data-lucide="paperclip"></i> Descargar archivo adjunto</a>`;
        }
      }

      const textHtml = (msg.content && (!isMediaPlaceholder || !msg.media_url))
        ? `<div class="msg-text">${utils.escapeHtml(msg.content)}</div>`
        : '';

      const deleteMsgBtn = canDeleteMessages
        ? `<button class="btn-delete-msg" onclick="chatModule.deleteMessage(${msg.id})" title="Borrar mensaje" aria-label="Borrar mensaje"><i data-lucide="trash-2"></i></button>`
        : '';

      msgDiv.innerHTML = `
        ${deleteMsgBtn}
        <div class="msg-sender">${senderLabel}</div>
        ${textHtml}
        ${mediaHtml}
        <div class="msg-time" style="display:flex;align-items:center;justify-content:flex-end;gap:4px">
          <span>${utils.escapeHtml(timeStr)}</span>
          ${statusBadge}
        </div>
      `;

      container.appendChild(msgDiv);
    });

    // Auto-scroll al último mensaje
    container.scrollTop = container.scrollHeight;
    utils.renderIcons();
  },


  renderOrderPanel() {
    if (!this.currentConversation) return;

    const conv = this.currentConversation;
    const contact = conv.contact || {};
    const branch = conv.branch || {};

    // 1. Actualizar Información del Contacto en Columna 3
    const detailName = document.getElementById('detailName');
    const detailPhone = document.getElementById('detailPhone');
    const detailFirstSeen = document.getElementById('detailFirstSeen');
    const detailLastSeen = document.getElementById('detailLastSeen');
    const detailBranchTag = document.getElementById('detailBranchTag');
    const detailNotes = document.getElementById('detailNotes');

    if (detailName) detailName.textContent = contact.name || 'Cliente';
    if (detailPhone) detailPhone.textContent = contact.phone || '-';
    if (detailFirstSeen) detailFirstSeen.textContent = contact.created_at ? utils.formatDate(contact.created_at) : '-';
    if (detailLastSeen) detailLastSeen.textContent = conv.updated_at ? utils.formatTime(conv.updated_at) : '-';
    if (detailBranchTag) {
      detailBranchTag.textContent = branch.name || 'Sin sucursal';
      detailBranchTag.style.backgroundColor = branch.color ? `${branch.color}22` : 'var(--blue-light)';
      detailBranchTag.style.color = branch.color || 'var(--blue)';
    }
    if (detailNotes) {
      detailNotes.textContent = conv.notes || 'Sin notas registradas para esta conversación.';
    }

    // 2. Actualizar Bloque de Pedido Actual
    const orderId = document.getElementById('orderId');
    const orderBranch = document.getElementById('orderBranch');
    const orderDeliveryType = document.getElementById('orderDeliveryType');
    const orderPaymentMethod = document.getElementById('orderPaymentMethod');
    const orderType = document.getElementById('orderType');
    const orderStatus = document.getElementById('orderStatus');
    const orderSubtotal = document.getElementById('orderSubtotal');

    const deliveryLabels = { delivery: '🛵 Delivery', pickup: '🏠 Retiro en el local' };
    const paymentLabels = { card: '💳 Tarjeta', yappy: '📱 Yappy', cash: '💵 Efectivo', ach: '🏦 ACH' };
    const deliveryText = conv.delivery_type ? (deliveryLabels[conv.delivery_type] || conv.delivery_type) : '-';
    const paymentText = conv.payment_method ? (paymentLabels[conv.payment_method] || conv.payment_method) : '-';

    if (orderDeliveryType) orderDeliveryType.textContent = deliveryText;
    if (orderPaymentMethod) orderPaymentMethod.textContent = paymentText;

    const orders = conv.orders || [];
    if (orders.length > 0) {
      const order = orders[0];
      if (orderId) orderId.textContent = `#${order.order_code || order.id}`;
      if (orderBranch) orderBranch.textContent = branch.name || '-';
      if (orderType) orderType.textContent = order.order_type || 'WhatsApp';
      if (orderStatus) orderStatus.textContent = order.status || 'En Proceso';
      if (orderSubtotal) orderSubtotal.textContent = `$${(order.subtotal || order.total_amount || 0).toFixed(2)}`;
    } else {
      if (orderId) orderId.textContent = '-';
      if (orderBranch) orderBranch.textContent = branch.name || '-';
      if (orderType) orderType.textContent = 'Sin comanda';
      if (orderStatus) orderStatus.textContent = 'N/A';
      if (orderSubtotal) orderSubtotal.textContent = '$0.00';
    }
  },

  setupComposer() {
    const composer = document.getElementById('chatComposer');
    if (composer) composer.style.display = 'block';

    const tabMsg = document.getElementById('tabMsg');
    const tabInternal = document.getElementById('tabInternal');
    const msgInput = document.getElementById('messageInput');
    const btnSend = document.getElementById('btnSend');

    if (tabMsg && tabInternal) {
      tabMsg.onclick = () => {
        this.isInternalNote = false;
        tabMsg.classList.add('active');
        tabInternal.classList.remove('active');
        if (msgInput) {
          msgInput.placeholder = 'Escribe un mensaje para WhatsApp...';
          msgInput.focus();
        }
      };
      tabInternal.onclick = () => {
        this.isInternalNote = true;
        tabInternal.classList.add('active');
        tabMsg.classList.remove('active');
        if (msgInput) {
          msgInput.placeholder = 'Escribe una nota interna (solo visible para el equipo)...';
          msgInput.focus();
        }
      };
    }

    if (btnSend) {
      btnSend.onclick = () => {
        const input = document.getElementById('messageInput');
        if (input && input.value.trim()) {
          this.sendMessage(input.value);
        }
      };
    }

    if (msgInput) {
      msgInput.onkeydown = (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
          e.preventDefault();
          if (msgInput.value.trim()) {
            this.sendMessage(msgInput.value);
          }
        }
      };
    }
  },

  async sendMessage(text) {
    if (!this.currentConversation || !text.trim()) return;

    try {
      const payload = {
        conversation_id: this.currentConversation.id,
        content: text.trim(),
        is_internal: this.isInternalNote
      };

      const newMsg = await api.post('/messages/', payload);
      if (!this.currentConversation.messages) {
        this.currentConversation.messages = [];
      }
      this.currentConversation.messages.push(newMsg);
      this.renderMessages();

      const input = document.getElementById('messageInput');
      if (input) input.value = '';

      conversationsModule.loadConversations();
    } catch (e) {
      utils.showToast(`Error enviando mensaje: ${e.message}`, 'error');
    }
  },

  async takeConversation() {
    if (!this.currentConversation) return;
    try {
      const updated = await api.post(`/conversations/${this.currentConversation.id}/take`, {});
      this.currentConversation = updated;
      this.renderHeader();
      conversationsModule.loadConversations();
      utils.showToast('✓ Conversación tomada exitosamente.', 'success');
    } catch (e) {
      utils.showToast(`Error tomando conversación: ${e.message}`, 'error');
    }
  },

  openTransferModal() {
    if (!this.currentConversation) return;
    document.getElementById('modalTransferBranch').classList.add('active');
  },

  async confirmTransfer() {
    if (!this.currentConversation) return;
    const targetBranchId = document.getElementById('transferTargetBranch').value;
    const reason = document.getElementById('transferReason').value;

    if (!targetBranchId) {
      utils.showToast('Por favor selecciona una sucursal destino.', 'error');
      return;
    }

    try {
      await api.post(`/conversations/${this.currentConversation.id}/transfer`, {
        target_branch_id: parseInt(targetBranchId),
        reason: reason.trim()
      });
      document.getElementById('modalTransferBranch').classList.remove('active');
      this.renderEmpty();
      conversationsModule.loadConversations();
      utils.showToast('✓ Conversación transferida con éxito.', 'success');
    } catch (e) {
      utils.showToast(`Error transferiendo conversación: ${e.message}`, 'error');
    }
  },

  async closeConversation() {
    if (!this.currentConversation) return;
    if (confirm('¿Deseas marcar esta conversación como resuelta/cerrada?')) {
      try {
        await api.put(`/conversations/${this.currentConversation.id}/status?new_status=closed`, {});
        this.renderEmpty();
        conversationsModule.loadConversations();
        utils.showToast('✓ Conversación cerrada.', 'info');
      } catch (e) {
        utils.showToast(`Error cerrando conversación: ${e.message}`, 'error');
      }
    }
  },

  async deleteConversation() {
    if (!this.currentConversation) return;
    const nombreCliente = this.currentConversation.contact?.name || 'este cliente';
    const confirmado = confirm(
      `¿Seguro que quieres BORRAR PERMANENTEMENTE la conversación con ${nombreCliente}?\n\nEsta acción no se puede deshacer: se perderán todos los mensajes y fotos de esta conversación.`
    );
    if (!confirmado) return;

    try {
      await api.delete(`/conversations/${this.currentConversation.id}`);
      utils.showToast('✓ Conversación eliminada definitivamente.', 'info');
      this.renderEmpty();
      conversationsModule.loadConversations();
    } catch (e) {
      utils.showToast(`Error eliminando conversación: ${e.message}`, 'error');
    }
  },

  async deleteMessage(messageId) {
    if (!confirm('¿Deseas eliminar este mensaje permanentemente?')) return;
    try {
      await api.delete(`/messages/${messageId}`);
      if (this.currentConversation && this.currentConversation.messages) {
        this.currentConversation.messages = this.currentConversation.messages.filter(m => m.id !== messageId);
        this.renderMessages();
      }
      utils.showToast('✓ Mensaje eliminado correctamente.', 'info');
    } catch (e) {
      utils.showToast(`Error al eliminar mensaje: ${e.message}`, 'error');
    }
  },

  async retryMessage(messageId) {
    try {
      utils.showToast('Reintentando envío de mensaje...', 'info');
      const updatedMsg = await api.post(`/messages/${messageId}/retry`, {});
      if (this.currentConversation && this.currentConversation.messages) {
        const idx = this.currentConversation.messages.findIndex(m => m.id === messageId);
        if (idx !== -1) {
          this.currentConversation.messages[idx] = updatedMsg;
          this.renderMessages();
        }
      }
      utils.showToast('✓ Mensaje enviado exitosamente.', 'success');
    } catch (e) {
      utils.showToast(`Error en reintento: ${e.message}`, 'error');
    }
  },

  async toggleAutomation() {
    if (!this.currentConversation) return;
    try {
      const res = await api.post(`/conversations/${this.currentConversation.id}/toggle-automation`, {});
      this.currentConversation.automation_paused = res.automation_paused;
      const state = res.automation_paused ? 'pausada' : 'reanudada';
      utils.showToast(`✓ Respuestas automáticas del bot ${state}.`, 'info');
    } catch (e) {
      utils.showToast(`Error al cambiar automatización: ${e.message}`, 'error');
    }
  },

  openLightbox(mediaSrc) {
    const modal = document.getElementById('modalImageLightbox');
    const img = document.getElementById('lightboxImage');
    const downloadBtn = document.getElementById('lightboxDownloadBtn');
    if (!modal || !img) return;

    img.src = mediaSrc;
    if (downloadBtn) {
      downloadBtn.href = mediaSrc;
    }
    modal.style.display = 'flex';
    utils.renderIcons();

    const onKeyDown = (e) => {
      if (e.key === 'Escape') {
        this.closeLightbox();
        document.removeEventListener('keydown', onKeyDown);
      }
    };
    document.addEventListener('keydown', onKeyDown);
  },

  closeLightbox(e) {
    if (e && e.target && e.target.id !== 'modalImageLightbox' && !e.target.closest('.lightbox-close-btn')) {
      return;
    }
    const modal = document.getElementById('modalImageLightbox');
    const img = document.getElementById('lightboxImage');
    if (modal) {
      modal.style.display = 'none';
      if (img) img.src = '';
    }
  }
};


