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
          <td colspan="5" style="text-align:center;padding:24px;color:var(--text-muted)">
            No hay usuarios registrados.
          </td>
        </tr>
      `;
      return;
    }

    this.users.forEach(u => {
      const tr = document.createElement('tr');
      const branchName = u.branch ? u.branch.name : (u.role === 'admin' || this.isLogistics(u) ? 'Todas las sucursales' : '-');
      const isSelf = currentUser && currentUser.id === u.id;

      // El nombre del rol en español, el mismo de la cabecera (FarmhouseShell.roleLabel).
      const rolTxt = utils.escapeHtml(this.roleLabel(u));
      let roleBadge;
      if (u.role === 'admin') {
        roleBadge = `<span class="tag-type badge-role-admin"><i data-lucide="crown"></i> ${rolTxt}</span>`;
      } else if (this.isLogistics(u)) {
        roleBadge = `<span class="tag-type badge-role-supervisor"><i data-lucide="truck"></i> ${rolTxt}</span>`;
      } else if (u.role === 'supervisor') {
        roleBadge = `<span class="tag-type badge-role-supervisor"><i data-lucide="shield"></i> ${rolTxt}</span>`;
      } else {
        roleBadge = `<span class="tag-type badge-role-agent"><i data-lucide="user"></i> ${rolTxt}</span>`;
      }

      let statusBadge = u.active
        ? `<span class="dev-badge online">● Activo</span>`
        : `<span class="dev-badge offline">○ Inactivo</span>`;

      let actionsHtml = '';
      if (currentUser && currentUser.role === 'admin') {
        actionsHtml += `<button type="button" class="btn-sm-action" onclick="usersModule.openEditModal(${u.id})"><i data-lucide="pencil"></i> Editar</button> `;
        if (!isSelf) {
          actionsHtml += `<button type="button" class="btn-sm-action" onclick="usersModule.toggleActive(${u.id})"><i data-lucide="${u.active ? 'pause' : 'play'}"></i> ${u.active ? 'Pausar' : 'Activar'}</button> `;
          actionsHtml += `<button type="button" class="btn-sm-action delete-action" onclick="usersModule.openDeleteModal(${u.id})" aria-label="Eliminar a ${utils.escapeHtml(u.name)}"><i data-lucide="trash-2"></i> Eliminar</button>`;
        }
      }

      // data-label: en tablet de pie y celular la fila se ve como tarjeta (administracion.css).
      tr.innerHTML = `
        <td class="adm-td-main">
          <strong>${utils.escapeHtml(u.name)}</strong>
          <div class="adm-sub" style="font-size:11px;color:var(--primary-color);font-weight:600">@${utils.escapeHtml(u.username)}</div>
          ${u.email ? `<div class="adm-sub" style="font-size:11px;color:var(--text-muted)">${utils.escapeHtml(u.email)}</div>` : ''}
        </td>
        <td data-label="Rol">${roleBadge}</td>
        <td data-label="Sucursal">${utils.escapeHtml(branchName)}</td>
        <td data-label="Estado">${statusBadge}</td>
        <td class="adm-td-actions${actionsHtml ? '' : ' is-empty'}" style="white-space:nowrap">${actionsHtml}</td>
      `;
      tableBody.appendChild(tr);
    });

    utils.renderIcons();
  },

  // "Gerente de logística" no es un rol aparte en el servidor: es un supervisor SIN sucursal,
  // que ve y opera todas (inventario, compras, tareas, reportes) pero no administra usuarios,
  // dispositivos, integraciones ni respaldos.
  normalizeRole(data) {
    if (data && data.role === 'logistica') return { ...data, role: 'supervisor', branch_id: null };
    return data;
  },
  isLogistics(u) {
    return u && u.role === 'supervisor' && !u.branch_id;
  },
  roleLabel(u) {
    if (window.FarmhouseShell && typeof FarmhouseShell.roleLabel === 'function') return FarmhouseShell.roleLabel(u);
    if (!u) return '';
    if (u.role === 'admin') return 'Administrador';
    if (u.role === 'supervisor') return u.branch_id ? 'Encargado' : 'Gerente de logística';
    if (u.role === 'agent') return 'Empleado';
    if (u.role === 'rrhh') return 'Recursos Humanos';
    return u.role || '';
  },

  async registerUser(data) {
    const newUser = await api.post('/users/', this.normalizeRole(data));
    await this.loadUsers();
    utils.showToast(`Usuario @${newUser.username} creado.`, 'success');
    return newUser;
  },

  async updateUser(id, data) {
    const updated = await api.put(`/users/${id}`, this.normalizeRole(data));
    await this.loadUsers();
    utils.showToast(`Cambios guardados para @${updated.username}.`, 'success');
    return updated;
  },

  async toggleActive(id) {
    try {
      const updated = await api.post(`/users/${id}/toggle-active`, {});
      await this.loadUsers();
      utils.showToast(updated.active ? `@${updated.username} está activo: ya puede entrar.` : `@${updated.username} quedó en pausa: no puede entrar hasta que lo actives.`, 'success');
    } catch (e) {
      utils.showToast(`No se pudo cambiar el estado: ${e.message}`, 'error');
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
    const editRole = document.getElementById('editUserRole');
    editRole.value = this.isLogistics(u) && editRole.querySelector('option[value="logistica"]') ? 'logistica' : u.role;
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
    const btn = document.getElementById('btnConfirmDeleteUser');
    const prev = btn ? btn.innerHTML : '';
    if (btn) { btn.disabled = true; btn.textContent = 'Eliminando…'; }
    try {
      await api.delete(`/users/${this.userToDeleteId}`);
      document.getElementById('modalDeleteUser').classList.remove('active');
      this.userToDeleteId = null;
      await this.loadUsers();
      utils.showToast('Usuario eliminado.', 'success');
    } catch (e) {
      utils.showToast(`No se pudo eliminar el usuario: ${e.message}`, 'error');
    } finally {
      if (btn) { btn.disabled = false; btn.innerHTML = prev; }
    }
  }
};
