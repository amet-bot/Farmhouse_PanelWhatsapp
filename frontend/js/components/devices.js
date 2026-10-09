/**
 * Farmhouse WhatsApp Center - Módulo de Gestión de Dispositivos
 * Renderizado seguro con escape de HTML contra ataques XSS (Punto 2)
 *
 * Vinculación: un equipo solo queda autorizado cuando alguien escribe en él, una vez, el código
 * de vinculación que generó el administrador (POST /devices/enroll). El navegador recibe un token
 * secreto que guarda api.setDeviceId(); el código público FH-DEVICE-… es solo la etiqueta.
 * Ya no hay auto-vinculación: antes bastaba estar en la lista para "conectarse".
 */

const devicesModule = {
  devices: [],
  heartbeatInterval: null,

  async init() {
    await this.loadDevices();
    this.updateCurrentDeviceUI();
    this.startHeartbeat();
  },

  async loadDevices(branchId = null) {
    try {
      const user = auth.getUser();
      let endpoint = '/devices/?limit=100';
      if (branchId) {
        endpoint = `/devices/?branch_id=${branchId}&limit=100`;
      } else if (user && user.role === 'agent' && user.branch_id) {
        endpoint = `/devices/?branch_id=${user.branch_id}&limit=100`;
      }
      this.devices = await api.get(endpoint);
      this.renderTable();
      return this.devices;
    } catch (e) {
      console.error('Error cargando dispositivos:', e);
      return [];
    }
  },

  /** El equipo al que está vinculado ESTE navegador, según el código guardado al vincular. */
  currentDevice() {
    const code = api.getDeviceCode();
    return code ? this.devices.find(d => d.device_id === code) : null;
  },

  renderTable() {
    const tableBody = document.getElementById('deviceTableBody');
    const btnOpenAdd = document.getElementById('btnOpenAddDevice');
    const user = auth.getUser();
    if (!tableBody) return;

    if (btnOpenAdd) {
      btnOpenAdd.style.display = (user && user.role === 'admin') ? 'inline-block' : 'none';
    }

    this.renderEnrollBox();

    tableBody.innerHTML = '';
    if (this.devices.length === 0) {
      tableBody.innerHTML = `
        <tr>
          <td class="u-empty-cell" colspan="6">
            No hay dispositivos registrados para esta sucursal.
          </td>
        </tr>
      `;
      return;
    }

    const currentCode = api.getDeviceCode();

    this.devices.forEach(dev => {
      const tr = document.createElement('tr');
      const branchName = dev.branch ? dev.branch.name : '-';
      const userName = dev.assigned_user ? dev.assigned_user.name : 'Sin asignar';
      const isCurrent = dev.device_id === currentCode && !!api.getDeviceId();

      let statusBadge = `<span class="dev-badge offline">○ Inactivo</span>`;
      if (dev.status === 'active' && dev.enrolled_at) {
        statusBadge = `<span class="dev-badge online">● Vinculado</span>`;
      } else if (dev.status === 'active') {
        statusBadge = `<span class="dev-badge offline">◌ Sin vincular</span>`;
      } else if (dev.status === 'revoked' || dev.status === 'disabled') {
        statusBadge = `<span class="dev-badge disabled">✕ Revocado</span>`;
      }

      let actionsHtml = '';
      if (user && user.role === 'admin') {
        actionsHtml += `<button class="btn-sm-action" onclick="devicesModule.openEditModal(${dev.id})" title="Editar"><i data-lucide="pencil"></i> Editar</button> `;
        if (dev.status === 'active') {
          actionsHtml += `<button class="btn-sm-action" onclick="devicesModule.newEnrollCode(${dev.id})" title="Código para vincular este equipo"><i data-lucide="key-round"></i> Código</button> `;
          actionsHtml += `<button class="btn-sm-action delete-action" onclick="devicesModule.revokeDevice(${dev.id})" title="Revocar"><i data-lucide="ban"></i> Revocar</button> `;
        }
      }

      if (isCurrent) {
        actionsHtml += `<span class="u-flag-ok"><i data-lucide="check"></i> Este equipo</span>`;
      }

      tr.innerHTML = `
        <td>
          <strong>${utils.escapeHtml(dev.name)}</strong>
          <div class="u-mono u-muted">ID: ${utils.escapeHtml(dev.device_id)}</div>
        </td>
        <td><span class="tag-type">${utils.escapeHtml(dev.device_type)}</span></td>
        <td>${utils.escapeHtml(branchName)}</td>
        <td>${utils.escapeHtml(userName)}</td>
        <td>${statusBadge}</td>
        <td class="u-nowrap">${actionsHtml}</td>
      `;
      tableBody.appendChild(tr);
    });

    utils.renderIcons();
  },

  /** Caja "Vincular este equipo" del modal de dispositivos: estado actual + campo del código. */
  renderEnrollBox() {
    const box = document.getElementById('deviceEnrollBox');
    if (!box) return;
    const dev = this.currentDevice();
    const linked = dev && api.getDeviceId();
    const status = document.getElementById('deviceEnrollStatus');
    if (status) {
      status.innerHTML = linked
        ? `<i data-lucide="shield-check"></i> Este navegador está vinculado a <strong>${utils.escapeHtml(dev.name)}</strong> <span style="font-family:monospace;color:var(--text-muted)">${utils.escapeHtml(dev.device_id)}</span>.`
        : `<i data-lucide="shield-alert"></i> Este navegador <strong>no está vinculado</strong>. Escribe el código que te dio el administrador.`;
    }
    const unlinkBtn = document.getElementById('btnUnlinkDevice');
    if (unlinkBtn) unlinkBtn.style.display = linked ? 'inline-flex' : 'none';
  },

  /** Canjea el código de vinculación por el token de este equipo. */
  async enroll(code) {
    const clean = (code || '').trim();
    if (!clean) {
      utils.showToast('Escribe el código de vinculación.', 'error');
      return false;
    }
    try {
      const res = await api.post('/devices/enroll', { code: clean });
      api.setDeviceId(res.device_token);
      api.setDeviceCode(res.device.device_id);
      await this.loadDevices();
      this.updateCurrentDeviceUI();
      wsClient.disconnect();
      wsClient.connect();
      if (typeof conversationsModule !== 'undefined') conversationsModule.loadConversations();

      const modalForbidden = document.getElementById('modalDeviceForbidden');
      if (modalForbidden) modalForbidden.classList.remove('active');
      const modalDevices = document.getElementById('modalDevicesList');
      if (modalDevices) modalDevices.classList.remove('active');
      utils.showToast(`Equipo vinculado: ${res.device.name}`, 'success');
      return true;
    } catch (e) {
      utils.showToast(e.message, 'error');
      return false;
    }
  },

  /** Olvida el token de este navegador (p. ej. una computadora prestada). El admin no necesita hacer nada. */
  unlink() {
    if (!confirm('¿Desvincular este navegador? Para volver a entrar como equipo autorizado hará falta un código nuevo.')) return;
    api.setDeviceId('');
    api.setDeviceCode('');
    this.updateCurrentDeviceUI();
    this.renderTable();
    wsClient.disconnect();
    utils.showToast('Navegador desvinculado.', 'info');
  },

  updateCurrentDeviceUI() {
    const dev = this.currentDevice();
    const linked = dev && api.getDeviceId();
    const topDevBadge = document.getElementById('topDevBadge');
    if (topDevBadge) {
      if (linked && dev.status === 'active') {
        topDevBadge.innerHTML = `<span class="nav-icon"><i data-lucide="laptop"></i></span> <strong>${utils.escapeHtml(dev.device_id)}</strong> <small>(${utils.escapeHtml(dev.name)})</small> <span class="status-circle" style="display:inline-block;width:6px;height:6px;margin-left:4px"></span>`;
      } else {
        topDevBadge.innerHTML = `<span class="nav-icon"><i data-lucide="laptop"></i></span> <strong>Sin dispositivo vinculado</strong>`;
      }
      utils.renderIcons();
    }
  },

  /** Muestra el código de vinculación UNA vez (el servidor no lo vuelve a entregar). */
  showEnrollCode(dev) {
    const modal = document.getElementById('modalDeviceCode');
    if (!modal) {
      alert(`Código de vinculación de ${dev.name}: ${dev.enrollment_code}\nEscríbelo en ese equipo en Dispositivos → "Vincular este equipo". Vale 24 horas.`);
      return;
    }
    document.getElementById('deviceCodeName').textContent = dev.name;
    document.getElementById('deviceCodeValue').textContent = dev.enrollment_code;
    modal.classList.add('active');
    const copyBtn = document.getElementById('btnCopyDeviceCode');
    if (copyBtn) {
      copyBtn.onclick = async () => {
        try {
          await navigator.clipboard.writeText(dev.enrollment_code);
          utils.showToast('Código copiado.', 'success');
        } catch (e) {
          utils.showToast('No se pudo copiar; escríbelo a mano.', 'warning');
        }
      };
    }
  },

  async registerDevice(data) {
    const newDev = await api.post('/devices/', data);
    await this.loadDevices();
    utils.showToast(`✓ Dispositivo '${newDev.name}' registrado.`, 'success');
    this.showEnrollCode(newDev);
    return newDev;
  },

  async newEnrollCode(id) {
    try {
      const dev = await api.post(`/devices/${id}/enrollment-code`, {});
      await this.loadDevices();
      this.showEnrollCode(dev);
    } catch (e) {
      utils.showToast(`No se pudo generar el código: ${e.message}`, 'error');
    }
  },

  async updateDevice(id, data) {
    const updated = await api.put(`/devices/${id}`, data);
    await this.loadDevices();
    utils.showToast(`✓ Dispositivo '${updated.name}' actualizado.`, 'success');
    return updated;
  },

  async revokeDevice(id) {
    if (!confirm('¿Estás seguro de que deseas revocar el acceso a este dispositivo?')) return;
    // Sin try/catch un fallo (sin permiso, red) quedaba como promesa rechazada sin aviso.
    try {
      await api.post(`/devices/${id}/revoke`, {});
      await this.loadDevices();
      utils.showToast('Acceso del dispositivo revocado.', 'info');
    } catch (e) {
      utils.showToast(`No se pudo revocar el dispositivo: ${e.message}`, 'error');
    }
  },

  openAddModal() {
    const userSelect = document.getElementById('addDevUser');
    if (userSelect) {
      userSelect.innerHTML = '<option value="">-- Sin asignar / Agente de turno --</option>';
      usersModule.users.forEach(u => {
        const rol = (typeof usersModule.roleLabel === 'function') ? usersModule.roleLabel(u) : u.role;
        userSelect.innerHTML += `<option value="${u.id}">${utils.escapeHtml(u.name)} (${utils.escapeHtml(rol)}${u.branch ? ' - ' + utils.escapeHtml(u.branch.name) : ''})</option>`;
      });
    }
    document.getElementById('formAddDevice').reset();
    document.getElementById('modalAddDevice').classList.add('active');
  },

  openEditModal(devId) {
    const dev = this.devices.find(d => d.id === devId);
    if (!dev) return;

    document.getElementById('editDevId').value = dev.id;
    document.getElementById('editDevName').value = dev.name;
    document.getElementById('editDevType').value = dev.device_type;
    document.getElementById('editDevBranch').value = dev.branch_id;
    document.getElementById('editDevStatus').value = dev.status;

    document.getElementById('modalEditDevice').classList.add('active');
  },

  startHeartbeat() {
    if (this.heartbeatInterval) clearInterval(this.heartbeatInterval);
    this.heartbeatInterval = setInterval(async () => {
      const dev = this.currentDevice();
      if (dev && api.getDeviceId() && auth.isAuthenticated()) {
        try {
          await api.post(`/devices/${dev.id}/heartbeat`, {});
        } catch (e) {}
      }
    }, 30000);
  }
};
