/**
 * Farmhouse WhatsApp Center - Inicialización y Control de la Aplicación
 */

document.addEventListener('DOMContentLoaded', async () => {
  // 1. Mapeo de elementos principales del DOM
  const modalLogin = document.getElementById('modalLogin');
  const loginForm = document.getElementById('loginForm');
  const btnLogout = document.getElementById('btnLogout');
  const themeToggle = document.getElementById('btnThemeToggle');
  const themeIconSlot = document.getElementById('themeIconSlot');
  const themeLabel = document.querySelector('#btnThemeToggle .theme-label');

  // 2. Control de Tema (Claro / Oscuro) con Íconos Lucide
  function applyTheme(theme) {
    document.documentElement.setAttribute('data-theme', theme);
    localStorage.setItem('fh_theme', theme);
    if (themeIconSlot) {
      themeIconSlot.innerHTML = `<i data-lucide="${theme === 'dark' ? 'sun' : 'moon'}"></i>`;
    }
    if (themeLabel) {
      themeLabel.textContent = theme === 'dark' ? 'Claro' : 'Oscuro';
    }
    utils.renderIcons();
  }

  const savedTheme = localStorage.getItem('fh_theme') || 'light';
  applyTheme(savedTheme);

  if (themeToggle) {
    themeToggle.addEventListener('click', () => {
      const currentTheme = document.documentElement.getAttribute('data-theme');
      const newTheme = currentTheme === 'dark' ? 'light' : 'dark';
      applyTheme(newTheme);
    });
  }

  // 2.1 Menú lateral como panel deslizante en celular (hamburguesa + fondo + botón cerrar)
  const sidebarEl = document.querySelector('.sidebar');
  const sidebarBackdrop = document.getElementById('sidebarBackdrop');
  const btnHamburger = document.getElementById('btnHamburgerMenu');
  const btnSidebarClose = document.getElementById('btnSidebarClose');

  function openSidebarDrawer() {
    sidebarEl?.classList.add('mobile-open');
    sidebarBackdrop?.classList.add('active');
  }
  function closeSidebarDrawer() {
    sidebarEl?.classList.remove('mobile-open');
    sidebarBackdrop?.classList.remove('active');
  }
  btnHamburger?.addEventListener('click', openSidebarDrawer);
  btnSidebarClose?.addEventListener('click', closeSidebarDrawer);
  sidebarBackdrop?.addEventListener('click', closeSidebarDrawer);
  // Al elegir cualquier opción del menú en celular, se cierra solo (no afecta escritorio)
  sidebarEl?.addEventListener('click', (e) => {
    if (e.target.closest('.nav-btn') || e.target.closest('.branch-btn')) {
      closeSidebarDrawer();
    }
  });

  // 3. Manejo de Modales y Autenticación
  function showLoginModal(errorMessage = '') {
    if (modalLogin) {
      modalLogin.classList.add('active');
      const errBox = document.getElementById('loginError');
      if (errBox) {
        if (errorMessage) {
          errBox.textContent = errorMessage;
          errBox.style.display = 'block';
        } else {
          errBox.style.display = 'none';
        }
      }
      utils.renderIcons();
    }
  }

  function hideLoginModal() {
    if (modalLogin) {
      modalLogin.classList.remove('active');
      const errBox = document.getElementById('loginError');
      if (errBox) errBox.style.display = 'none';
    }
  }

  // Toggle de visibilidad de contraseña en login
  const btnTogglePassword = document.getElementById('btnTogglePassword');
  const loginPasswordInput = document.getElementById('password');
  if (btnTogglePassword && loginPasswordInput) {
    btnTogglePassword.addEventListener('click', () => {
      if (loginPasswordInput.type === 'password') {
        loginPasswordInput.type = 'text';
        btnTogglePassword.innerHTML = '<i data-lucide="eye-off"></i>';
      } else {
        loginPasswordInput.type = 'password';
        btnTogglePassword.innerHTML = '<i data-lucide="eye"></i>';
      }
      utils.renderIcons();
    });
  }

  // Enviar formulario de Login
  async function handleLoginSubmit(e) {
    if (e) e.preventDefault();
    const usernameInput = document.getElementById('username') || document.getElementById('email') || document.querySelector('input[name="username"]') || document.querySelector('input[name="email"]');
    const passwordInput = document.getElementById('password') || document.querySelector('input[name="password"]');
    const username = usernameInput ? usernameInput.value.trim().toLowerCase() : '';
    const password = passwordInput ? passwordInput.value.trim() : '';
    const errBox = document.getElementById('loginError');
    const btnSubmit = document.getElementById('btnLoginSubmit');

    if (!username || !password) {
      if (errBox) {
        errBox.textContent = 'Por favor ingresa tu nombre de usuario y contraseña.';
        errBox.style.display = 'block';
      }
      return;
    }

    try {
      if (errBox) errBox.style.display = 'none';
      if (btnSubmit) {
        btnSubmit.disabled = true;
        btnSubmit.textContent = 'Validando credenciales...';
      }

      await auth.login(username, password);
      hideLoginModal();
      await initApp();
    } catch (err) {
      if (errBox) {
        errBox.textContent = err.message || 'Nombre de usuario o contraseña incorrectos.';
        errBox.style.display = 'block';
      }
    } finally {
      if (btnSubmit) {
        btnSubmit.disabled = false;
        btnSubmit.textContent = 'Iniciar sesión';
      }
    }
  }

  if (loginForm) {
    loginForm.addEventListener('submit', handleLoginSubmit);
  }
  const btnLoginSubmit = document.getElementById('btnLoginSubmit');
  if (btnLoginSubmit) {
    btnLoginSubmit.addEventListener('click', (e) => {
      if (loginForm && !loginForm.checkValidity()) {
        loginForm.reportValidity();
        return;
      }
      handleLoginSubmit(e);
    });
  }

  // Logout
  if (btnLogout) {
    btnLogout.addEventListener('click', async () => {
      await auth.logout();
      wsClient.disconnect();
      chatModule.renderEmpty();
      const passInp = document.getElementById('password');
      if (passInp) passInp.value = '';
      showLoginModal();
    });
  }

  // Eventos de Autenticación y Seguridad
  window.addEventListener('auth:unauthorized', () => {
    wsClient.disconnect();
    chatModule.renderEmpty();
    showLoginModal('Tu sesión expiró. Inicia sesión nuevamente.');
  });

  window.addEventListener('auth:device_forbidden', (e) => {
    document.getElementById('deviceForbiddenText').textContent = e.detail;
    document.getElementById('modalDeviceForbidden').classList.add('active');
  });

  document.getElementById('closeModalDeviceForbidden').addEventListener('click', () => {
    document.getElementById('modalDeviceForbidden').classList.remove('active');
    document.getElementById('modalDevicesList').classList.add('active');
    devicesModule.renderTable();
  });

  // 4. Inicialización Global de la Aplicación
  async function initApp() {
    const user = auth.getUser();
    if (!user) return;

    // Encabezado de Usuario
    document.getElementById('topAgentName').textContent = user.name;
    document.getElementById('topAgentRole').textContent = `${user.role.toUpperCase()} ${user.branch ? '• ' + user.branch.name : ''}`;
    document.getElementById('topAgentAvatar').textContent = utils.getInitials(user.name);

    // Permisos de Menú
    const navUsers = document.getElementById('navUsers');
    if (navUsers) {
      navUsers.style.display = (user.role === 'admin') ? 'flex' : 'none';
    }

    // Inicialización Secuencial de Módulos
    await branchesModule.init();
    await usersModule.init();
    await devicesModule.init();
    wsClient.connect();
    await conversationsModule.init();
    utils.renderIcons();

    // Notificaciones Push (silencioso: solo re-sincroniza si el permiso ya fue concedido antes)
    pushModule.init();
    updateNotifBellIcon();

    // Si la app fue abierta desde una notificación push (nueva pestaña), abrir esa conversación
    const urlParams = new URLSearchParams(window.location.search);
    const targetConvId = urlParams.get('conversation_id');
    if (targetConvId) {
      chatModule.loadConversation(parseInt(targetConvId));
      window.history.replaceState({}, document.title, window.location.pathname);
    }
  }

  // Estado visual de la campana de notificaciones según el permiso del navegador
  function updateNotifBellIcon() {
    const iconSlot = document.getElementById('notifBellIconSlot');
    const btn = document.getElementById('btnEnableNotifications');
    if (!iconSlot || !btn || !('Notification' in window)) return;
    if (Notification.permission === 'granted') {
      iconSlot.innerHTML = '<i data-lucide="bell-ring"></i>';
      btn.title = 'Notificaciones push activadas';
      btn.classList.add('notif-active');
    } else if (Notification.permission === 'denied') {
      iconSlot.innerHTML = '<i data-lucide="bell-off"></i>';
      btn.title = 'Notificaciones bloqueadas por el navegador. Habilítalas desde la configuración del sitio.';
    } else {
      iconSlot.innerHTML = '<i data-lucide="bell"></i>';
      btn.title = 'Activar notificaciones push';
    }
    utils.renderIcons();
  }

  const btnEnableNotifications = document.getElementById('btnEnableNotifications');
  if (btnEnableNotifications) {
    btnEnableNotifications.addEventListener('click', async () => {
      if ('Notification' in window && Notification.permission === 'denied') {
        utils.showToast('Bloqueaste las notificaciones para este sitio. Actívalas desde los ajustes del navegador.', 'warning');
        return;
      }
      const ok = await pushModule.requestPermissionAndSubscribe();
      updateNotifBellIcon();
      if (ok) {
        utils.showToast('Notificaciones push activadas para esta sucursal.', 'success');
      }
    });
  }

  // Al hacer clic en una notificación push, el Service Worker enfoca esta pestaña y avisa qué conversación abrir
  if ('serviceWorker' in navigator) {
    navigator.serviceWorker.addEventListener('message', (event) => {
      if (event.data && event.data.type === 'push_notification_click' && event.data.url) {
        const params = new URL(event.data.url, window.location.origin).searchParams;
        const convId = params.get('conversation_id');
        if (convId) chatModule.loadConversation(parseInt(convId));
      }
    });
  }

  // 5. Conexión y Eventos en Tiempo Real (WebSockets)
  wsClient.on('new_incoming_message', (data) => {
    conversationsModule.loadConversations();
    if (chatModule.currentConversation && chatModule.currentConversation.id === data.conversation_id) {
      if (!chatModule.currentConversation.messages) chatModule.currentConversation.messages = [];
      const yaExiste = chatModule.currentConversation.messages.some(m => m.id === data.message.id);
      if (!yaExiste) {
        chatModule.currentConversation.messages.push(data.message);
        chatModule.renderMessages();
      }
      // Si el mensaje es una comanda del menú o trae datos de orden, recargar la conversación para actualizar el panel de Pedido Actual
      const content = String(data.message?.content || '');
      if (content.includes('MI PEDIDO FARMHOUSE') || content.includes('Pedido: FH-')) {
        chatModule.loadConversation(data.conversation_id);
      }
    }
    utils.showToast(`Nuevo mensaje de ${data.contact_name}`, 'info');
  });

  wsClient.on('message_media_updated', (data) => {
    if (chatModule.currentConversation && chatModule.currentConversation.id === data.conversation_id) {
      if (chatModule.currentConversation.messages) {
        const msg = chatModule.currentConversation.messages.find(m => m.id === data.message_id);
        if (msg) {
          msg.media_url = data.media_url;
          msg.media_type = data.media_type;
          msg.media_mime_type = data.media_mime_type;
          // Si el backend agotó los reintentos de descarga, `media_failed` llega en true: se
          // refleja en error_detail (mismo campo que ya usa el renderer para decidir el estado
          // de error/Reintentar) para no quedarse mostrando "Descargando..." para siempre.
          if (data.media_failed) {
            msg.error_detail = 'media_download_failed';
          } else if (data.media_url) {
            msg.error_detail = null;
          }
          chatModule.renderMessages();
        }
      }
    }
    conversationsModule.loadConversations();
  });

  wsClient.on('new_outgoing_message', (data) => {
    conversationsModule.loadConversations();
    if (chatModule.currentConversation && chatModule.currentConversation.id === data.conversation_id) {
      if (!chatModule.currentConversation.messages) chatModule.currentConversation.messages = [];
      // Evitar duplicar el mensaje si esta misma pestaña fue la que lo mandó
      const yaExiste = chatModule.currentConversation.messages.some(m => m.id === data.message.id);
      if (!yaExiste) {
        chatModule.currentConversation.messages.push(data.message);
        chatModule.renderMessages();
      }
    }
  });

  wsClient.on('cart_update', (data) => {
    // El cliente está armando el pedido en /menu ahora mismo (Punto 5): se actualiza el panel
    // de "Pedido actual" sin recargar toda la conversación, para que se sienta instantáneo.
    if (!chatModule.currentConversation || chatModule.currentConversation.id !== data.conversation_id) return;
    const conv = chatModule.currentConversation;
    if (!conv.orders) conv.orders = [];

    const cart = data.cart || {};
    const isEmpty = cart.status === 'empty' || !cart.order_id;
    conv.orders = conv.orders.filter((o) => o.status !== 'carrito_activo');

    if (!isEmpty) {
      conv.orders.unshift({
        id: cart.order_id,
        order_code: cart.order_code,
        conversation_id: cart.conversation_id,
        branch_id: cart.branch_id,
        status: cart.status,
        order_type: cart.order_type,
        subtotal: cart.subtotal,
        delivery_cost: cart.delivery_fee,
        total: cart.total,
        items_json: JSON.stringify({ items: cart.items, delivery_address: cart.delivery_address, payment_method: cart.payment_method, source: 'menu_web_cart' }),
      });
    }

    chatModule.renderOrderPanel();
  });

  wsClient.on('order_created', (data) => {
    conversationsModule.loadConversations();
    branchesModule.updateCounters();
    if (chatModule.currentConversation && chatModule.currentConversation.id === data.conversation_id) {
      chatModule.loadConversation(data.conversation_id);
    }
    utils.showToast(`🧾 Nuevo pedido de ${data.contact_name}: ${data.order.order_code}`, 'success');
  });

  wsClient.on('conversation_transferred', (data) => {
    conversationsModule.loadConversations();
    branchesModule.updateCounters();
    if (chatModule.currentConversation && chatModule.currentConversation.id === data.conversation_id) {
      chatModule.loadConversation(data.conversation_id);
    }
  });

  wsClient.on('conversation_deleted', (data) => {
    conversationsModule.loadConversations();
    if (chatModule.currentConversation && chatModule.currentConversation.id === data.conversation_id) {
      chatModule.renderEmpty();
      utils.showToast('Esta conversación fue eliminada.', 'info');
    }
  });

  wsClient.on('message_deleted', (data) => {
    if (chatModule.currentConversation && chatModule.currentConversation.id === data.conversation_id) {
      if (chatModule.currentConversation.messages) {
        chatModule.currentConversation.messages = chatModule.currentConversation.messages.filter(m => m.id !== data.message_id);
        chatModule.renderMessages();
      }
    }
  });

  // 6. Formularios y Modales de Administración

  // Modales de Usuarios (Admin)
  const navUsersBtn = document.getElementById('navUsers');
  if (navUsersBtn) {
    navUsersBtn.addEventListener('click', () => {
      document.getElementById('modalUsersList').classList.add('active');
      usersModule.renderTable();
    });
  }
  document.getElementById('closeModalUsersList').addEventListener('click', () => {
    document.getElementById('modalUsersList').classList.remove('active');
  });
  document.getElementById('btnOpenAddUser').addEventListener('click', () => {
    usersModule.openAddModal();
  });
  document.getElementById('closeModalAddUser').addEventListener('click', () => {
    document.getElementById('modalAddUser').classList.remove('active');
  });
  document.getElementById('closeModalEditUser').addEventListener('click', () => {
    document.getElementById('modalEditUser').classList.remove('active');
  });
  document.getElementById('closeModalDeleteUser').addEventListener('click', () => {
    document.getElementById('modalDeleteUser').classList.remove('active');
  });
  document.getElementById('btnCancelDeleteUser').addEventListener('click', () => {
    document.getElementById('modalDeleteUser').classList.remove('active');
  });
  document.getElementById('btnConfirmDeleteUser').addEventListener('click', () => {
    usersModule.confirmDeleteUser();
  });

  document.getElementById('addUserRole').addEventListener('change', (e) => {
    const branchGroup = document.getElementById('addUserBranchGroup');
    const branchSelect = document.getElementById('addUserBranch');
    if (e.target.value === 'admin') {
      branchGroup.style.display = 'none';
      branchSelect.required = false;
    } else {
      branchGroup.style.display = 'block';
      branchSelect.required = (e.target.value === 'agent');
    }
  });

  document.getElementById('formAddUser').addEventListener('submit', async (e) => {
    e.preventDefault();
    const saveBtn = document.getElementById('btnSaveUser');
    const errBox = document.getElementById('addUserError');
    if (errBox) errBox.style.display = 'none';

    if (saveBtn && saveBtn.disabled) return;

    const showAddError = (msg) => {
      if (errBox) {
        errBox.textContent = `⚠️ ${msg}`;
        errBox.style.display = 'block';
      }
      utils.showToast(msg, 'error');
    };

    const roleVal = document.getElementById('addUserRole').value;
    const branchVal = document.getElementById('addUserBranch').value;
    const usernameVal = document.getElementById('addUserUsername').value.trim().toLowerCase();
    const nameVal = document.getElementById('addUserName').value.trim();
    const emailInput = document.getElementById('addUserEmail');
    const emailRaw = emailInput ? emailInput.value.trim() : '';
    const pwdVal = document.getElementById('addUserPassword').value.trim();

    if (!usernameVal || usernameVal.length < 2) {
      showAddError('El usuario / código de empleado es obligatorio (mínimo 2 caracteres).');
      return;
    }
    if (!nameVal || nameVal.length < 2) {
      showAddError('El nombre completo es obligatorio.');
      return;
    }
    if (!pwdVal || pwdVal.length < 4) {
      showAddError('La contraseña inicial debe tener al menos 4 caracteres.');
      return;
    }
    if (emailRaw && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(emailRaw)) {
      showAddError('El correo electrónico no tiene un formato válido.');
      return;
    }
    if (roleVal === 'agent' && !branchVal) {
      showAddError('Para un agente debes seleccionar una sucursal.');
      return;
    }

    const data = {
      username: usernameVal,
      name: nameVal,
      email: emailRaw ? emailRaw.toLowerCase() : null,
      password: pwdVal,
      role: roleVal,
      branch_id: (roleVal === 'agent' || branchVal) ? parseInt(branchVal) : null,
      active: true
    };

    const originalBtnText = saveBtn ? saveBtn.textContent : '';
    if (saveBtn) {
      saveBtn.disabled = true;
      saveBtn.textContent = 'Guardando...';
    }

    try {
      await usersModule.registerUser(data);
      if (errBox) errBox.style.display = 'none';
      document.getElementById('modalAddUser').classList.remove('active');
      document.getElementById('formAddUser').reset();
    } catch (err) {
      showAddError(`Error creando usuario: ${err.message}`);
    } finally {
      if (saveBtn) {
        saveBtn.disabled = false;
        saveBtn.textContent = originalBtnText;
      }
    }
  });

  document.getElementById('formEditUser').addEventListener('submit', async (e) => {
    e.preventDefault();
    const editErrBox = document.getElementById('editUserError');
    if (editErrBox) editErrBox.style.display = 'none';

    const showEditError = (msg) => {
      if (editErrBox) {
        editErrBox.textContent = `⚠️ ${msg}`;
        editErrBox.style.display = 'block';
      }
      utils.showToast(msg, 'error');
    };

    const id = document.getElementById('editUserId').value;
    const branchVal = document.getElementById('editUserBranch').value;
    const pwdVal = document.getElementById('editUserPassword').value.trim();
    const editEmailInput = document.getElementById('editUserEmail');
    const emailVal = editEmailInput ? editEmailInput.value.trim().toLowerCase() : null;

    if (pwdVal && pwdVal.length < 4) {
      showEditError('La nueva contraseña debe tener al menos 4 caracteres.');
      return;
    }

    const data = {
      username: document.getElementById('editUserUsername').value.trim().toLowerCase(),
      name: document.getElementById('editUserName').value.trim(),
      email: emailVal || null,
      role: document.getElementById('editUserRole').value,
      branch_id: branchVal ? parseInt(branchVal) : null,
      active: document.getElementById('editUserStatus').value === 'true'
    };
    if (pwdVal) data.password = pwdVal;

    try {
      await usersModule.updateUser(id, data);
      if (editErrBox) editErrBox.style.display = 'none';
      document.getElementById('modalEditUser').classList.remove('active');
    } catch (err) {
      showEditError(`Error actualizando usuario: ${err.message}`);
    }
  });

  // Modales de Dispositivos
  document.getElementById('navDevices').addEventListener('click', () => {
    document.getElementById('modalDevicesList').classList.add('active');
    devicesModule.renderTable();
  });
  document.getElementById('topDevBadge').addEventListener('click', () => {
    document.getElementById('modalDevicesList').classList.add('active');
    devicesModule.renderTable();
  });
  document.getElementById('closeModalDevicesList').addEventListener('click', () => {
    document.getElementById('modalDevicesList').classList.remove('active');
  });
  document.getElementById('btnOpenAddDevice').addEventListener('click', () => {
    devicesModule.openAddModal();
  });
  document.getElementById('closeModalAddDevice').addEventListener('click', () => {
    document.getElementById('modalAddDevice').classList.remove('active');
  });
  document.getElementById('closeModalEditDevice').addEventListener('click', () => {
    document.getElementById('modalEditDevice').classList.remove('active');
  });

  document.getElementById('formAddDevice').addEventListener('submit', async (e) => {
    e.preventDefault();
    const nameVal = document.getElementById('addDevName').value.trim();
    const devTypeVal = document.getElementById('addDevType').value;
    const branchVal = document.getElementById('addDevBranch').value;
    const assignedUserVal = document.getElementById('addDevUser').value;

    if (!nameVal || !branchVal) {
      utils.showToast('Por favor completa el nombre y la sucursal del dispositivo.', 'error');
      return;
    }

    const data = {
      name: nameVal,
      device_type: devTypeVal,
      branch_id: parseInt(branchVal),
      assigned_user_id: assignedUserVal ? parseInt(assignedUserVal) : null,
      status: 'active'
    };
    try {
      await devicesModule.registerDevice(data);
      document.getElementById('modalAddDevice').classList.remove('active');
    } catch (err) {
      utils.showToast(`Error registrando dispositivo: ${err.message}`, 'error');
    }
  });

  document.getElementById('formEditDevice').addEventListener('submit', async (e) => {
    e.preventDefault();
    const id = document.getElementById('editDevId').value;
    const branchVal = document.getElementById('editDevBranch').value;
    const data = {
      name: document.getElementById('editDevName').value.trim(),
      device_type: document.getElementById('editDevType').value,
      branch_id: parseInt(branchVal),
      status: document.getElementById('editDevStatus').value
    };
    try {
      await devicesModule.updateDevice(id, data);
      document.getElementById('modalEditDevice').classList.remove('active');
    } catch (err) {
      utils.showToast(`Error actualizando dispositivo: ${err.message}`, 'error');
    }
  });

  // Modal Transferencia de Conversación
  document.getElementById('closeModalTransfer').addEventListener('click', () => {
    document.getElementById('modalTransferBranch').classList.remove('active');
  });
  document.getElementById('btnCancelTransfer').addEventListener('click', () => {
    document.getElementById('modalTransferBranch').classList.remove('active');
  });
  document.getElementById('btnConfirmTransfer').addEventListener('click', () => {
    chatModule.confirmTransfer();
  });

  // Nota: el envío de mensajes (clic en botón + tecla Enter) se maneja en
  // chatModule.setupComposer(), que se re-vincula en cada conversación.
  // No duplicar el listener aquí para evitar envíos dobles a WhatsApp.

  // 7. Verificación inicial de sesión al cargar la página
  const existingUser = await auth.checkSession();
  if (existingUser) {
    hideLoginModal();
    await initApp();
  } else {
    showLoginModal();
    try {
      await branchesModule.loadBranches();
    } catch (e) {}
    utils.renderIcons();
  }
});
