/**
 * Farmhouse WhatsApp Center - Módulo de Gestión de Usuarios
 * Renderizado seguro con escape de HTML contra ataques XSS (Punto 2)
 */

const usersModule = {
  users: [],
  userToDeleteId: null,

  async init() {
    await this.loadUsers();
  },

  async loadUsers() {
    try {
      this.users = await api.get('/users/?limit=100');
      this.renderTable();
      return this.users;
    } catch (e) {
      console.error('Error cargando usuarios:', e);
      return [];
    }
  },

  renderTable() {
    const tableBody = document.getElementById('userTableBody');
    const btnOpenAdd = document.getElementById('btnOpenAddUser');
    const currentUser = auth.getUser();
    if (!tableBody) return;

    if (btnOpenAdd) {
      btnOpenAdd.style.display = (currentUser && currentUser.role === 'admin') ? 'inline-block' : 'none';
    }

    tableBody.innerHTML = '';
    if (this.users.length === 0) {
      tableBody.innerHTML = `
        <tr>
          <td colspan="5" style="text-align:center;padding:24px;color:var(--muted)">
            No hay usuarios registrados.
          </td>
        </tr>
      `;
      return;
    }

    this.users.forEach(u => {
      const tr = document.createElement('tr');
      const branchName = u.branch ? u.branch.name : (u.role === 'admin' ? 'Acceso Global' : '-');
      const isSelf = currentUser && currentUser.id === u.id;

      let roleBadge = `<span class="tag-type">${utils.escapeHtml(u.role)}</span>`;
      if (u.role === 'admin') {
        roleBadge = `<span class="tag-type badge-role-admin"><i data-lucide="crown"></i> Admin</span>`;
      } else if (u.role === 'supervisor') {
        roleBadge = `<span class="tag-type badge-role-supervisor"><i data-lucide="shield"></i> Supervisor</span>`;
      } else {
        roleBadge = `<span class="tag-type badge-role-agent"><i data-lucide="user"></i> Agente</span>`;
      }

      let statusBadge = u.active
        ? `<span class="dev-badge online">● Activo</span>`
        : `<span class="dev-badge offline">○ Inactivo</span>`;

      let actionsHtml = '';
      if (currentUser && currentUser.role === 'admin') {
        actionsHtml += `<button class="btn-sm-action" onclick="usersModule.openEditModal(${u.id})" title="Editar"><i data-lucide="pencil"></i> Editar</button> `;
        if (!isSelf) {
          actionsHtml += `<button class="btn-sm-action" onclick="usersModule.toggleActive(${u.id})" title="Cambiar estado"><i data-lucide="${u.active ? 'pause' : 'play'}"></i> ${u.active ? 'Pausar' : 'Activar'}</button> `;
          actionsHtml += `<button class="btn-sm-action btn-danger" onclick="usersModule.openDeleteModal(${u.id})" title="Eliminar" aria-label="Eliminar usuario"><i data-lucide="trash-2"></i></button>`;
        }
      }

      tr.innerHTML = `
        <td>
          <strong>${utils.escapeHtml(u.name)}</strong>
          <div style="font-size:11px;color:var(--primary);font-weight:600">@${utils.escapeHtml(u.username)}</div>
          ${u.email ? `<div style="font-size:11px;color:var(--text-muted)">${utils.escapeHtml(u.email)}</div>` : ''}
        </td>
        <td>${roleBadge}</td>
        <td>${utils.escapeHtml(branchName)}</td>
        <td>${statusBadge}</td>
        <td style="white-space:nowrap">${actionsHtml}</td>
      `;
      tableBody.appendChild(tr);
    });

    utils.renderIcons();
  },

  async registerUser(data) {
    const newUser = await api.post('/users/', data);
    await this.loadUsers();
    utils.showToast(`✓ Usuario '@${newUser.username}' creado exitosamente.`, 'success');
    return newUser;
  },

  async updateUser(id, data) {
    const updated = await api.put(`/users/${id}`, data);
    await this.loadUsers();
    utils.showToast(`✓ Usuario '@${updated.username}' actualizado.`, 'success');
    return updated;
  },

  async toggleActive(id) {
    try {
      const updated = await api.post(`/users/${id}/toggle-active`, {});
      await this.loadUsers();
      utils.showToast(`✓ Estado de '@${updated.username}' actualizado a: ${updated.active ? 'Activo' : 'Inactivo'}`, 'info');
    } catch (e) {
      utils.showToast(`Error: ${e.message}`, 'error');
    }
  },

  openAddModal() {
    const roleSelect = document.getElementById('addUserRole');
    const branchGroup = document.getElementById('addUserBranchGroup');
    const branchSelect = document.getElementById('addUserBranch');

    if (roleSelect && branchGroup) {
      roleSelect.value = 'agent';
      branchGroup.style.display = 'block';
      if (branchSelect) branchSelect.required = true;
    }

    document.getElementById('formAddUser').reset();
    const errBox = document.getElementById('addUserError');
    if (errBox) errBox.style.display = 'none';
    document.getElementById('modalAddUser').classList.add('active');
  },

  openEditModal(userId) {
    const u = this.users.find(user => user.id === userId);
    if (!u) return;

    const editErrBox = document.getElementById('editUserError');
    if (editErrBox) editErrBox.style.display = 'none';

    document.getElementById('editUserId').value = u.id;
    document.getElementById('editUserUsername').value = u.username;
    document.getElementById('editUserName').value = u.name;
    if (document.getElementById('editUserEmail')) {
      document.getElementById('editUserEmail').value = u.email || '';
    }
    document.getElementById('editUserPassword').value = '';
    document.getElementById('editUserRole').value = u.role;
    document.getElementById('editUserBranch').value = u.branch_id || '';
    document.getElementById('editUserStatus').value = u.active ? 'true' : 'false';

    document.getElementById('modalEditUser').classList.add('active');
  },

  openDeleteModal(userId) {
    const u = this.users.find(user => user.id === userId);
    if (!u) return;
    this.userToDeleteId = userId;
    document.getElementById('deleteUserNameSpan').textContent = u.name;
    document.getElementById('deleteUserUsernameSpan').textContent = `@${u.username}`;
    document.getElementById('modalDeleteUser').classList.add('active');
  },

  async confirmDeleteUser() {
    if (!this.userToDeleteId) return;
    try {
      await api.delete(`/users/${this.userToDeleteId}`);
      document.getElementById('modalDeleteUser').classList.remove('active');
      this.userToDeleteId = null;
      await this.loadUsers();
      utils.showToast('✓ Usuario eliminado correctamente.', 'success');
    } catch (e) {
      utils.showToast(`Error eliminando usuario: ${e.message}`, 'error');
    }
  }
};
