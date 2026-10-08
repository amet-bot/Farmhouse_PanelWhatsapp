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
    document.querySelectorAll('.admin-view').forEach((v) => { v.hidden = v.dataset.view !== tab; });
    // Las pestañas que viven en otro archivo (admin-sistema.js) se cargan al abrirse.
    document.dispatchEvent(new CustomEvent('admin:tab', { detail: tab }));
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
  // registra dos dispositivos o dos sucursales. Mientras tanto dice "Guardando…".
  async function withSubmitBusy(form, action) {
    const btn = form.querySelector('[type="submit"]');
    if (btn && btn.disabled) return;
    const prev = btn ? btn.innerHTML : '';
    if (btn) { btn.disabled = true; btn.textContent = 'Guardando…'; }
    try {
      await action();
    } finally {
      if (btn) { btn.disabled = false; btn.innerHTML = prev; }
    }
  }
  const roleLabel = (u) => usersModule.roleLabel(u);
  const DEVICE_TYPES = { computadora: 'Computadora', tablet: 'Tablet', celular: 'Celular' };

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

  const ROLE_HINTS = {
    agent: 'Opera su sucursal: recibir, contar, merma, cierre de turno, tareas, solicitudes.',
    supervisor: 'Encargado de UNA sucursal: además aprueba solicitudes, ajusta inventario, arma la hoja de cierre y ve reportes de su sucursal.',
    logistica: 'Ve y opera TODAS las sucursales: existencias, pedido sugerido, órdenes, tareas, Centro de operación y reportes. No administra usuarios, dispositivos, integraciones ni respaldos. Igual que un encargado, entra desde un dispositivo registrado (pestaña Dispositivos).',
    admin: 'Acceso total, incluida esta pantalla de Administración.',
    rrhh: 'Solo ve y gestiona los Contratos de los colaboradores (cédulas, cuentas, salarios). Nada más del sistema. La sucursal es opcional (p. ej. la oficina). Contraseña de al menos 10 caracteres, no solo números.',
  };
  function syncAddRole() {
    const role = $('addUserRole').value;
    const sinSucursal = role === 'admin' || role === 'logistica';   // Recursos Humanos puede (o no) tener una sucursal, p. ej. la oficina
    $('addUserBranchGroup').style.display = sinSucursal ? 'none' : 'block';
    $('addUserBranch').required = role === 'agent' || role === 'supervisor';
    if (sinSucursal) $('addUserBranch').value = '';
    $('addUserRoleHint').textContent = ROLE_HINTS[role] || '';
  }
  $('addUserRole').addEventListener('change', syncAddRole);
  $('btnOpenAddUser').addEventListener('click', () => setTimeout(syncAddRole, 0));
  $('editUserRole').addEventListener('change', () => {
    if (['admin', 'logistica'].includes($('editUserRole').value)) $('editUserBranch').value = '';
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
    if (roleVal === 'agent' && !branchVal) return showError('Un empleado trabaja en una sucursal: elígela.');
    if (roleVal === 'rrhh' && (pwdVal.length < 10 || /^\d+$/.test(pwdVal))) return showError('Para Recursos Humanos la contraseña debe tener al menos 10 caracteres y no ser solo números (hoy tiene ' + pwdVal.length + ').');
    if (roleVal === 'supervisor' && !branchVal) return showError('Un encargado es de una sucursal: elígela. Para todas, usa "Gerente de logística".');

    const data = {
      username: usernameVal, name: nameVal, email: emailRaw ? emailRaw.toLowerCase() : null,
      password: pwdVal, role: roleVal,
      branch_id: (roleVal === 'agent' || branchVal) ? parseInt(branchVal) : null,
      active: true,
    };

    if (saveBtn.disabled) return;
    const prevLabel = saveBtn.innerHTML;
    saveBtn.disabled = true;
    saveBtn.textContent = 'Guardando…';
    try {
      await usersModule.registerUser(data);
      $('modalAddUser').classList.remove('active');
      $('formAddUser').reset();
    } catch (err) {
      showError(`No se pudo crear el usuario: ${err.message}`);
    } finally {
      saveBtn.disabled = false;
      saveBtn.innerHTML = prevLabel;
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
    if (pwdVal && $('editUserRole').value === 'rrhh' && (pwdVal.length < 10 || /^\d+$/.test(pwdVal))) return showError('Para Recursos Humanos la contraseña debe tener al menos 10 caracteres y no ser solo números (hoy tiene ' + pwdVal.length + ').');

    if ($('editUserRole').value === 'supervisor' && !branchVal) return showError('Un encargado es de una sucursal: elígela. Para todas, usa "Gerente de logística".');

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
        showError(`No se pudieron guardar los cambios: ${err.message}`);
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
      tbody.innerHTML = `<tr><td colspan="6" class="adm-empty">Todavía no hay dispositivos registrados. Registra la tablet o computadora de cada sucursal con "Registrar dispositivo".</td></tr>`;
      return;
    }
    tbody.innerHTML = devices.map((dev) => {
      const branchName = dev.branch ? esc(dev.branch.name) : '-';
      const userName = dev.assigned_user ? esc(dev.assigned_user.name) : 'Sin asignar';
      let statusBadge = '<span class="dev-badge offline">○ Inactivo</span>';
      if (dev.status === 'active' && dev.enrolled_at) statusBadge = '<span class="dev-badge online">● Vinculado</span>';
      else if (dev.status === 'active') statusBadge = '<span class="dev-badge offline">◌ Sin vincular</span>';
      else if (dev.status === 'revoked') statusBadge = '<span class="dev-badge disabled">✕ Revocado</span>';
      else if (dev.status === 'disabled') statusBadge = '<span class="dev-badge disabled">⏸ Deshabilitado</span>';

      let actions = `<button type="button" class="btn-sm-action" onclick="adminModule.openEditDevice(${dev.id})"><i data-lucide="pencil"></i> Editar</button>`;
      if (dev.status === 'active') {
        actions += ` <button type="button" class="btn-sm-action" onclick="adminModule.newDeviceCode(${dev.id})" title="Código para vincular ese equipo"><i data-lucide="key-round"></i> Código</button>`;
        actions += ` <button type="button" class="btn-sm-action delete-action" onclick="adminModule.revokeDevice(${dev.id})"><i data-lucide="ban"></i> Quitar acceso</button>`;
      }

      return `
        <tr>
          <td class="adm-td-main"><strong>${esc(dev.name)}</strong><div class="adm-sub" style="font-size:11px;color:var(--text-muted);font-family:monospace">Código: ${esc(dev.device_id)}</div></td>
          <td data-label="Tipo"><span class="tag-type">${esc(DEVICE_TYPES[dev.device_type] || dev.device_type)}</span></td>
          <td data-label="Sucursal">${branchName}</td>
          <td data-label="Usuario asignado">${userName}</td>
          <td data-label="Estado">${statusBadge}</td>
          <td class="adm-td-actions" style="white-space:nowrap">${actions}</td>
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
  // El código se muestra UNA vez: el servidor solo guarda su hash.
  function showDeviceCode(dev) {
    $('deviceCodeName').textContent = dev.name;
    $('deviceCodeValue').textContent = dev.enrollment_code;
    $('modalDeviceCode').classList.add('active');
    utils.renderIcons();
  }
  $('btnCopyDeviceCode').addEventListener('click', async () => {
    try {
      await navigator.clipboard.writeText($('deviceCodeValue').textContent);
      utils.showToast('Código copiado.', 'success');
    } catch (err) {
      utils.showToast('No se pudo copiar; escríbelo a mano.', 'warning');
    }
  });
  ['closeModalDeviceCode', 'btnOkDeviceCode'].forEach((id) => $(id).addEventListener('click', () => $('modalDeviceCode').classList.remove('active')));

  adminModule.newDeviceCode = async (devId) => {
    try {
      const dev = await api.post(`/devices/${devId}/enrollment-code`, {});
      await loadDevices();
      showDeviceCode(dev);
    } catch (err) {
      utils.showToast(err.message || 'No se pudo generar el código.', 'error');
    }
  };
  adminModule.revokeDevice = async (devId) => {
    const dev = devices.find((d) => d.id === devId);
    if (!confirm(`¿Estás seguro de que quieres quitarle el acceso a ${dev ? `«${dev.name}»` : 'este dispositivo'}?\n\nDesde ese aparato ya no se podrá entrar hasta que lo vuelvas a activar en "Editar" y lo vincules con un código nuevo.`)) return;
    try {
      await api.post(`/devices/${devId}/revoke`, {});
      await loadDevices();
      utils.showToast('Acceso quitado: desde ese dispositivo ya no se puede entrar.', 'success');
    } catch (err) {
      utils.showToast(err.message || 'No se pudo revocar el dispositivo.', 'error');
    }
  };

  $('btnOpenAddDevice').addEventListener('click', () => {
    const userSelect = $('addDevUser');
    userSelect.innerHTML = '<option value="">Sin asignar (lo usa quien esté de turno)</option>' +
      usersModule.users.map((u) => `<option value="${u.id}">${esc(u.name)} (${esc(roleLabel(u))}${u.branch ? ' · ' + esc(u.branch.name) : ''})</option>`).join('');
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
      errBox.textContent = '⚠️ Elige la sucursal del dispositivo.';
      errBox.style.display = 'block';
      return;
    }
    await withSubmitBusy(e.target, async () => {
      try {
        const newDev = await api.post('/devices/', data);
        await loadDevices();
        utils.showToast(`Dispositivo «${newDev.name}» registrado.`, 'success');
        $('modalAddDevice').classList.remove('active');
        showDeviceCode(newDev);
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
        utils.showToast(`Cambios guardados en «${updated.name}».`, 'success');
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
      tbody.innerHTML = `<tr><td colspan="7" class="adm-empty">No hay sucursales registradas.</td></tr>`;
      return;
    }
    tbody.innerHTML = branchesList.map((b) => {
      const statusBadge = b.active ? '<span class="dev-badge online">● Activa</span>' : '<span class="dev-badge disabled">✕ Inactiva</span>';
      return `
        <tr>
          <td class="adm-td-main"><strong style="color:${esc(b.color || 'inherit')}">${esc(b.name)}</strong>${b.visible_to_customers === false ? '<span class="adm-tag" title="No sale en el menú digital ni en el bot de WhatsApp">Oculta a clientes</span>' : ''}</td>
          <td data-label="Código"><span class="tag-type">${esc(b.code)}</span></td>
          <td data-label="Dirección">${esc(b.address || '—')}</td>
          <td data-label="Delivery">${b.accepts_delivery ? 'Sí' : 'No'}</td>
          <td data-label="Horario">${esc(hora12(b.opens_at || '10:30'))} a ${esc(hora12(b.closes_at || '21:30'))}</td>
          <td data-label="Estado">${statusBadge}</td>
          <td class="adm-td-actions" style="white-space:nowrap">
            <button type="button" class="btn-sm-action" onclick="adminModule.openEditBranch(${b.id})"><i data-lucide="pencil"></i> Editar</button>
            <button type="button" class="btn-sm-action${b.active ? ' delete-action' : ''}" onclick="adminModule.toggleBranch(${b.id})"><i data-lucide="${b.active ? 'pause' : 'play'}"></i> ${b.active ? 'Desactivar' : 'Activar'}</button>
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
    $('editBranchVisible').checked = b.visible_to_customers !== false;
    $('editBranchOpens').value = b.opens_at || '';
    $('editBranchCloses').value = b.closes_at || '';
    $('editBranchError').style.display = 'none';
    $('modalEditBranch').classList.add('active');
  };
  // "10:30" -> "10:30 am"
  function hora12(t) {
    const [h, m] = String(t).split(':').map(Number);
    if (Number.isNaN(h)) return String(t);
    return `${(h % 12) || 12}:${String(m || 0).padStart(2, '0')} ${h < 12 ? 'am' : 'pm'}`;
  }
  adminModule.toggleBranch = async (branchId) => {
    const b = branchesList.find((x) => x.id === branchId);
    // Desactivar una sucursal la saca de todas las listas: se confirma. Activarla, no.
    if (b && b.active && !confirm(`¿Desactivar la sucursal «${b.name}»?\n\nDeja de aparecer en Inventario, Operación, reportes y en la lista de sucursales. Puedes volver a activarla cuando quieras.`)) return;
    try {
      await api.post(`/branches/${branchId}/toggle-active`, {});
      await Promise.all([loadBranchesTable(), loadBranchesForSelects()]);
      utils.showToast(b && b.active ? `Sucursal «${b.name}» desactivada.` : 'Sucursal activada.', 'success');
    } catch (err) {
      utils.showToast(err.message || 'No se pudo cambiar el estado.', 'error');
    }
  };

  $('btnOpenAddBranch').addEventListener('click', () => {
    $('formAddBranch').reset();
    $('addBranchColor').value = '#16a34a';
    $('addBranchDelivery').checked = true;
    $('addBranchVisible').checked = true;
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
      visible_to_customers: $('addBranchVisible').checked,
      opens_at: $('addBranchOpens').value || null,
      closes_at: $('addBranchCloses').value || null,
    };
    await withSubmitBusy(e.target, async () => {
      try {
        await api.post('/branches/', data);
        await Promise.all([loadBranchesTable(), loadBranchesForSelects()]);
        utils.showToast('Sucursal creada.', 'success');
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
      visible_to_customers: $('editBranchVisible').checked,
      opens_at: $('editBranchOpens').value || null,
      closes_at: $('editBranchCloses').value || null,
    };
    await withSubmitBusy(e.target, async () => {
      try {
        await api.put(`/branches/${id}`, data);
        await Promise.all([loadBranchesTable(), loadBranchesForSelects()]);
        utils.showToast('Cambios guardados en la sucursal.', 'success');
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
