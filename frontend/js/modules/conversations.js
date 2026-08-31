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

        // Sincronizar tabs de filtro
        document.querySelectorAll('.tab-btn, .filter-pill').forEach(p => {
          p.classList.toggle('active', p.dataset.tab === this.activeTab);
        });

        this.currentPage = 0;
        this.loadConversations();
      });
    });
  },

  setBranchFilter(branchId) {
    this.activeBranchId = branchId;
    this.currentPage = 0;
    this.loadConversations();
  },

  async loadConversations(append = false) {
    try {
      let endpoint = `/conversations/?status=${encodeURIComponent(this.activeTab)}&skip=${this.currentPage * this.pageSize}&limit=${this.pageSize}`;
      if (this.activeBranchId) {
        endpoint += `&branch_id=${this.activeBranchId}`;
      }
      if (this.searchQuery) {
        endpoint += `&search=${encodeURIComponent(this.searchQuery)}`;
      }

      const results = await api.get(endpoint);
      if (append) {
        this.conversations = [...this.conversations, ...results];
      } else {
        this.conversations = results;
      }

      this.hasMore = results.length === this.pageSize;
      this.renderList();
      branchesModule.updateCounters();
      return this.conversations;
    } catch (e) {
      console.error('Error cargando conversaciones:', e);
      return [];
    }
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

      // Preview seguro contra XSS
      const previewText = conv.assigned_user ? `Atendido por: ${conv.assigned_user.name}` : (contactPhone || 'Conversación activa');

      item.innerHTML = `
        <div class="conv-avatar" style="border-left: 3px solid ${utils.escapeHtml(branchColor)}; background:${avatarColor}22; color:${avatarColor}">
          ${utils.escapeHtml(initials)}
        </div>
        <div class="conv-content">
          <div class="conv-top">
            <span class="conv-name">${utils.escapeHtml(contactName)}</span>
            <span class="conv-time">${utils.escapeHtml(timeStr)}</span>
          </div>
          <div class="conv-preview">${utils.escapeHtml(previewText)}</div>
          <div class="conv-meta">
            <span class="conv-branch-tag" style="color:${utils.escapeHtml(branchColor)}">● ${utils.escapeHtml(branchName)}</span>
            ${statusHtml}
          </div>
        </div>
      `;

      item.addEventListener('click', () => {
        this.selectConversation(conv.id);
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

  selectConversation(convId) {
    this.selectedId = convId;
    document.querySelectorAll('.conv-item').forEach(el => el.classList.remove('active'));
    this.renderList();
    
    // Soporte para vista móvil (oculta lista y muestra chat en pantallas pequeñas)
    const wsContainer = document.getElementById('workspaceContainer');
    if (wsContainer) {
      wsContainer.classList.add('show-chat');
    }

    chatModule.loadConversation(convId);
  }
};
