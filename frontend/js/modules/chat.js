/**
 * Farmhouse WhatsApp Center - Módulo de Chat y Mensajería
 * Protección estricta contra XSS en mensajes y notas (Punto 2)
 */

const chatModule = {
  currentConversation: null,
  // conversationId de la petición GET /conversations/{id} más reciente que se disparó.
  // selectedConversationId (única fuente de verdad de "qué conversación quiere ver el
  // agente ahora mismo") — todo lo demás (header, chat, panel de contacto, pedido actual,
  // acciones rápidas) se deriva de currentConversation, que solo se sobreescribe cuando la
  // respuesta que llega corresponde exactamente a esta id.
  activeRequestConvId: null,
  isInternalNote: false,

  async loadConversation(convId) {
    // 1. Se fija esta conversación como "la que el agente quiere ver" ANTES de esperar la
    //    respuesta del servidor. Si el agente hace click en otra conversación mientras esta
    //    petición sigue en vuelo, activeRequestConvId cambia y la comprobación de abajo
    //    descarta la respuesta vieja en vez de sobreescribir el panel con datos de otro
    //    contacto — esta era la causa real del bug: dos fetch concurrentes (uno por cada
    //    click) podían resolver en cualquier orden, y el que terminaba último "ganaba"
    //    sin importar cuál conversación estaba seleccionada en ese momento.
    this.activeRequestConvId = convId;

    // 2. Limpia inmediatamente el panel derecho y el chat central: nunca debe verse el
    //    contacto/pedido anterior mientras carga el nuevo (Punto 5).
    this.renderLoadingState();

    let data;
    try {
      data = await api.get(`/conversations/${convId}`);
    } catch (e) {
      if (this.activeRequestConvId !== convId) return; // ya se seleccionó otra conversación
      console.error('Error cargando conversación:', e);
      utils.showToast(`Error: ${e.message}`, 'error');
      return;
    }

    // 3. Respuesta obsoleta (llegó después de que el agente ya cambió de conversación):
    //    se descarta por completo, sin tocar el estado ni el DOM.
    if (this.activeRequestConvId !== convId) return;

    this.currentConversation = data;
    this.renderHeader();
    this.renderMessages();
    this.renderOrderPanel();
    this.setupComposer();
  },

  renderLoadingState() {
    // Estado transitorio mientras se resuelve loadConversation(): usa los mismos IDs de
    // elemento que renderHeader/renderMessages/renderOrderPanel para que, en cuanto esos
    // vuelvan a ejecutarse con datos reales, los reemplacen sin dejar rastro del contacto
    // anterior. No es un rediseño: mismos textos/estructura, solo un valor "Cargando...".
    const header = document.getElementById('chatHeader');
    if (header && header.style.display !== 'none') {
      const chatName = document.getElementById('chatName');
      const chatPhone = document.getElementById('chatPhone');
      if (chatName) chatName.textContent = 'Cargando...';
      if (chatPhone) chatPhone.textContent = '';
    }

    const composer = document.getElementById('chatComposer');
    if (composer) composer.style.display = 'none';

    const messages = document.getElementById('chatMessages');
    if (messages) {
      messages.innerHTML = '<div style="text-align:center;padding:40px;color:var(--text-muted)">Cargando conversación...</div>';
    }

    const detailName = document.getElementById('detailName');
    const detailPhone = document.getElementById('detailPhone');
    const detailFirstSeen = document.getElementById('detailFirstSeen');
    const detailLastSeen = document.getElementById('detailLastSeen');
    const detailBranchTag = document.getElementById('detailBranchTag');
    const detailNotes = document.getElementById('detailNotes');
    if (detailName) detailName.textContent = 'Cargando...';
    if (detailPhone) detailPhone.textContent = '-';
    if (detailFirstSeen) detailFirstSeen.textContent = '-';
    if (detailLastSeen) detailLastSeen.textContent = '-';
    if (detailBranchTag) {
      detailBranchTag.textContent = '-';
      detailBranchTag.style.backgroundColor = '';
      detailBranchTag.style.color = '';
    }
    if (detailNotes) detailNotes.textContent = 'Cargando...';

    const orderLiveDot = document.getElementById('orderLiveDot');
    const orderItemsWrap = document.getElementById('orderItemsWrap');
    if (orderLiveDot) orderLiveDot.hidden = true;
    if (orderItemsWrap) orderItemsWrap.hidden = true;

    const oldDeliveryRow = document.getElementById('orderDeliveryFee')?.closest('.order-row');
    if (oldDeliveryRow) oldDeliveryRow.remove();

    const orderFieldDefaults = {
      orderId: 'Cargando...', orderBranch: '-', orderDeliveryType: '-', orderPaymentMethod: '-',
      orderType: '-', orderStatus: '-', orderSubtotal: '$0.00', orderTotal: '$0.00',
    };
    Object.entries(orderFieldDefaults).forEach(([id, value]) => {
      const el = document.getElementById(id);
      if (el) el.textContent = value;
    });
  },

  renderEmpty() {
    this.currentConversation = null;
    // Invalida cualquier loadConversation() todavía en vuelo: si llega tarde, ya no
    // coincidirá con activeRequestConvId y se descartará en vez de repoblar el panel.
    this.activeRequestConvId = null;
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

    // Red de seguridad: nunca pintar dos burbujas con el mismo ID, sin importar
    // por qué ruta (HTTP, WebSocket) haya llegado el mensaje duplicado.
    if (Array.isArray(this.currentConversation.messages)) {
      const seenIds = new Set();
      this.currentConversation.messages = this.currentConversation.messages.filter(msg => {
        if (!msg || !msg.id) return true;
        if (seenIds.has(msg.id)) return false;
        seenIds.add(msg.id);
        return true;
      });
    }

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
            <div class="msg-media-container">
              <img src="${utils.escapeHtml(mediaSrc)}"
                   alt="Imagen enviada por ${utils.escapeHtml(this.currentConversation.contact?.name || 'el cliente')}"
                   class="msg-media-image"
                   loading="lazy"
                   onclick="chatModule.openLightbox('${utils.escapeHtml(mediaSrc)}')"
                   onerror="chatModule.handleMediaImgError(this, ${msg.id})">
              <div class="msg-media-error-badge" hidden>
                <i data-lucide="image-off"></i>
                <span>No se pudo cargar esta imagen</span>
                <button type="button" class="btn-retry-media" onclick="chatModule.retryMedia(${msg.id})">Reintentar</button>
              </div>
            </div>`;
        } else if (msg.media_type === 'video' || /\.(mp4|webm|mov)$/i.test(msg.media_url)) {
          mediaHtml = `<video controls class="msg-media-video"><source src="${utils.escapeHtml(mediaSrc)}" type="${utils.escapeHtml(msg.media_mime_type || 'video/mp4')}"></video>`;
        } else if (msg.media_type === 'audio' || /\.(mp3|ogg|wav|m4a)$/i.test(msg.media_url)) {
          mediaHtml = `<audio controls class="msg-media-audio"><source src="${utils.escapeHtml(mediaSrc)}" type="${utils.escapeHtml(msg.media_mime_type || 'audio/mpeg')}"></audio>`;
        } else {
          mediaHtml = `<a href="${utils.escapeHtml(mediaSrc)}" target="_blank" rel="noopener" class="msg-media-file-link"><i data-lucide="paperclip"></i> Descargar archivo adjunto</a>`;
        }
      } else if (msg.media_type) {
        const mediaTypeLabels = { image: 'imagen', video: 'video', audio: 'audio', document: 'documento', sticker: 'sticker' };
        const label = mediaTypeLabels[msg.media_type] || 'archivo';
        if (msg.error_detail === 'media_download_failed') {
          mediaHtml = `
            <div class="msg-media-error-badge">
              <i data-lucide="image-off"></i>
              <span>No se pudo cargar esta ${label}</span>
              <button type="button" class="btn-retry-media" onclick="chatModule.retryMedia(${msg.id})">Reintentar</button>
            </div>`;
        } else {
          mediaHtml = `
            <div class="msg-media-skeleton" aria-live="polite">
              <span class="msg-media-spinner"></span>
              <span>Descargando ${label} de WhatsApp...</span>
            </div>`;
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

    // 2. Actualizar Bloque de Pedido Actual (carrito activo del Menú Digital en tiempo real,
    //    o el último pedido confirmado). conv.orders ya viene ordenado del más reciente al más
    //    antiguo (ver models/conversation.py), así que orders[0] siempre es "lo vigente" — salvo
    //    que sea un carrito abandonado, en cuyo caso se busca el siguiente relevante.
    const oldDeliveryRow = document.getElementById('orderDeliveryFee')?.closest('.order-row');
    if (oldDeliveryRow) oldDeliveryRow.remove();

    const orderId = document.getElementById('orderId');
    const orderBranch = document.getElementById('orderBranch');
    const orderDeliveryType = document.getElementById('orderDeliveryType');
    const orderPaymentMethod = document.getElementById('orderPaymentMethod');
    const orderType = document.getElementById('orderType');
    const orderStatus = document.getElementById('orderStatus');
    const orderSubtotal = document.getElementById('orderSubtotal');
    const orderTotal = document.getElementById('orderTotal');
    const orderLiveDot = document.getElementById('orderLiveDot');
    const orderItemsWrap = document.getElementById('orderItemsWrap');
    const orderItemsCount = document.getElementById('orderItemsCount');
    const orderItemsList = document.getElementById('orderItemsList');

    const DELIVERY_LABELS = { delivery: '🛵 Delivery', pickup: '🏠 Retiro en el local' };
    const PAYMENT_LABELS = { card: '💳 Tarjeta', yappy: '📱 Yappy', cash: '💵 Efectivo', ach: '🏦 ACH' };
    const STATUS_LABELS = {
      carrito_activo: 'Armando pedido', en_proceso: 'En proceso', en_cocina: 'En cocina',
      en_delivery: 'En camino', entregado: 'Entregado', cancelado: 'Cancelado', abandonado: 'Carrito abandonado',
    };

    const orders = conv.orders || [];
    const current = orders.find(o => o.status !== 'abandonado') || null;
    const isDraft = !!current && current.status === 'carrito_activo';

    if (orderLiveDot) orderLiveDot.hidden = !isDraft;

    let itemsData = null;
    if (current && current.items_json) {
      try { itemsData = JSON.parse(current.items_json); } catch (e) { itemsData = null; }
    }

    // Entrega/pago: mientras el cliente arma el carrito, la fuente de verdad es el carrito
    // mismo (más reciente); si aún no hay ninguno, se cae al último valor conocido en la
    // conversación (fijado por el bot de WhatsApp o por un pedido confirmado anterior).
    const deliveryType = current ? (current.order_type === 'delivery' ? 'delivery' : 'pickup') : conv.delivery_type;
    const paymentMethod = (itemsData && itemsData.payment_method) || conv.payment_method;

    if (orderDeliveryType) orderDeliveryType.textContent = deliveryType ? (DELIVERY_LABELS[deliveryType] || deliveryType) : '-';
    if (orderPaymentMethod) orderPaymentMethod.textContent = paymentMethod ? (PAYMENT_LABELS[paymentMethod] || paymentMethod) : '-';

    if (!current) {
      if (orderId) orderId.textContent = 'Sin pedido activo';
      if (orderBranch) orderBranch.textContent = branch.name || '-';
      if (orderType) orderType.textContent = '-';
      if (orderStatus) orderStatus.textContent = 'N/A';
      if (orderItemsWrap) orderItemsWrap.hidden = true;
      if (orderSubtotal) orderSubtotal.textContent = '$0.00';
      if (orderTotal) orderTotal.textContent = '$0.00';
      return;
    }

    if (orderId) orderId.textContent = isDraft ? 'Carrito activo' : `#${current.order_code || current.id}`;
    if (orderBranch) orderBranch.textContent = branch.name || '-';
    if (orderType) {
      const source = itemsData && itemsData.source;
      orderType.textContent = (source && String(source).startsWith('menu_web')) ? '🌐 Menú Digital' : '💬 WhatsApp';
    }
    if (orderStatus) orderStatus.textContent = STATUS_LABELS[current.status] || current.status;

    const items = (itemsData && itemsData.items) || [];
    if (orderItemsWrap && orderItemsCount && orderItemsList) {
      if (items.length > 0) {
        orderItemsWrap.hidden = false;
        orderItemsCount.textContent = `${items.length} producto${items.length === 1 ? '' : 's'}`;
        orderItemsList.innerHTML = items.map((it) => `
          <div class="order-item-row">
            <div class="order-item-row-main">
              <span>${it.quantity}x ${utils.escapeHtml(it.title || '')}</span>
              <span>$${Number(it.line_total || 0).toFixed(2)}</span>
            </div>
            ${(it.addons && it.addons.length) ? `<div class="order-item-addons">${it.addons.map((a) => `+ ${utils.escapeHtml(a.title || '')}`).join('<br>')}</div>` : ''}
            ${it.notes ? `<div class="order-item-addons">Nota: ${utils.escapeHtml(it.notes)}</div>` : ''}
          </div>
        `).join('');
      } else {
        orderItemsWrap.hidden = true;
      }
    }

    let subtotalNum = Number(current.subtotal ?? 0);
    let deliveryNum = Number(current.delivery_cost ?? 0);
    let totalNum = Number(current.total ?? 0);

    // Fallback 1: Si subtotal es 0 pero itemsData tiene items, calcular de los items
    if (subtotalNum === 0 && items && items.length > 0) {
      subtotalNum = items.reduce((acc, it) => acc + (Number(it.line_total || 0)), 0);
    }

    // Fallback 2: Si total es 0 pero tenemos subtotal, sumar delivery
    if (totalNum === 0 && subtotalNum > 0) {
      totalNum = subtotalNum + deliveryNum;
    }

    // Fallback 3: Extraer monto del texto de mensajes si vino con "TOTAL: $XX.XX"
    if (totalNum === 0 && conv.messages && conv.messages.length > 0) {
      for (const m of conv.messages) {
        if (m.content && m.content.includes("TOTAL: $")) {
          const match = m.content.match(/TOTAL:\s*\$([0-9]+(?:\.[0-9]{2})?)/i);
          if (match && match[1]) {
            totalNum = parseFloat(match[1]);
            if (subtotalNum === 0) subtotalNum = Math.max(0, totalNum - deliveryNum);
            break;
          }
        }
      }
    }

    if (orderSubtotal) orderSubtotal.textContent = `$${subtotalNum.toFixed(2)}`;
    if (orderTotal) orderTotal.textContent = `$${totalNum.toFixed(2)}`;
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
      // El WebSocket (new_outgoing_message) puede insertar este mismo mensaje
      // antes de que esta promesa se resuelva; evitar duplicarlo en pantalla.
      const yaExiste = this.currentConversation.messages.some(m => m.id === newMsg.id);
      if (!yaExiste) {
        this.currentConversation.messages.push(newMsg);
        this.renderMessages();
      }

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

  handleMediaImgError(imgEl, messageId) {
    // El archivo se descargó y se guardó en el servidor, pero el navegador no pudo cargarlo
    // ahora (servidor reiniciado sin persistir /media, red, etc.). Se muestra el mismo estado
    // de error + Reintentar que usamos cuando la descarga desde Meta falla, en vez de un ícono
    // roto de navegador.
    imgEl.style.display = 'none';
    const badge = imgEl.nextElementSibling;
    if (badge) badge.hidden = false;
    utils.renderIcons();
  },

  async retryMedia(messageId) {
    try {
      const updatedMsg = await api.post(`/messages/${messageId}/retry-media`, {});
      if (this.currentConversation && this.currentConversation.messages) {
        const idx = this.currentConversation.messages.findIndex(m => m.id === messageId);
        if (idx !== -1) {
          this.currentConversation.messages[idx] = updatedMsg;
          this.renderMessages();
        }
      }
    } catch (e) {
      utils.showToast('No se pudo cargar el archivo. Intenta de nuevo en unos minutos.', 'error');
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


