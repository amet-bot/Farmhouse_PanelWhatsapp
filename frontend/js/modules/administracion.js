/**
 * Farmhouse Link — Administración (Sucursales, empleados y dispositivos)
 *
 * El panel dedicado que "Sucursales, empleados y dispositivos" prometía en el hub en vez de
 * seguir enterrado dentro de Centro WhatsApp. Usuarios reusa js/modules/users.js tal cual (ya
 * era genérico, sin nada atado a WhatsApp) — los mismos formularios y el mismo backend, calcados
 * de Centro WhatsApp. Dispositivos es lógica nueva y más simple: el módulo devices.js de Centro
 * WhatsApp trae enganchado wsClient/conversationsModule (auto-vincular ESTE navegador a un
 * dispositivo, heartbeat) que no tiene sentido en una pantalla de administración pura. Sucursales
 * es completamente nuevo — antes solo se podían leer, nunca crear ni editar.
 */
document.addEventListener('DOMContentLoaded', async () => {
  const $ = (id) => document.getElementById(id);
  const esc = (s) => utils.escapeHtml(s ?? '');

  let branches = [];
  let devices = [];
  let branchesList = [];

  FarmhouseShell.initTheme();
  FarmhouseShell.initLogout({ redirectTo: '/' });

  // ==========================================================================
  // Pestañas
  // ==========================================================================
  function setTab(tab) {
    document.querySelectorAll('#adminTabs .admin-tab').forEach((b) => b.classList.toggle('active', b.dataset.tab === tab));
    $('viewUsuarios').hidden = tab !== 'usuarios';
    $('viewDispositivos').hidden = tab !== 'dispositivos';
    $('viewSucursales').hidden = tab !== 'sucursales';
  }
  document.querySelectorAll('#adminTabs .admin-tab').forEach((b) => b.addEventListener('click', () => setTab(b.dataset.tab)));

  // ==========================================================================
  // Sucursales — para llenar los <select> de los formularios de Usuarios/Dispositivos
  // ==========================================================================
  async function loadBranchesForSelects() {
    branches = await api.get('/branches/admin');
    const options = branches.map((b) => `<option value="${b.id}">${esc(b.name)}${b.active ? '' : ' (inactiva)'}</option>`).join('');
    // Los de usuario llevan la opción "sin sucursal" (como en Centro WhatsApp): sin ella el
    // <select> quedaba siempre en la primera sucursal, y crear o editar un admin o un
    // supervisor general lo dejaba atado a una sucursal — perdía el alcance global en
    // Inventario, Reportes, Prep y Operación.
    ['addUserBranch', 'editUserBranch'].forEach((id) => {
      const el = $(id);
      if (el) el.innerHTML = '<option value="">-- Sin sucursal (Admin / Supervisor general) --</option>' + options;
    });
    ['addDevBranch', 'editDevBranch'].forEach((id) => {
      const el = $(id);
      if (el) el.innerHTML = '<option value="">-- Seleccionar sucursal --</option>' + options;
    });
  }

  // Bloquea el botón de envío del formulario mientras corre la petición: un doble clic ya no
  // registra dos dispositivos o dos sucursales.
  async function withSubmitBusy(form, action) {
    const btn = form.querySelector('[type="submit"]');
    if (btn && btn.disabled) return;
    if (btn) btn.disabled = true;
    try {
      await action();
    } finally {
      if (btn) btn.disabled = false;
    }
  }

  window.addEventListener('auth:unauthorized', () => { window.location.href = '/'; });

  // ==========================================================================
  // Usuarios — reusa usersModule tal cual (js/modules/users.js), mismos ids de formulario.
  // ==========================================================================
  $('btnOpenAddUser').addEventListener('click', () => usersModule.openAddModal());
  $('closeModalAddUser').addEventListener('click', () => $('modalAddUser').classList.remove('active'));
  $('closeModalEditUser').addEventListener('click', () => $('modalEditUser').classList.remove('active'));
  $('closeModalDeleteUser').addEventListener('click', () => $('modalDeleteUser').classList.remove('active'));
  $('btnCancelDeleteUser').addEventListener('click', () => $('modalDeleteUser').classList.remove('active'));
  $('btnConfirmDeleteUser').addEventListener('click', () => usersModule.confirmDeleteUser());

  $('addUserRole').addEventListener('change', (e) => {
    const branchGroup = $('addUserBranchGroup');
    const branchSelect = $('addUserBranch');
    if (e.target.value === 'admin') {
      branchGroup.style.display = 'none';
      branchSelect.required = false;
    } else {
      branchGroup.style.display = 'block';
      branchSelect.required = (e.target.value === 'agent');
    }
  });

  $('formAddUser').addEventListener('submit', async (e) => {
    e.preventDefault();
    const saveBtn = $('btnSaveUser');
    const errBox = $('addUserError');
    errBox.style.display = 'none';
    const showError = (msg) => { errBox.textContent = `⚠️ ${msg}`; errBox.style.display = 'block'; utils.showToast(msg, 'error'); };

    const roleVal = $('addUserRole').value;
    const branchVal = $('addUserBranch').value;
    const usernameVal = $('addUserUsername').value.trim().toLowerCase();
    const nameVal = $('addUserName').value.trim();
    const emailRaw = $('addUserEmail').value.trim();
    const pwdVal = $('addUserPassword').value.trim();

    if (!usernameVal || usernameVal.length < 2) return showError('El usuario / código de empleado es obligatorio (mínimo 2 caracteres).');
    if (!nameVal || nameVal.length < 2) return showError('El nombre completo es obligatorio.');
    if (!pwdVal || pwdVal.length < 4) return showError('La contraseña inicial debe tener al menos 4 caracteres.');
    if (emailRaw && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(emailRaw)) return showError('El correo electrónico no tiene un formato válido.');
    if (roleVal === 'agent' && !branchVal) return showError('Para un agente debes seleccionar una sucursal.');

    const data = {
      username: usernameVal, name: nameVal, email: emailRaw ? emailRaw.toLowerCase() : null,
      password: pwdVal, role: roleVal,
      branch_id: (roleVal === 'agent' || branchVal) ? parseInt(branchVal) : null,
      active: true,
    };

    saveBtn.disabled = true;
    try {
      await usersModule.registerUser(data);
      $('modalAddUser').classList.remove('active');
      $('formAddUser').reset();
    } catch (err) {
      showError(`Error creando usuario: ${err.message}`);
    } finally {
      saveBtn.disabled = false;
    }
  });

  $('formEditUser').addEventListener('submit', async (e) => {
    e.preventDefault();
    const errBox = $('editUserError');
    errBox.style.display = 'none';
    const showError = (msg) => { errBox.textContent = `⚠️ ${msg}`; errBox.style.display = 'block'; utils.showToast(msg, 'error'); };

    const id = $('editUserId').value;
    const branchVal = $('editUserBranch').value;
    const pwdVal = $('editUserPassword').value.trim();
    const emailVal = $('editUserEmail').value.trim().toLowerCase();

    if (pwdVal && pwdVal.length < 4) return showError('La nueva contraseña debe tener al menos 4 caracteres.');

    const data = {
      username: $('editUserUsername').value.trim().toLowerCase(),
      name: $('editUserName').value.trim(),
      email: emailVal || null,
      role: $('editUserRole').value,
      branch_id: branchVal ? parseInt(branchVal) : null,
      active: $('editUserStatus').value === 'true',
    };
    if (pwdVal) data.password = pwdVal;

    await withSubmitBusy(e.target, async () => {
      try {
        await usersModule.updateUser(id, data);
        $('modalEditUser').classList.remove('active');
      } catch (err) {
        showError(`Error actualizando usuario: ${err.message}`);
      }
    });
  });

  // ==========================================================================
  // Dispositivos — CRUD propio (sin wsClient/heartbeat, eso es de Centro WhatsApp).
  // ==========================================================================
  async function loadDevices() {
    devices = await api.get('/devices/?limit=200');
    renderDeviceTable();
  }

  function renderDeviceTable() {
    const tbody = $('deviceTableBody');
    if (!devices.length) {
      tbody.innerHTML = `<tr><td colspan="6" style="text-align:center;padding:24px;color:var(--text-muted)">No hay dispositivos registrados.</td></tr>`;
      return;
    }
    tbody.innerHTML = devices.map((dev) => {
      const branchName = dev.branch ? esc(dev.branch.name) : '-';
      const userName = dev.assigned_user ? esc(dev.assigned_user.name) : 'Sin asignar';
      let statusBadge = '<span class="dev-badge offline">○ Inactivo</span>';
      if (dev.status === 'active') statusBadge = '<span class="dev-badge online">● Activo</span>';
      else if (dev.status === 'revoked') statusBadge = '<span class="dev-badge disabled">✕ Revocado</span>';
      else if (dev.status === 'disabled') statusBadge = '<span class="dev-badge disabled">⏸ Deshabilitado</span>';

      let actions = `<button class="btn-sm-action" onclick="adminModule.openEditDevice(${dev.id})" title="Editar"><i data-lucide="pencil"></i> Editar</button>`;
      if (dev.status === 'active') {
        actions += ` <button class="btn-sm-action delete-action" onclick="adminModule.revokeDevice(${dev.id})" title="Revocar"><i data-lucide="ban"></i> Revocar</button>`;
      }

      return `
        <tr>
          <td><strong>${esc(dev.name)}</strong><div style="font-size:11px;color:var(--text-muted);font-family:monospace">ID: ${esc(dev.device_id)}</div></td>
          <td><span class="tag-type">${esc(dev.device_type)}</span></td>
          <td>${branchName}</td>
          <td>${userName}</td>
          <td>${statusBadge}</td>
          <td style="white-space:nowrap">${actions}</td>
        </tr>
      `;
    }).join('');
    utils.renderIcons();
  }

  window.adminModule = window.adminModule || {};
  adminModule.openEditDevice = (devId) => {
    const dev = devices.find((d) => d.id === devId);
    if (!dev) return;
    $('editDevId').value = dev.id;
    $('editDevName').value = dev.name;
    $('editDevType').value = dev.device_type;
    $('editDevBranch').value = dev.branch_id;
    $('editDevStatus').value = dev.status;
    $('modalEditDevice').classList.add('active');
  };
  adminModule.revokeDevice = async (devId) => {
    if (!confirm('¿Estás seguro de que deseas revocar el acceso a este dispositivo?')) return;
    try {
      await api.post(`/devices/${devId}/revoke`, {});
      await loadDevices();
      utils.showToast('Acceso del dispositivo revocado.', 'info');
    } catch (err) {
      utils.showToast(err.message || 'No se pudo revocar el dispositivo.', 'error');
    }
  };

  $('btnOpenAddDevice').addEventListener('click', () => {
    const userSelect = $('addDevUser');
    userSelect.innerHTML = '<option value="">-- Sin asignar / Agente de turno --</option>' +
      usersModule.users.map((u) => `<option value="${u.id}">${esc(u.name)} (${esc(u.role)}${u.branch ? ' - ' + esc(u.branch.name) : ''})</option>`).join('');
    $('formAddDevice').reset();
    $('addDevError').style.display = 'none';
    $('modalAddDevice').classList.add('active');
  });
  $('closeModalAddDevice').addEventListener('click', () => $('modalAddDevice').classList.remove('active'));
  $('closeModalEditDevice').addEventListener('click', () => $('modalEditDevice').classList.remove('active'));

  $('formAddDevice').addEventListener('submit', async (e) => {
    e.preventDefault();
    const errBox = $('addDevError');
    errBox.style.display = 'none';
    const data = {
      name: $('addDevName').value.trim(),
      device_type: $('addDevType').value,
      branch_id: parseInt($('addDevBranch').value),
      assigned_user_id: $('addDevUser').value ? parseInt($('addDevUser').value) : null,
    };
    if (!data.branch_id) {
      errBox.textContent = '⚠️ Seleccioná la sucursal del dispositivo.';
      errBox.style.display = 'block';
      return;
    }
    await withSubmitBusy(e.target, async () => {
      try {
        const newDev = await api.post('/devices/', data);
        await loadDevices();
        utils.showToast(`✓ Dispositivo '${newDev.name}' registrado.`, 'success');
        $('modalAddDevice').classList.remove('active');
      } catch (err) {
        errBox.textContent = `⚠️ ${err.message}`;
        errBox.style.display = 'block';
      }
    });
  });

  $('formEditDevice').addEventListener('submit', async (e) => {
    e.preventDefault();
    const errBox = $('editDevError');
    errBox.style.display = 'none';
    const id = $('editDevId').value;
    const data = {
      name: $('editDevName').value.trim(),
      device_type: $('editDevType').value,
      branch_id: parseInt($('editDevBranch').value),
      status: $('editDevStatus').value,
    };
    await withSubmitBusy(e.target, async () => {
      try {
        const updated = await api.put(`/devices/${id}`, data);
        await loadDevices();
        utils.showToast(`✓ Dispositivo '${updated.name}' actualizado.`, 'success');
        $('modalEditDevice').classList.remove('active');
      } catch (err) {
        errBox.textContent = `⚠️ ${err.message}`;
        errBox.style.display = 'block';
      }
    });
  });

  // ==========================================================================
  // Sucursales — nuevo (antes solo se podían leer, nunca crear ni editar).
  // ==========================================================================
  async function loadBranchesTable() {
    branchesList = await api.get('/branches/admin');
    renderBranchTable();
  }

  function renderBranchTable() {
    const tbody = $('branchTableBody');
    if (!branchesList.length) {
      tbody.innerHTML = `<tr><td colspan="6" style="text-align:center;padding:24px;color:var(--text-muted)">No hay sucursales registradas.</td></tr>`;
      return;
    }
    tbody.innerHTML = branchesList.map((b) => {
      const statusBadge = b.active ? '<span class="dev-badge online">● Activa</span>' : '<span class="dev-badge disabled">✕ Inactiva</span>';
      return `
        <tr>
          <td><strong style="color:${esc(b.color || 'inherit')}">${esc(b.name)}</strong></td>
          <td><span class="tag-type">${esc(b.code)}</span></td>
          <td>${esc(b.address || '-')}</td>
          <td>${b.accepts_delivery ? 'Sí' : 'No'}</td>
          <td>${statusBadge}</td>
          <td style="white-space:nowrap">
            <button class="btn-sm-action" onclick="adminModule.openEditBranch(${b.id})" title="Editar"><i data-lucide="pencil"></i> Editar</button>
            <button class="btn-sm-action" onclick="adminModule.toggleBranch(${b.id})" title="Cambiar estado"><i data-lucide="${b.active ? 'pause' : 'play'}"></i> ${b.active ? 'Desactivar' : 'Activar'}</button>
          </td>
        </tr>
      `;
    }).join('');
    utils.renderIcons();
  }

  adminModule.openEditBranch = (branchId) => {
    const b = branchesList.find((x) => x.id === branchId);
    if (!b) return;
    $('editBranchId').value = b.id;
    $('editBranchName').value = b.name;
    $('editBranchCode').value = b.code;
    $('editBranchAddress').value = b.address || '';
    $('editBranchColor').value = b.color || '#16a34a';
    $('editBranchDelivery').checked = !!b.accepts_delivery;
    $('editBranchError').style.display = 'none';
    $('modalEditBranch').classList.add('active');
  };
  adminModule.toggleBranch = async (branchId) => {
    try {
      await api.post(`/branches/${branchId}/toggle-active`, {});
      await Promise.all([loadBranchesTable(), loadBranchesForSelects()]);
      utils.showToast('Estado de la sucursal actualizado.', 'info');
    } catch (err) {
      utils.showToast(err.message || 'No se pudo cambiar el estado.', 'error');
    }
  };

  $('btnOpenAddBranch').addEventListener('click', () => {
    $('formAddBranch').reset();
    $('addBranchColor').value = '#16a34a';
    $('addBranchDelivery').checked = true;
    $('addBranchError').style.display = 'none';
    $('modalAddBranch').classList.add('active');
  });
  $('closeModalAddBranch').addEventListener('click', () => $('modalAddBranch').classList.remove('active'));
  $('closeModalEditBranch').addEventListener('click', () => $('modalEditBranch').classList.remove('active'));

  $('formAddBranch').addEventListener('submit', async (e) => {
    e.preventDefault();
    const errBox = $('addBranchError');
    errBox.style.display = 'none';
    const data = {
      name: $('addBranchName').value.trim(),
      code: $('addBranchCode').value.trim(),
      address: $('addBranchAddress').value.trim() || null,
      color: $('addBranchColor').value,
      accepts_delivery: $('addBranchDelivery').checked,
    };
    await withSubmitBusy(e.target, async () => {
      try {
        await api.post('/branches/', data);
        await Promise.all([loadBranchesTable(), loadBranchesForSelects()]);
        utils.showToast('✓ Sucursal creada.', 'success');
        $('modalAddBranch').classList.remove('active');
      } catch (err) {
        errBox.textContent = `⚠️ ${err.message}`;
        errBox.style.display = 'block';
      }
    });
  });

  $('formEditBranch').addEventListener('submit', async (e) => {
    e.preventDefault();
    const errBox = $('editBranchError');
    errBox.style.display = 'none';
    const id = $('editBranchId').value;
    const data = {
      name: $('editBranchName').value.trim(),
      code: $('editBranchCode').value.trim(),
      address: $('editBranchAddress').value.trim() || null,
      color: $('editBranchColor').value,
      accepts_delivery: $('editBranchDelivery').checked,
    };
    await withSubmitBusy(e.target, async () => {
      try {
        await api.put(`/branches/${id}`, data);
        await Promise.all([loadBranchesTable(), loadBranchesForSelects()]);
        utils.showToast('✓ Sucursal actualizada.', 'success');
        $('modalEditBranch').classList.remove('active');
      } catch (err) {
        errBox.textContent = `⚠️ ${err.message}`;
        errBox.style.display = 'block';
      }
    });
  });

  // ==========================================================================
  // Arranque
  // ==========================================================================
  const user = await auth.checkSession();
  if (!user) { window.location.href = '/'; return; }

  if (user.role !== 'admin') {
    $('adminGate').hidden = true;
    $('adminDenied').hidden = false;
    utils.renderIcons();
    return;
  }

  $('adminGate').hidden = true;
  $('adminMain').hidden = false;
  FarmhouseShell.fillUserHeader({ nameId: 'adminAgentName', roleId: 'adminAgentRole', avatarId: 'adminAgentAvatar' }, user);

  // Cada carga por su lado: antes eran awaits en cadena sin try/catch, y si fallaba una (p. ej.
  // /devices/) las siguientes nunca corrían y la pantalla quedaba a medias sin ningún aviso.
  const loaders = [
    ['las sucursales', loadBranchesForSelects],
    ['los usuarios', () => usersModule.loadUsers()],
    ['los dispositivos', loadDevices],
    ['la tabla de sucursales', loadBranchesTable],
  ];
  const results = await Promise.allSettled(loaders.map(([, fn]) => fn()));
  results.forEach((r, i) => {
    if (r.status === 'rejected') utils.showToast(`No se pudieron cargar ${loaders[i][0]}.`, 'error');
  });
  utils.renderIcons();
});
