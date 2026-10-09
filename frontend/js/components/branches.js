/**
 * Farmhouse WhatsApp Center - Módulo de Sucursales
 */

const branchesModule = {
  branches: [],
  activeBranchId: null,

  async init() {
    await this.loadBranches();
    this.renderSidebar();
    this.populateSelects();
  },

  async loadBranches() {
    try {
      this.branches = await api.get('/branches/');
      return this.branches;
    } catch (err) {
      console.error('Error cargando sucursales:', err);
      return [];
    }
  },

  renderSidebar() {
    const container = document.getElementById('sidebarBranchesList');
    if (!container) return;

    const user = auth.getUser();
    container.innerHTML = '';

    // Si es agente, solo puede ver la sucursal asignada
    let visibleBranches = this.branches;
    if (user && user.role === 'agent' && user.branch_id) {
      visibleBranches = this.branches.filter(b => b.id === user.branch_id);
    }

    visibleBranches.forEach(branch => {
      const btn = document.createElement('button');
      btn.className = 'nav-btn';
      btn.dataset.branchId = branch.id;
      if (this.activeBranchId === branch.id) {
        btn.classList.add('active');
      }

      const escapedName = utils.escapeHtml(branch.name);
      const escapedColor = utils.escapeHtml(branch.color || '#16a34a');

      btn.innerHTML = `
        <span class="nav-left-group">
          <span class="branch-dot" style="background-color:${escapedColor}"></span>
          <span class="nav-label">${escapedName}</span>
        </span>
        <span class="nav-badge" id="badgeBranch_${branch.id}">0</span>
      `;

      btn.addEventListener('click', () => {
        this.selectBranch(branch.id);
      });

      container.appendChild(btn);
    });

    utils.renderIcons();
  },

  selectBranch(branchId) {
    this.activeBranchId = branchId;
    const topSelect = document.getElementById('branchSelect');
    if (topSelect && !topSelect.disabled) topSelect.value = String(branchId);
    document.querySelectorAll('.sidebar .nav-btn').forEach(btn => {
      btn.classList.remove('active');
      if (btn.dataset.branchId && parseInt(btn.dataset.branchId) === branchId) {
        btn.classList.add('active');
      }
    });

    // En móvil, asegurarse de mostrar la columna de conversaciones al cambiar de sucursal
    const wsContainer = document.getElementById('workspaceContainer');
    if (wsContainer) {
      wsContainer.classList.remove('show-chat');
    }

    conversationsModule.setBranchFilter(branchId);
  },

  /** Vuelve a todas las sucursales (select de arriba en "Todas" o la ✕ del aviso de la lista). */
  clearBranch() {
    this.activeBranchId = null;
    const topSelect = document.getElementById('branchSelect');
    if (topSelect && !topSelect.disabled) topSelect.value = '';
    document.querySelectorAll('.sidebar .nav-btn[data-branch-id]').forEach((btn) => btn.classList.remove('active'));
    conversationsModule.setBranchFilter(null);
  },

  populateSelects() {
    const userSelect = document.getElementById('addUserBranch');
    const editUserSelect = document.getElementById('editUserBranch');
    const devSelect = document.getElementById('addDevBranch');
    const editDevSelect = document.getElementById('editDevBranch');
    const transferSelect = document.getElementById('transferTargetBranch');

    const optionsHtml = this.branches
      .map(b => `<option value="${b.id}">${utils.escapeHtml(b.name)}</option>`)
      .join('');

    if (userSelect) userSelect.innerHTML = '<option value="">-- Sin sucursal (Admin) --</option>' + optionsHtml;
    if (editUserSelect) editUserSelect.innerHTML = '<option value="">-- Sin sucursal (Admin) --</option>' + optionsHtml;
    if (devSelect) devSelect.innerHTML = '<option value="">-- Seleccionar Sucursal --</option>' + optionsHtml;
    if (editDevSelect) editDevSelect.innerHTML = '<option value="">-- Seleccionar Sucursal --</option>' + optionsHtml;
    if (transferSelect) transferSelect.innerHTML = '<option value="">-- Seleccionar Sucursal Destino --</option>' + optionsHtml;

    // "Sucursal actual" de la barra superior: estaba siempre vacío. Filtra la bandeja igual
    // que tocar una sucursal en el sidebar (un agente solo ve la suya, fija).
    const topSelect = document.getElementById('branchSelect');
    if (topSelect && !topSelect.dataset.bound) {
      const user = auth.getUser();
      if (user && user.role === 'agent' && user.branch_id) {
        const own = this.branches.find((b) => b.id === user.branch_id);
        topSelect.innerHTML = own ? `<option value="${own.id}">${utils.escapeHtml(own.name)}</option>` : '';
        topSelect.disabled = true;
      } else {
        topSelect.innerHTML = '<option value="">Todas las sucursales</option>' + optionsHtml;
        topSelect.addEventListener('change', () => {
          const id = topSelect.value ? Number(topSelect.value) : null;
          if (id) {
            this.selectBranch(id);
          } else {
            this.clearBranch();
          }
        });
      }
      topSelect.dataset.bound = '1';
    }
  },

  // Varios disparadores piden los contadores casi a la vez (cada carga de la lista, cada evento
  // del WebSocket): se agrupan en una sola petición por ráfaga.
  _countersTimer: null,

  updateCounters() {
    clearTimeout(this._countersTimer);
    this._countersTimer = setTimeout(() => this._fetchCounters(), 250);
  },

  async _fetchCounters() {
    try {
      // Un solo GET agrupado en el servidor. Antes eran 4 listados completos de hasta 100
      // conversaciones (con todos sus mensajes) solo para contarlos, y el número se quedaba
      // en 100 aunque hubiera más.
      const c = await api.get('/conversations/counts');
      const setText = (el, value) => { if (el) el.textContent = value; };

      // El sidebar muestra siempre el total; las pestañas cuentan lo que está filtrando la
      // lista de abajo (con una sucursal elegida decían "Abiertas 18" sobre 8 conversaciones).
      setText(document.getElementById('badgeConversaciones'), c.abiertas);
      setText(document.querySelector('[data-nav="no-asignadas"] .nav-badge'), c.no_asignadas);
      setText(document.querySelector('[data-nav="todas"] .nav-badge'), c.todas);

      const filtered = conversationsModule.activeBranchId;
      const tabs = filtered
        ? ((c.por_sucursal || {})[String(filtered)] || { abiertas: 0, no_asignadas: 0, pendientes: 0, todas: 0 })
        : c;
      setText(document.getElementById('tabCountAbiertas'), tabs.abiertas);
      setText(document.getElementById('tabCountNoAsignadas'), tabs.no_asignadas);
      setText(document.getElementById('tabCountTodas'), tabs.todas);
      setText(document.getElementById('tabCountPendientes'), tabs.pendientes);

      const byBranch = c.abiertas_por_sucursal || {};
      this.branches.forEach(b => {
        setText(document.getElementById(`badgeBranch_${b.id}`), byBranch[String(b.id)] || 0);
      });
    } catch (e) {
      console.warn('Error actualizando contadores:', e);
    }
  }
};
