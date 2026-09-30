/**
 * Farmhouse Link — Tareas de la sucursal
 *
 * Lo que el encargado o la oficina mandan desde el Centro de operación, visto por el equipo:
 * primero lo que es para mí, después lo que es para todo el equipo, y al final lo asignado a
 * otros. Un botón grande "Marcar hecha" (y "Empezar" para avisar que alguien la tomó).
 * El aviso push abre /tareas?task=ID y esa tarea queda resaltada.
 *
 * Datos: GET /ops/tasks, POST /ops/tasks/{id}/status.
 */
document.addEventListener('DOMContentLoaded', async () => {
  const $ = (id) => document.getElementById(id);
  const esc = (s) => utils.escapeHtml(s ?? '');

  FarmhouseShell.initTheme();
  FarmhouseShell.initLogout({ redirectTo: '/' });
  window.addEventListener('auth:unauthorized', () => { window.location.href = '/'; });

  const user = await auth.checkSession();
  if (!user) { window.location.href = '/'; return; }
  const isGlobal = user.role === 'admin' || (user.role === 'supervisor' && !user.branch_id);
  $('tarGate').hidden = true;
  $('tarMain').hidden = false;
  FarmhouseShell.fillUserHeader({ nameId: 'tarAgentName', roleId: 'tarAgentRole', avatarId: 'tarAgentAvatar' }, user);

  const params = new URLSearchParams(location.search);
  const state = {
    branchId: user.branch_id || null, branches: [], tab: 'pendientes',
    pending: [], done: [], focusId: Number(params.get('task')) || null, busy: new Set(),
  };

  // ---- sucursal (solo quien ve todas) ----
  if (isGlobal) {
    try { state.branches = (await api.get('/branches/')).filter((b) => b.active !== false && b.code !== 'CAT'); } catch (e) { state.branches = []; }
    const sel = $('branchSelect');
    sel.innerHTML = '<option value="">Todas las sucursales</option>' + state.branches.map((b) => `<option value="${b.id}">${esc(b.name)}</option>`).join('');
    sel.hidden = false;
    const fromUrl = Number(params.get('branch'));
    state.branchId = state.branches.some((b) => b.id === fromUrl) ? fromUrl : null;
    sel.value = state.branchId || '';
    sel.addEventListener('change', () => { state.branchId = sel.value ? Number(sel.value) : null; updateScope(); load(); });
  }
  function updateScope() {
    const b = state.branches.find((x) => x.id === Number(state.branchId));
    $('tarHeaderScope').textContent = b ? b.name : (isGlobal ? 'Todas las sucursales' : (user.branch ? user.branch.name : 'Sin sucursal'));
  }
  updateScope();

  // ---- fechas ----
  const hora = (d) => d.toLocaleTimeString('es-PA', { hour: 'numeric', minute: '2-digit' });
  function dueChip(t) {
    if (!t.due_date) return '';
    const d = utils._parseServerDate(t.due_date);
    const hoy = new Date(); hoy.setHours(0, 0, 0, 0);
    const dia = new Date(d); dia.setHours(0, 0, 0, 0);
    const dias = Math.round((dia - hoy) / 86400000);
    const cuando = dias === 0 ? `hoy ${hora(d)}` : dias === 1 ? `mañana ${hora(d)}` : dias === -1 ? `ayer ${hora(d)}` : d.toLocaleDateString('es-PA', { day: 'numeric', month: 'short' }) + ` ${hora(d)}`;
    if (t.overdue) return `<span class="tar-chip late"><i data-lucide="alarm-clock"></i> Vencida · ${esc(cuando)}</span>`;
    const horas = (d - Date.now()) / 3600000;
    return `<span class="tar-chip ${horas < 3 ? 'soon' : ''}"><i data-lucide="clock"></i> Para ${esc(cuando)}</span>`;
  }
  const ago = (iso) => {
    const mins = Math.max(0, Math.round((Date.now() - utils._parseServerDate(iso)) / 60000));
    if (mins < 60) return `hace ${mins} min`;
    if (mins < 60 * 24) return `hace ${Math.round(mins / 60)} h`;
    return utils.formatDateTime(iso);
  };
  const esHoy = (iso) => { if (!iso) return false; const d = utils._parseServerDate(iso); const h = new Date(); return d.toDateString() === h.toDateString(); };

  // ---- cargar ----
  async function load() {
    const q = state.branchId ? `&branch_id=${state.branchId}` : '';
    try {
      const [pend, hechas] = await Promise.all([
        api.get(`/ops/tasks?status=pendientes&limit=200${q}`),
        api.get(`/ops/tasks?status=hecha&limit=100${q}`),
      ]);
      state.pending = pend;
      state.done = hechas.filter((t) => esHoy(t.completed_at));
      render();
    } catch (err) {
      $('taskBox').innerHTML = `<div class="ops-empty">No se pudieron cargar las tareas. ${esc(err.message || '')}</div>`;
    }
  }

  function card(t) {
    const mine = t.assigned_to_user_id === user.id;
    const para = mine ? '<span class="tar-chip mine"><i data-lucide="user-check"></i> Para ti</span>'
      : t.assigned_to_user_id ? `<span class="tar-chip"><i data-lucide="user"></i> Para ${esc(t.assigned_to_name || '—')}</span>`
      : '<span class="tar-chip"><i data-lucide="users"></i> Para todo el equipo</span>';
    const sucursal = isGlobal && !state.branchId ? `<span class="tar-chip"><i data-lucide="store"></i> ${esc(t.branch_name)}</span>` : '';
    const busy = state.busy.has(t.id) ? 'disabled' : '';
    let acciones = '';
    if (t.status === 'hecha') {
      acciones = `<div class="tar-actions single"><button type="button" class="inv-secondary-btn tar-undo" data-act="pendiente" data-id="${t.id}" ${busy}>Deshacer</button></div>`;
    } else if (t.status === 'en_proceso') {
      acciones = `<div class="tar-actions single"><button type="button" class="inv-primary-btn" data-act="hecha" data-id="${t.id}" ${busy}><i data-lucide="check"></i> Marcar hecha</button></div>`;
    } else {
      acciones = `<div class="tar-actions">
          <button type="button" class="inv-secondary-btn" data-act="en_proceso" data-id="${t.id}" ${busy}>Empezar</button>
          <button type="button" class="inv-primary-btn" data-act="hecha" data-id="${t.id}" ${busy}><i data-lucide="check"></i> Marcar hecha</button>
        </div>`;
    }
    const clases = ['tar-card', mine ? 'is-mine' : '', t.overdue && t.status !== 'hecha' ? 'is-late' : '', t.status === 'hecha' ? 'is-done' : '', state.focusId === t.id ? 'is-focus' : ''].join(' ');
    return `
      <article class="${clases}" data-task="${t.id}">
        <h3 class="tar-card-title">${esc(t.title)}</h3>
        ${t.description ? `<p class="tar-card-desc">${esc(t.description)}</p>` : ''}
        <div class="tar-meta">
          ${para}${sucursal}
          ${t.status === 'en_proceso' ? '<span class="tar-chip doing"><i data-lucide="loader"></i> En proceso</span>' : ''}
          ${t.status === 'hecha' ? `<span class="tar-chip mine"><i data-lucide="check"></i> Hecha ${esc(t.completed_at ? ago(t.completed_at) : '')}</span>` : dueChip(t)}
        </div>
        <span class="tar-from">Enviada por ${esc(t.created_by_name)} · ${esc(ago(t.created_at))}</span>
        ${acciones}
      </article>`;
  }

  function render() {
    $('countPend').textContent = state.pending.length;
    $('countDone').textContent = state.done.length;
    document.querySelectorAll('.tar-tab').forEach((b) => b.classList.toggle('active', b.dataset.tab === state.tab));
    const box = $('taskBox');
    if (state.tab === 'hechas') {
      box.innerHTML = state.done.length
        ? `<div class="tar-list">${state.done.map(card).join('')}</div>`
        : '<div class="tar-empty"><i data-lucide="clipboard-list"></i><h3>Nada hecho todavía hoy</h3><p>Cuando marques una tarea como hecha aparece aquí.</p></div>';
      utils.renderIcons();
      return;
    }
    if (!state.pending.length) {
      box.innerHTML = '<div class="tar-empty"><i data-lucide="party-popper"></i><h3>No hay tareas pendientes</h3><p>Cuando te manden una, te llega un aviso y aparece aquí.</p></div>';
      utils.renderIcons();
      return;
    }
    const mias = state.pending.filter((t) => t.assigned_to_user_id === user.id);
    const equipo = state.pending.filter((t) => !t.assigned_to_user_id);
    const otros = state.pending.filter((t) => t.assigned_to_user_id && t.assigned_to_user_id !== user.id);
    const grupo = (titulo, lista) => (lista.length ? `<p class="tar-group-title">${titulo}</p><div class="tar-list">${lista.map(card).join('')}</div>` : '');
    box.innerHTML = grupo('Para ti', mias) + grupo('Para todo el equipo', equipo) + grupo('De otros compañeros', otros);
    utils.renderIcons();
    if (state.focusId) {
      const el = box.querySelector(`[data-task="${state.focusId}"]`);
      if (el) el.scrollIntoView({ block: 'center', behavior: 'smooth' });
    }
  }

  document.querySelector('.tar-tabs').addEventListener('click', (e) => {
    const b = e.target.closest('.tar-tab');
    if (!b) return;
    state.tab = b.dataset.tab;
    render();
  });

  // ---- acciones ----
  $('taskBox').addEventListener('click', async (e) => {
    const b = e.target.closest('button[data-act]');
    if (!b) return;
    const id = Number(b.dataset.id);
    const accion = b.dataset.act;
    state.busy.add(id);
    b.disabled = true;
    try {
      const t = await api.post(`/ops/tasks/${id}/status`, { status: accion });
      state.pending = state.pending.filter((x) => x.id !== id);
      state.done = state.done.filter((x) => x.id !== id);
      if (t.status === 'hecha') { state.done.unshift(t); utils.showToast('¡Listo! Tarea marcada como hecha.', 'success'); }
      else { state.pending.unshift(t); if (accion === 'en_proceso') utils.showToast('Marcada en proceso: el equipo sabe que la tomaste.', 'info'); }
      if (state.focusId === id && t.status === 'hecha') state.focusId = null;
      render();
    } catch (err) {
      utils.showToast(err.message || 'No se pudo actualizar la tarea.', 'error');
    } finally {
      state.busy.delete(id);
      b.disabled = false;
    }
  });

  // Se refresca solo: cada minuto y al volver a la pestaña.
  setInterval(() => { if (!document.hidden) load(); }, 60000);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) load(); });

  await load();
  utils.renderIcons();
});
