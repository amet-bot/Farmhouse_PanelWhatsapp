/**
 * Farmhouse WhatsApp Center - Módulo de Gestión de Dispositivos
 * Renderizado seguro con escape de HTML contra ataques XSS (Punto 2)
 */

const devicesModule = {
  devices: [],
  heartbeatInterval: null,

  async init() {
    await this.loadDevices();

    // Auto-vincular al primer dispositivo activo de la sucursal del agente si no hay uno válido
    const user = auth.getUser();
    if (user && user.role === 'agent' && user.branch_id) {
      const currentDevId = api.getDeviceId();
      const activeBranchDevs = this.devices.filter(d => d.status === 'active' && d.branch_id === user.branch_id);
      const isCurrentValid = activeBranchDevs.some(d => d.device_id === currentDevId);

      if (!isCurrentValid && activeBranchDevs.length > 0) {
        this.useDevice(activeBranchDevs[0].device_id, false);
      }
    }

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

  renderTable() {
    const tableBody = document.getElementById('deviceTableBody');
    const btnOpenAdd = document.getElementById('btnOpenAddDevice');
    const user = auth.getUser();
    if (!tableBody) return;

    if (btnOpenAdd) {
      btnOpenAdd.style.display = (user && user.role === 'admin') ? 'inline-block' : 'none';
    }

    tableBody.innerHTML = '';
    if (this.devices.length === 0) {
      tableBody.innerHTML = `
        <tr>
          <td colspan="6" style="text-align:center;padding:24px;color:var(--muted)">
            No hay dispositivos registrados para esta sucursal.
          </td>
        </tr>
      `;
      return;
    }

    const currentDevId = api.getDeviceId();

    this.devices.forEach(dev => {
      const tr = document.createElement('tr');
      const branchName = dev.branch ? dev.branch.name : '-';
      const userName = dev.assigned_user ? dev.assigned_user.name : 'Sin asignar';
      const isCurrent = dev.device_id === currentDevId;

      let statusBadge = `<span class="dev-badge offline">○ Inactivo</span>`;
      if (dev.status === 'active') {
        statusBadge = `<span class="dev-badge online">● Activo (Autorizado)</span>`;
      } else if (dev.status === 'revoked' || dev.status === 'disabled') {
        statusBadge = `<span class="dev-badge disabled">✕ Revocado</span>`;
      }

      let actionsHtml = '';
      if (user && user.role === 'admin') {
        actionsHtml += `<button class="btn-sm-action" onclick="devicesModule.openEditModal(${dev.id})" title="Editar"><i data-lucide="pencil"></i> Editar</button> `;
        if (dev.status === 'active') {
          actionsHtml += `<button class="btn-sm-action btn-danger" onclick="devicesModule.revokeDevice(${dev.id})" title="Revocar"><i data-lucide="ban"></i> Revocar</button> `;
        }
      }

      if (isCurrent) {
        actionsHtml += `<span style="font-size:11px;color:var(--green);font-weight:700;display:inline-flex;align-items:center;gap:4px"><i data-lucide="check"></i> En Uso</span>`;
      } else if (dev.status === 'active') {
        actionsHtml += `<button class="btn-sm-action" onclick="devicesModule.useDevice('${utils.escapeHtml(dev.device_id)}')"><i data-lucide="plug"></i> Conectar</button>`;
      }

      tr.innerHTML = `
        <td>
          <strong>${utils.escapeHtml(dev.name)}</strong>
          <div style="font-size:11px;color:var(--text-muted);font-family:monospace">ID: ${utils.escapeHtml(dev.device_id)}</div>
        </td>
        <td><span class="tag-type">${utils.escapeHtml(dev.device_type)}</span></td>
        <td>${utils.escapeHtml(branchName)}</td>
        <td>${utils.escapeHtml(userName)}</td>
        <td>${statusBadge}</td>
        <td style="white-space:nowrap">${actionsHtml}</td>
      `;
      tableBody.appendChild(tr);
    });

    utils.renderIcons();
  },

  useDevice(deviceId, showNotification = true) {
    api.setDeviceId(deviceId);
    this.updateCurrentDeviceUI();
    this.renderTable();
    wsClient.disconnect();
    wsClient.connect();
    conversationsModule.loadConversations();

    const modalForbidden = document.getElementById('modalDeviceForbidden');
    if (modalForbidden) modalForbidden.classList.remove('active');
    const modalDevices = document.getElementById('modalDevicesList');
    if (modalDevices) modalDevices.classList.remove('active');

    if (showNotification) {
      utils.showToast(`Navegador vinculado al dispositivo autorizado: ${deviceId}`, 'success');
    }
  },

  updateCurrentDeviceUI() {
    const devId = api.getDeviceId();
    const dev = this.devices.find(d => d.device_id === devId);
    const topDevBadge = document.getElementById('topDevBadge');
    if (topDevBadge) {
      if (dev && dev.status === 'active') {
        topDevBadge.innerHTML = `<span class="nav-icon"><i data-lucide="laptop"></i></span> <strong>${utils.escapeHtml(dev.device_id)}</strong> <small>(${utils.escapeHtml(dev.name)})</small> <span class="status-circle" style="display:inline-block;width:6px;height:6px;margin-left:4px"></span>`;
      } else if (devId) {
        topDevBadge.innerHTML = `<span class="nav-icon"><i data-lucide="laptop"></i></span> <strong>${utils.escapeHtml(devId)}</strong>`;
      } else {
        topDevBadge.innerHTML = `<span class="nav-icon"><i data-lucide="laptop"></i></span> <strong>Sin dispositivo vinculado</strong>`;
      }
      utils.renderIcons();
    }
  },

  async registerDevice(data) {
    const newDev = await api.post('/devices/', data);
    await this.loadDevices();
    utils.showToast(`✓ Dispositivo '${newDev.name}' registrado en SQL Server.`, 'success');
    return newDev;
  },

  async updateDevice(id, data) {
    const updated = await api.put(`/devices/${id}`, data);
    await this.loadDevices();
    utils.showToast(`✓ Dispositivo '${updated.name}' actualizado.`, 'success');
    return updated;
  },

  async revokeDevice(id) {
    if (confirm('¿Estás seguro de que deseas revocar el acceso a este dispositivo?')) {
      await api.post(`/devices/${id}/revoke`, {});
      await this.loadDevices();
      utils.showToast('Acceso del dispositivo revocado.', 'info');
    }
  },

  openAddModal() {
    const userSelect = document.getElementById('addDevUser');
    if (userSelect) {
      userSelect.innerHTML = '<option value="">-- Sin asignar / Agente de turno --</option>';
      usersModule.users.forEach(u => {
        userSelect.innerHTML += `<option value="${u.id}">${utils.escapeHtml(u.name)} (${utils.escapeHtml(u.role)}${u.branch ? ' - ' + utils.escapeHtml(u.branch.name) : ''})</option>`;
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
      const devId = api.getDeviceId();
      const dev = this.devices.find(d => d.device_id === devId);
      if (dev && auth.isAuthenticated()) {
        try {
          await api.post(`/devices/${dev.id}/heartbeat`, {});
        } catch (e) {}
      }
    }, 30000);
  }
};
