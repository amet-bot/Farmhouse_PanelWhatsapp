/**
 * Farmhouse - Comunicación Interna
 *
 * Mensajería entre el personal, sin pasar por WhatsApp ni por el número enrutador. Dos formas
 * de conversación: directos entre dos personas (de cualquier sucursal, ese es el punto) y el
 * canal del equipo de cada sucursal, que el backend crea y sincroniza solo.
 *
 * Reusa el mismo WebSocket del Centro WhatsApp (ws.js): el backend manda los mensajes nuevos
 * con `type: "internal_message"` dirigidos a cada participante, así que acá alcanza con
 * escuchar ese tipo. El POST ya devuelve el mensaje creado y se pinta de inmediato; el evento
 * que vuelve por WebSocket para el propio autor se descarta por id, para no duplicar la burbuja
 * ni depender de cuál de los dos llegue primero.
 */

document.addEventListener('DOMContentLoaded', async () => {

  const REFRESH_MS = 60000;   // refresco de bandeja y presencia mientras la pestaña está a la vista
  const $ = (id) => document.getElementById(id);

  const state = {
    me: null,
    threads: [],
    people: [],
    activeThreadId: null,
    activeThread: null,
    messages: [],
    renderedIds: new Set(),
    pane: 'conversaciones',
    search: { threads: '', people: '' },
    sending: false,
  };

  const esc = (v) => utils.escapeHtml(v);

  // ==========================================================================
  // Tema y sesión
  // ==========================================================================
  const themeIconSlot = $('themeIconSlot');
  const themeLabel = document.querySelector('#btnThemeToggle .theme-label');

  function applyTheme(theme) {
    document.documentElement.setAttribute('data-theme', theme);
    localStorage.setItem('fh_theme', theme);
    if (themeIconSlot) themeIconSlot.innerHTML = `<i data-lucide="${theme === 'dark' ? 'sun' : 'moon'}"></i>`;
    if (themeLabel) themeLabel.textContent = theme === 'dark' ? 'Claro' : 'Oscuro';
    utils.renderIcons();
  }
  applyTheme(localStorage.getItem('fh_theme') || 'light');

  $('btnThemeToggle')?.addEventListener('click', () => {
    const current = document.documentElement.getAttribute('data-theme');
    applyTheme(current === 'dark' ? 'light' : 'dark');
  });

  $('btnLogout')?.addEventListener('click', async () => {
    await auth.logout();
    window.location.href = '/';
  });

  window.addEventListener('auth:unauthorized', () => {
    window.location.href = '/';
  });

  // ==========================================================================
  // Formato
  // ==========================================================================
  /** Hora suelta para la burbuja; para la bandeja alcanza con "14:32", "Ayer" o la fecha. */
  function inboxTime(iso) {
    if (!iso) return '';
    const date = utils._parseServerDate(iso);
    if (!date) return '';
    const { label } = utils.formatDateSeparator(iso);
    if (label === 'Hoy') return utils.formatTime(iso);
    if (label === 'Ayer') return 'Ayer';
    return date.toLocaleDateString('es-PA', { day: 'numeric', month: 'short' });
  }

  function avatarHtml(name, { channel = false, online = false } = {}) {
    if (channel) {
      return `<span class="int-avatar is-channel"><i data-lucide="users-round"></i></span>`;
    }
    return `<span class="int-avatar">${esc(utils.getInitials(name))}${online ? '<span class="int-presence"></span>' : ''}</span>`;
  }

  const skeleton = (rows = 5) => Array.from({ length: rows }).map(() => `
    <div class="int-skeleton-row">
      <span class="int-sk int-sk-avatar"></span>
      <span class="int-sk int-sk-line"></span>
    </div>`).join('');

  const emptyHtml = (title, text) => `
    <div class="int-empty">
      <strong>${esc(title)}</strong>
      <p>${esc(text)}</p>
    </div>`;

  // ==========================================================================
  // Pestañas de la columna izquierda
  // ==========================================================================
  document.querySelectorAll('.int-tab').forEach((tab) => {
    tab.addEventListener('click', () => {
      state.pane = tab.dataset.pane;
      document.querySelectorAll('.int-tab').forEach((t) => t.classList.toggle('active', t === tab));
      $('paneConversaciones').hidden = state.pane !== 'conversaciones';
      $('paneDirectorio').hidden = state.pane !== 'directorio';
      if (state.pane === 'directorio') loadDirectory();
      utils.renderIcons();
    });
  });

  // En celular la bandeja y la conversación se turnan como dos pantallas.
  function showChatOnMobile() {
    document.querySelector('.int-shell')?.classList.add('is-chat');
  }
  function showListOnMobile() {
    document.querySelector('.int-shell')?.classList.remove('is-chat');
  }
  $('btnChatBack')?.addEventListener('click', showListOnMobile);

  // ==========================================================================
  // Bandeja
  // ==========================================================================
  async function loadThreads({ silent = false } = {}) {
    if (!silent) $('threadList').innerHTML = skeleton();
    try {
      state.threads = await api.get('/internal/threads');
      renderThreads();
    } catch (err) {
      if (!silent) $('threadList').innerHTML = emptyHtml('No se pudo cargar', err.message || 'Intentá de nuevo en un momento.');
    }
  }

  function totalUnread() {
    return state.threads.reduce((acc, t) => acc + (t.unread_count || 0), 0);
  }

  function renderThreads() {
    const q = state.search.threads.trim().toLowerCase();
    const rows = state.threads.filter((t) =>
      !q || `${t.title} ${t.subtitle || ''} ${t.last_message_preview || ''}`.toLowerCase().includes(q)
    );

    const badge = $('tabUnread');
    const unread = totalUnread();
    badge.hidden = unread === 0;
    badge.textContent = unread > 99 ? '99+' : String(unread);
    document.title = unread ? `(${unread}) Comunicación Interna` : 'Comunicación Interna';

    const list = $('threadList');
    if (!rows.length) {
      list.innerHTML = q
        ? emptyHtml('Sin resultados', 'Ninguna conversación coincide con esa búsqueda.')
        : emptyHtml('Todavía no hay conversaciones', 'Abrí el Directorio y escribile a quien necesites.');
      return;
    }

    list.innerHTML = rows.map((t) => {
      const active = t.id === state.activeThreadId ? ' active' : '';
      const unreadCls = t.unread_count ? ' has-unread' : '';
      const preview = t.last_message_preview
        ? `${t.last_message_sender ? esc(t.last_message_sender) + ': ' : ''}${esc(t.last_message_preview)}`
        : (t.kind === 'branch' ? 'Canal del equipo' : 'Sin mensajes todavía');
      return `
        <button type="button" class="int-row${active}${unreadCls}" data-thread-id="${t.id}">
          <span class="int-row-avatar">${avatarHtml(t.title, { channel: t.kind === 'branch', online: !!(t.counterpart && t.counterpart.online) })}</span>
          <span class="int-row-main">
            <span class="int-row-name">${esc(t.title)}</span>
            <span class="int-row-preview">${preview}</span>
          </span>
          <span class="int-row-meta">
            <span class="int-row-time">${esc(inboxTime(t.last_message_at))}</span>
            ${t.unread_count ? `<span class="int-unread">${t.unread_count > 99 ? '99+' : t.unread_count}</span>` : ''}
          </span>
        </button>`;
    }).join('');

    list.querySelectorAll('.int-row').forEach((row) => {
      row.addEventListener('click', () => openThread(Number(row.dataset.threadId)));
    });
    utils.renderIcons();
  }

  $('threadSearch')?.addEventListener('input', (e) => {
    state.search.threads = e.target.value;
    renderThreads();
  });

  // ==========================================================================
  // Directorio
  // ==========================================================================
  async function loadDirectory({ silent = false } = {}) {
    if (!silent && !state.people.length) $('peopleList').innerHTML = skeleton(4);
    try {
      state.people = await api.get('/internal/directory');
      renderPeople();
    } catch (err) {
      if (!silent) $('peopleList').innerHTML = emptyHtml('No se pudo cargar', err.message || 'Intentá de nuevo.');
    }
  }

  function renderPeople() {
    const q = state.search.people.trim().toLowerCase();
    const rows = state.people.filter((p) =>
      !q || `${p.name} ${p.branch_name || ''} ${p.role}`.toLowerCase().includes(q)
    );

    const list = $('peopleList');
    if (!rows.length) {
      list.innerHTML = emptyHtml('Sin resultados', 'No hay nadie con ese nombre o sucursal.');
      return;
    }

    // Agrupado por sucursal: en una empresa multisucursal es el primer dato que se busca.
    const groups = new Map();
    rows.forEach((p) => {
      const key = p.branch_name || 'Administración';
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(p);
    });

    list.innerHTML = Array.from(groups.entries()).map(([branch, people]) => `
      <div class="int-group-label">${esc(branch)}</div>
      ${people.map((p) => `
        <button type="button" class="int-row" data-user-id="${p.id}">
          <span class="int-row-avatar">${avatarHtml(p.name, { online: p.online })}</span>
          <span class="int-row-main">
            <span class="int-row-name">${esc(p.name)}</span>
            <span class="int-row-preview">${p.online ? 'En línea' : 'Desconectado'}</span>
          </span>
          <span class="int-row-meta">
            ${p.role !== 'agent' ? `<span class="int-chip role">${esc(p.role)}</span>` : ''}
          </span>
        </button>`).join('')}
    `).join('');

    list.querySelectorAll('.int-row').forEach((row) => {
      row.addEventListener('click', () => openDirect(Number(row.dataset.userId)));
    });
    utils.renderIcons();
  }

  $('peopleSearch')?.addEventListener('input', (e) => {
    state.search.people = e.target.value;
    renderPeople();
  });

  async function openDirect(userId) {
    try {
      const thread = await api.post('/internal/threads/direct', { user_id: userId });
      if (!state.threads.some((t) => t.id === thread.id)) state.threads.unshift(thread);
      // Vuelve a Conversaciones: el directorio es para encontrar a alguien, no para quedarse.
      document.querySelector('.int-tab[data-pane="conversaciones"]')?.click();
      await openThread(thread.id);
    } catch (err) {
      utils.showToast(err.message || 'No se pudo abrir la conversación.', 'error');
    }
  }

  // ==========================================================================
  // Conversación abierta
  // ==========================================================================
  async function openThread(threadId) {
    state.activeThreadId = threadId;
    state.activeThread = state.threads.find((t) => t.id === threadId) || null;
    $('chatEmpty').hidden = true;
    $('chatLive').hidden = false;
    showChatOnMobile();
    renderChatHeader();
    renderThreads();

    $('messageList').innerHTML = skeleton(3);
    try {
      state.messages = await api.get(`/internal/threads/${threadId}/messages?limit=80`);
      state.renderedIds = new Set(state.messages.map((m) => m.id));
      renderMessages();
      await markRead(threadId);
    } catch (err) {
      $('messageList').innerHTML = emptyHtml('No se pudo cargar', err.message || 'Intentá de nuevo.');
    }
    $('composerInput')?.focus();
  }

  function renderChatHeader() {
    const t = state.activeThread;
    if (!t) return;
    const isChannel = t.kind === 'branch';
    $('chatAvatar').outerHTML = avatarHtml(t.title, {
      channel: isChannel,
      online: !!(t.counterpart && t.counterpart.online),
    }).replace('class="int-avatar', 'id="chatAvatar" class="int-avatar');
    $('chatTitle').textContent = t.title;
    $('chatSubtitle').textContent = isChannel
      ? (t.subtitle || '')
      : [t.subtitle, t.counterpart && t.counterpart.online ? 'En línea' : null].filter(Boolean).join(' · ');
    const kind = $('chatKind');
    kind.hidden = !isChannel;
    kind.textContent = 'Canal';
    utils.renderIcons();
  }

  function renderMessages() {
    const list = $('messageList');
    if (!state.messages.length) {
      list.innerHTML = emptyHtml('Conversación nueva', 'Escribí el primer mensaje.');
      return;
    }

    let lastDayKey = null;
    let lastSender = null;
    const isChannel = state.activeThread && state.activeThread.kind === 'branch';

    list.innerHTML = state.messages.map((m) => {
      const mine = m.sender_user_id === state.me.id;
      const { key, label } = utils.formatDateSeparator(m.created_at);
      let html = '';
      if (key !== lastDayKey) {
        html += `<div class="int-day">${esc(label)}</div>`;
        lastDayKey = key;
        lastSender = null;
      }
      // El nombre solo encabeza el primer mensaje de una tanda del mismo autor, y solo en
      // canales: en un directo ya se sabe quiénes son los dos.
      const showAuthor = isChannel && !mine && m.sender_user_id !== lastSender;
      lastSender = m.sender_user_id;
      html += `
        <div class="int-msg${mine ? ' mine' : ''}">
          ${showAuthor ? `<span class="int-msg-author">${esc(m.sender_name)}</span>` : ''}
          <div class="int-bubble">${esc(m.body)}</div>
          <span class="int-msg-time">${esc(utils.formatTime(m.created_at))}</span>
        </div>`;
      return html;
    }).join('');

    list.scrollTop = list.scrollHeight;
  }

  async function markRead(threadId) {
    try {
      await api.post(`/internal/threads/${threadId}/read`, {});
    } catch (err) { /* no bloquea la lectura */ }
    const thread = state.threads.find((t) => t.id === threadId);
    if (thread) {
      thread.unread_count = 0;
      renderThreads();
    }
  }

  // ==========================================================================
  // Enviar
  // ==========================================================================
  const composerInput = $('composerInput');

  function autoGrow() {
    composerInput.style.height = 'auto';
    composerInput.style.height = `${Math.min(composerInput.scrollHeight, 160)}px`;
  }

  composerInput?.addEventListener('input', autoGrow);

  composerInput?.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      $('composer').requestSubmit();
    }
  });

  $('composer')?.addEventListener('submit', async (e) => {
    e.preventDefault();
    const body = composerInput.value.trim();
    if (!body || state.sending || !state.activeThreadId) return;

    state.sending = true;
    $('btnSend').disabled = true;
    try {
      const message = await api.post(`/internal/threads/${state.activeThreadId}/messages`, { body });
      composerInput.value = '';
      autoGrow();
      appendMessage(message);
      bumpThread(state.activeThreadId, message, { mine: true });
    } catch (err) {
      utils.showToast(err.message || 'No se pudo enviar el mensaje.', 'error');
    } finally {
      state.sending = false;
      $('btnSend').disabled = false;
      composerInput.focus();
    }
  });

  /** Agrega una burbuja si no estaba ya (el POST y el evento de WebSocket traen la misma). */
  function appendMessage(message) {
    if (state.renderedIds.has(message.id)) return;
    state.renderedIds.add(message.id);
    state.messages.push(message);
    renderMessages();
  }

  /** Refleja el último mensaje en la bandeja sin volver a pedir la lista entera al servidor. */
  function bumpThread(threadId, message, { mine = false } = {}) {
    const thread = state.threads.find((t) => t.id === threadId);
    if (!thread) {
      loadThreads({ silent: true });
      return;
    }
    thread.last_message_at = message.created_at;
    thread.last_message_preview = message.body.length > 90 ? `${message.body.slice(0, 90).trimEnd()}…` : message.body;
    thread.last_message_sender = mine ? 'Vos' : (message.sender_name || '').split(' ')[0];
    if (!mine && threadId !== state.activeThreadId) {
      thread.unread_count = (thread.unread_count || 0) + 1;
    }
    // _parseServerDate y no `new Date` a secas: el backend serializa en UTC pero sin marcarlo
    // ("2026-09-21T23:01:00", sin Z), y `new Date` lo leería como hora local. Mezclar las dos
    // lecturas desordena la bandeja por la diferencia horaria.
    const at = (t) => (utils._parseServerDate(t.last_message_at) || new Date(0)).getTime();
    state.threads.sort((a, b) => at(b) - at(a));
    renderThreads();
  }

  // ==========================================================================
  // Tiempo real
  // ==========================================================================
  function setConnection(status) {
    const el = $('intConn');
    if (!el) return;
    el.classList.toggle('online', status === 'connected');
    el.classList.toggle('offline', status === 'disconnected');
    el.querySelector('.int-conn-label').textContent =
      status === 'connected' ? 'En vivo' : (status === 'disconnected' ? 'Sin conexión' : 'Conectando');
  }

  wsClient.on('connected', () => {
    setConnection('connected');
    loadThreads({ silent: true });   // recupera lo que haya entrado mientras no había conexión
    if (state.pane === 'directorio') loadDirectory({ silent: true });
  });

  wsClient.on('disconnected', () => setConnection('disconnected'));

  wsClient.on('internal_message', (data) => {
    const message = data.message;
    if (!message) return;
    if (data.thread_id === state.activeThreadId) {
      appendMessage(message);
      if (message.sender_user_id !== state.me.id) markRead(data.thread_id);
    }
    bumpThread(data.thread_id, message, { mine: message.sender_user_id === state.me.id });
  });

  // Presencia y bandeja se refrescan solos mientras la pestaña esté a la vista. La presencia
  // vive en las conexiones abiertas del servidor, así que no hay evento que avise de un cambio.
  setInterval(() => {
    if (document.hidden) return;
    loadThreads({ silent: true });
    if (state.pane === 'directorio') loadDirectory({ silent: true });
  }, REFRESH_MS);

  // ==========================================================================
  // Arranque
  // ==========================================================================
  const existingUser = await auth.checkSession();
  if (!existingUser) {
    window.location.href = '/';
    return;
  }

  state.me = existingUser;
  $('intGate').hidden = true;
  $('intMain').hidden = false;
  $('intAgentName').textContent = existingUser.name;
  $('intAgentRole').textContent = `${existingUser.role.toUpperCase()}${existingUser.branch ? ' • ' + existingUser.branch.name : ''}`;
  $('intAgentAvatar').textContent = utils.getInitials(existingUser.name);
  $('intHeaderScope').textContent = existingUser.branch
    ? `Equipo de ${existingUser.branch.name} y el resto de las sucursales`
    : 'Todas las sucursales';

  await Promise.all([loadThreads(), loadDirectory({ silent: true })]);
  wsClient.connect();
  utils.renderIcons();
});
