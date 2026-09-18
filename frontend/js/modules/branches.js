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
  },

  async updateCounters() {
    try {
      // Las 4 llamadas son independientes entre sí: se disparan en paralelo (antes eran
      // secuenciales) para que actualizar los contadores tome el tiempo de la más lenta, no
      // la suma de las 4.
      const [convs, unassigned, allConvs, pending] = await Promise.all([
        api.get('/conversations/?status=abiertas&limit=100'),
        api.get('/conversations/?status=no-asignadas&limit=100'),
        api.get('/conversations/?status=todas&limit=100'),
        api.get('/conversations/?status=pendientes&limit=100'),
      ]);

      const badgeConv = document.getElementById('badgeConversaciones');
      if (badgeConv) badgeConv.textContent = convs.length;
      const tabAbiertas = document.getElementById('tabCountAbiertas');
      if (tabAbiertas) tabAbiertas.textContent = convs.length;

      const badgeUnassigned = document.querySelector('[data-nav="no-asignadas"] .nav-badge');
      if (badgeUnassigned) badgeUnassigned.textContent = unassigned.length;
      const tabUnassigned = document.getElementById('tabCountNoAsignadas');
      if (tabUnassigned) tabUnassigned.textContent = unassigned.length;

      const badgeAll = document.querySelector('[data-nav="todas"] .nav-badge');
      if (badgeAll) badgeAll.textContent = allConvs.length;
      const tabAll = document.getElementById('tabCountTodas');
      if (tabAll) tabAll.textContent = allConvs.length;

      const tabPending = document.getElementById('tabCountPendientes');
      if (tabPending) tabPending.textContent = pending.length;

      this.branches.forEach(b => {
        const count = convs.filter(c => c.branch_id === b.id).length;
        const bBadge = document.getElementById(`badgeBranch_${b.id}`);
        if (bBadge) bBadge.textContent = count;
      });
    } catch (e) {
      console.warn('Error actualizando contadores:', e);
    }
  }
};
