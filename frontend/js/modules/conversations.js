/**
 * Farmhouse WhatsApp Center - Módulo de Listado de Conversaciones
 * Incluye soporte para Paginación (Punto 8) y Escape contra XSS (Punto 2)
 */

const conversationsModule = {
  conversations: [],
  selectedId: null,
  activeTab: 'abiertas',
  activeBranchId: null,
  searchQuery: '',
  currentPage: 0,
  pageSize: 50,
  hasMore: true,
  // IDs de conversaciones para las que ya se disparó la alerta "recuerda responder" en esta
  // pestaña, para no repetirla en cada refresco de 6s mientras siga pendiente. Se libera en
  // cuanto needs_reminder vuelve a false (se abrió el chat o alguien respondió), así puede
  // volver a alertar si el cliente escribe de nuevo más tarde.
  remindedIds: new Set(),

  async init() {
    this.setupListeners();
    await this.loadConversations();
  },

  setupListeners() {
    document.querySelectorAll('.tab-btn, .filter-pill').forEach(pill => {
      pill.addEventListener('click', (e) => {
        document.querySelectorAll('.tab-btn, .filter-pill').forEach(p => p.classList.remove('active'));
        e.currentTarget.classList.add('active');
        this.activeTab = e.currentTarget.dataset.tab;
        this.currentPage = 0;

        // En móvil, asegurarse de mostrar la columna de conversaciones
        const wsContainer = document.getElementById('workspaceContainer');
        if (wsContainer) {
          wsContainer.classList.remove('show-chat');
        }

        this.loadConversations();
      });
    });

    const searchInput = document.getElementById('searchInput');
    if (searchInput) {
      let debounceTimer;
      searchInput.addEventListener('input', (e) => {
        clearTimeout(debounceTimer);
        debounceTimer = setTimeout(() => {
          this.searchQuery = e.target.value.trim();
          this.currentPage = 0;
          this.loadConversations();
        }, 300);
      });
    }

    document.querySelectorAll('.sidebar .nav-btn[data-nav]').forEach(btn => {
      btn.addEventListener('click', (e) => {
        const nav = e.currentTarget.dataset.nav;
        if (nav === 'conversaciones') {
          this.activeTab = 'abiertas';
          this.activeBranchId = null;
        } else if (nav === 'no-asignadas') {
          this.activeTab = 'no-asignadas';
          this.activeBranchId = null;
        } else if (nav === 'todas') {
          this.activeTab = 'todas';
          this.activeBranchId = null;
        }
        document.querySelectorAll('.sidebar .nav-btn').forEach(b => b.classList.remove('active'));
        e.currentTarget.classList.add('active');
        const topSelect = document.getElementById('branchSelect');
        if (topSelect && !topSelect.disabled) topSelect.value = '';

        // Sincronizar tabs de filtro
        document.querySelectorAll('.tab-btn, .filter-pill').forEach(p => {
          p.classList.toggle('active', p.dataset.tab === this.activeTab);
        });

        // En móvil, asegurarse de mostrar la columna de conversaciones al cambiar de sección
        const wsContainer = document.getElementById('workspaceContainer');
        if (wsContainer) {
          wsContainer.classList.remove('show-chat');
        }

        this.currentPage = 0;
        this.loadConversations();
      });
    });
  },

  setBranchFilter(branchId) {
    this.activeBranchId = branchId;
    this.currentPage = 0;

    // En móvil, asegurarse de mostrar la columna de conversaciones
    const wsContainer = document.getElementById('workspaceContainer');
    if (wsContainer) {
      wsContainer.classList.remove('show-chat');
    }

    this.loadConversations();
  },

  // Número de la petición de lista más reciente: el refresco de 6 s, los eventos del WebSocket
  // y un cambio de pestaña pueden pedir la lista casi a la vez, y la respuesta de una pestaña
  // anterior que llegaba última pisaba la lista de la pestaña ya elegida.
  _loadSeq: 0,
  // Tope del backend para `limit` en GET /conversations/.
  MAX_LIMIT: 100,

  async loadConversations(append = false) {
    const seq = ++this._loadSeq;
    // Un refresco (append=false) trae desde el principio todo lo ya cargado. Antes pedía solo
    // la "página actual": después de "Cargar más", el siguiente refresco de 6 s reemplazaba la
    // lista entera por la página 2 sola.
    const skip = append ? this.currentPage * this.pageSize : 0;
    const limit = append ? this.pageSize : Math.min((this.currentPage + 1) * this.pageSize, this.MAX_LIMIT);
    try {
      let endpoint = `/conversations/?status=${encodeURIComponent(this.activeTab)}&skip=${skip}&limit=${limit}`;
      if (this.activeBranchId) {
        endpoint += `&branch_id=${this.activeBranchId}`;
      }
      if (this.searchQuery) {
        endpoint += `&search=${encodeURIComponent(this.searchQuery)}`;
      }

      const results = await api.get(endpoint);
      if (seq !== this._loadSeq) return this.conversations; // llegó otra más nueva

      if (append) {
        const known = new Set(this.conversations.map((c) => c.id));
        this.conversations = [...this.conversations, ...results.filter((c) => !known.has(c.id))];
      } else if (this.conversations.length > limit) {
        // Se habían cargado más de las que entran en un refresco (tope de 100): se actualiza el
        // principio y se conserva el resto ya cargado en vez de cortar la lista.
        const fresh = new Set(results.map((c) => c.id));
        this.conversations = [...results, ...this.conversations.slice(limit).filter((c) => !fresh.has(c.id))];
      } else {
        this.conversations = results;
      }

      this.hasMore = results.length === limit;
      this.checkReminders(results);
      this.renderList();
      branchesModule.updateCounters();
      return this.conversations;
    } catch (e) {
      console.error('Error cargando conversaciones:', e);
      return [];
    }
  },

  /**
   * Recorre el listado recién cargado y dispara la alerta "recuerda responder" (una sola
   * vez por conversación mientras siga pendiente) para las que el backend marcó con
   * needs_reminder (ver Conversation.needs_reminder en el backend).
   */
  checkReminders(list) {
    list.forEach(conv => {
      if (!conv.needs_reminder) {
        // Libera solo las que el servidor confirma que ya no están pendientes (se abrieron o se
        // respondieron), para que puedan volver a alertar si el cliente escribe de nuevo. Antes
        // se liberaba toda la que no apareciera en ESTA lista: al cambiar de pestaña, filtro o
        // búsqueda se "olvidaban" y al volver se repetían el modal y el sonido de cada una.
        this.remindedIds.delete(conv.id);
        return;
      }
      if (!this.remindedIds.has(conv.id)) {
        this.remindedIds.add(conv.id);
        notificationModule.notifyPendingReminder(conv);
      }
    });
  },

  renderList() {
    const listContainer = document.getElementById('conversationList');
    if (!listContainer) return;

    listContainer.innerHTML = '';

    if (this.conversations.length === 0) {
      listContainer.innerHTML = `
        <div class="empty-state">
          <div class="empty-icon"><i data-lucide="message-square-dashed"></i></div>
          <p>No hay conversaciones en esta bandeja.</p>
        </div>
      `;
      utils.renderIcons();
      return;
    }

    this.conversations.forEach(conv => {
      const item = document.createElement('div');
      item.className = 'conv-item';
      if (this.selectedId === conv.id) {
        item.classList.add('active');
      }
      if (conv.needs_reminder) {
        item.classList.add('needs-reminder');
      }

      const contactName = conv.contact ? conv.contact.name : 'Cliente';
      const contactPhone = conv.contact ? conv.contact.phone : '';
      const branchName = conv.branch ? conv.branch.name : 'Sin sucursal';
      const branchColor = conv.branch ? (conv.branch.color || '#16a34a') : '#94a3b8';
      const timeStr = utils.formatTime(conv.updated_at);
      const initials = utils.getInitials(contactName);
      const avatarColor = utils.getAvatarColor(contactName);

      // Estado
      let statusHtml = '';
      if (conv.status === 'unassigned' || conv.status === 'new') {
        statusHtml = '<span class="status-badge status-unassigned">No asignado</span>';
      } else if (conv.status === 'open') {
        statusHtml = '<span class="status-badge status-open">Abierto</span>';
      } else if (conv.status === 'pending') {
        statusHtml = '<span class="status-badge status-pending">Pendiente</span>';
      }
      if (conv.needs_reminder) {
        statusHtml += '<span class="status-badge status-reminder">⏰ Sin responder</span>';
      }

      // Preview seguro contra XSS (muestra el último mensaje real si existe)
      let previewText = '';
      if (conv.messages && conv.messages.length > 0) {
        const lastM = conv.messages[conv.messages.length - 1];
        if (lastM.is_internal) {
          previewText = `🔒 Nota: ${lastM.content}`;
        } else if (lastM.direction === 'outgoing') {
          previewText = `Tú: ${lastM.content}`;
        } else {
          previewText = lastM.content || 'Mensaje multimedia';
        }
      } else if (conv.assigned_user) {
        previewText = `Atendido por: ${conv.assigned_user.name}`;
      } else {
        previewText = contactPhone || 'Conversación iniciada';
      }

      item.dataset.id = conv.id;
      item.innerHTML = `
        <div class="conv-avatar" style="background:${avatarColor}22; color:${avatarColor}; border: 1.5px solid ${avatarColor}66">
          ${utils.escapeHtml(initials)}
        </div>
        <div class="conv-content">
          <div class="conv-top">
            <span class="conv-name">${utils.escapeHtml(contactName)}</span>
            <span class="conv-time">${utils.escapeHtml(timeStr)}</span>
          </div>
          <div class="conv-preview" title="${utils.escapeHtml(previewText)}">${utils.escapeHtml(previewText)}</div>
          <div class="conv-meta">
            <span class="conv-branch-tag" style="color:${utils.escapeHtml(branchColor)}">● ${utils.escapeHtml(branchName)}</span>
            ${statusHtml}
          </div>
        </div>
      `;

      // Fila navegable con teclado (Tab + Enter/Espacio), no solo con mouse.
      item.tabIndex = 0;
      item.setAttribute('role', 'button');
      item.setAttribute('aria-label', `Abrir conversación con ${contactName}`);
      item.addEventListener('click', () => {
        this.selectConversation(conv.id);
      });
      item.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault();
          this.selectConversation(conv.id);
        }
      });

      listContainer.appendChild(item);
    });

    // Botón para cargar más si hay más páginas
    if (this.hasMore) {
      const loadMoreBtn = document.createElement('button');
      loadMoreBtn.className = 'btn-load-more';
      loadMoreBtn.style.cssText = 'width:100%;padding:10px;font-size:12px;background:none;border:1px dashed var(--line);border-radius:6px;cursor:pointer;margin-top:8px;color:var(--text-muted);display:flex;align-items:center;justify-content:center;gap:6px';
      loadMoreBtn.innerHTML = '<i data-lucide="arrow-down"></i> <span>Cargar más conversaciones</span>';
      loadMoreBtn.addEventListener('click', () => {
        this.currentPage++;
        this.loadConversations(true);
      });
      listContainer.appendChild(loadMoreBtn);
    }

    utils.renderIcons();
  },

  /** Marca cuál conversación está abierta (también cuando se abre desde una notificación). */
  markSelected(convId) {
    if (this.selectedId === convId) return;
    this.selectedId = convId;
    document.querySelectorAll('.conv-item').forEach((el) => {
      el.classList.toggle('active', Number(el.dataset.id) === Number(convId));
    });
  },

  selectConversation(convId) {
    this.markSelected(convId);
    
    // Soporte para vista móvil (oculta lista y muestra chat en pantallas pequeñas)
    const wsContainer = document.getElementById('workspaceContainer');
    if (wsContainer) {
      wsContainer.classList.add('show-chat');
    }

    chatModule.loadConversation(convId);
  },

  highlightConversation(convId) {
    if (!convId) return;
    const item = document.querySelector(`.conv-item[data-id="${convId}"]`);
    if (item) {
      item.classList.remove('conv-pulse-alert');
      // Trigger reflow to restart animation
      void item.offsetWidth;
      item.classList.add('conv-pulse-alert');
      setTimeout(() => item.classList.remove('conv-pulse-alert'), 5000);
    }
  }
};
