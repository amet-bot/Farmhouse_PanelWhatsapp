/**
 * Farmhouse Link — Contratos (administrador y Recursos Humanos; permiso contracts.manage).
 * Datos: /contracts (routers/contracts.py).
 *
 * Cada fila es un contrato: quien renueva tiene dos. «Exportar» arma el contrato en Word en el
 * navegador (js/modules/contract_docx.js) con los datos guardados; el servidor solo los guarda.
 * La plantilla es la del personal de sucursal y sirve para Definido, Temporal e Indefinido (en el
 * indefinido cambia la cláusula de vigencia; ver contract_docx.js). Servicios Profesionales no es un
 * contrato laboral y no se exporta.
 *
 * Seguridad en esta pantalla:
 * - La tabla trabaja con un RESUMEN (sin banco, salud, contacto ni salario, cédula enmascarada). El
 *   detalle completo se pide solo al abrir un contrato o solicitud, y cada lectura queda en auditoría.
 * - Los datos completos viven en memoria solo mientras el formulario está abierto; al cerrarlo se
 *   borran.
 * - Tras 15 minutos sin tocar nada, la sesión se cierra sola.
 * - Las invitaciones son enlaces de un solo uso: el enlace se muestra una vez y no se puede recuperar.
 */
document.addEventListener('DOMContentLoaded', async () => {
  const $ = (id) => document.getElementById(id);
  const esc = (s) => utils.escapeHtml(s ?? '');

  FarmhouseShell.initTheme();
  FarmhouseShell.initLogout({ redirectTo: '/' });
  window.addEventListener('auth:unauthorized', () => { window.location.href = '/'; });

  const user = await auth.checkSession();
  if (!user) { window.location.href = '/'; return; }
  if (!(user.permissions || []).includes('contracts.manage')) {
    $('ctGate').hidden = true;
    $('ctDenied').hidden = false;
    utils.renderIcons();
    return;
  }
  $('ctGate').hidden = true;
  $('ctMain').hidden = false;
  FarmhouseShell.fillUserHeader({ nameId: 'ctAgentName', roleId: 'ctAgentRole', avatarId: 'ctAgentAvatar' }, user);

  const FIXED_TERM = ['Definido', 'Temporal'];
  const EXPORTABLE = [...FIXED_TERM, 'Indefinido'];
  let contracts = [];      // resúmenes
  let intakes = [];        // resúmenes
  let dutiesDirty = false;

  // ==========================================================================
  // Cierre de sesión por inactividad
  // ==========================================================================
  const IDLE_MS = 15 * 60 * 1000;
  let idleTimer = null;
  function armIdle() {
    clearTimeout(idleTimer);
    idleTimer = setTimeout(async () => {
      contracts = []; intakes = [];
      try { await auth.logout(); } catch (e) { /* igual se sale */ }
      window.location.href = '/';
    }, IDLE_MS);
  }
  ['click', 'keydown', 'pointerdown', 'scroll', 'touchstart'].forEach((ev) => document.addEventListener(ev, armIdle, { passive: true }));
  armIdle();

  // ==========================================================================
  // Fechas y estado
  // ==========================================================================
  const today = () => {
    const d = new Date();
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
  };
  const fmtDate = (iso) => (iso ? iso.split('-').reverse().join('/') : '');
  const daysUntil = (iso) => Math.round((new Date(`${iso}T00:00:00`) - new Date(`${today()}T00:00:00`)) / 86400000);

  /** Activo / Por vencer (a 30 días o menos) / Vencido / Programado (todavía no empieza). */
  function statusOf(c) {
    if (c.start_date > today()) return 'Programado';
    if (!c.end_date) return 'Activo';
    if (c.end_date < today()) return 'Vencido';
    return daysUntil(c.end_date) <= 30 ? 'Por vencer' : 'Activo';
  }
  const STATUS_CLASS = { 'Activo': 'ok', 'Por vencer': 'warn', 'Vencido': 'bad', 'Programado': 'info' };
  function statusNote(c, st) {
    if (!c.end_date) return '';
    const d = daysUntil(c.end_date);
    const n = Math.abs(d);
    if (st === 'Vencido') return `hace ${n} ${n === 1 ? 'día' : 'días'}`;
    if (st === 'Por vencer') return d === 0 ? 'vence hoy' : `en ${d} ${d === 1 ? 'día' : 'días'}`;
    return '';
  }
  const fullName = (c) => `${c.first_name} ${c.last_name}`.trim();
  const initials = (c) => ((c.first_name || '').charAt(0) + (c.last_name || '').charAt(0)).toUpperCase() || '?';
  const AVATAR_COLORS = ['#0f6b43', '#2563a8', '#8a4fb0', '#b4631f', '#177f86', '#a83a54'];
  function avatarColor(name) {
    let h = 0;
    for (let i = 0; i < name.length; i++) h = (h * 31 + name.charCodeAt(i)) >>> 0;
    return AVATAR_COLORS[h % AVATAR_COLORS.length];
  }
  const safeUrl = (u) => (/^https?:\/\//i.test(u || '') ? u : '');
  const fmtDateTime = (iso) => {
    const d = new Date(`${iso}Z`);
    return Number.isNaN(d.getTime()) ? '—' : d.toLocaleString('es-PA', { dateStyle: 'medium', timeStyle: 'short' });
  };

  // ==========================================================================
  // Pestañas
  // ==========================================================================
  function setTab(tab) {
    document.querySelectorAll('#ctTabs .admin-tab').forEach((b) => b.classList.toggle('active', b.dataset.tab === tab));
    document.querySelectorAll('.admin-view').forEach((v) => { v.hidden = v.dataset.view !== tab; });
  }
  document.querySelectorAll('#ctTabs .admin-tab').forEach((b) => b.addEventListener('click', () => setTab(b.dataset.tab)));

  // ==========================================================================
  // Contratos: carga y tabla
  // ==========================================================================
  async function loadContracts() {
    try {
      contracts = await api.get('/contracts');
      renderContracts();
    } catch (err) {
      $('contractTableBody').innerHTML = `<tr><td colspan="8" class="adm-empty">No se pudieron cargar los contratos. ${esc(err.message || '')}</td></tr>`;
    }
  }

  function renderKpis() {
    const count = { 'Activo': 0, 'Por vencer': 0, 'Vencido': 0 };
    contracts.forEach((c) => { const s = statusOf(c); if (s in count) count[s] += 1; });
    const current = $('ctStatusFilter').value;
    const items = [
      ['', 'Total', contracts.length, 'all'],
      ['Activo', 'Activos', count['Activo'], 'ok'],
      ['Por vencer', 'Por vencer (30 días)', count['Por vencer'], 'warn'],
      ['Vencido', 'Vencidos', count['Vencido'], 'bad'],
    ];
    $('ctKpis').innerHTML = items.map(([key, label, n, tone]) =>
      `<button type="button" class="ct-kpi ct-${tone}${key && current === key ? ' on' : ''}" data-filter="${key}"><b>${n}</b><span>${label}</span></button>`).join('');
  }

  function renderContracts() {
    renderKpis();
    const q = $('ctSearch').value.trim().toLowerCase();
    const filter = $('ctStatusFilter').value;
    const rows = contracts.filter((c) => {
      if (filter && statusOf(c) !== filter) return false;
      return !q || [fullName(c), c.position, c.contract_type].join(' ').toLowerCase().includes(q);
    });
    const body = $('contractTableBody');
    if (!rows.length) {
      body.innerHTML = `<tr><td colspan="8" class="adm-empty">${contracts.length
        ? 'Ningún contrato coincide con la búsqueda.'
        : 'Todavía no hay contratos. Crea el primero con «Nuevo contrato» o invita a un colaborador a llenar sus datos.'}</td></tr>`;
      return;
    }
    body.innerHTML = rows.map((c) => {
      const st = statusOf(c);
      const note = statusNote(c, st);
      const url = safeUrl(c.document_url);
      const exportable = EXPORTABLE.includes(c.contract_type);
      return `
        <tr>
          <td class="adm-td-main">
            <div class="ct-who">
              <span class="ct-avatar" style="background:${avatarColor(fullName(c))}">${esc(initials(c))}</span>
              <div><strong>${esc(fullName(c))}</strong><div class="adm-sub ct-sub">${esc(c.id_number_masked)}</div></div>
            </div>
          </td>
          <td data-label="Puesto">${esc(c.position)}</td>
          <td data-label="Tipo"><span class="tag-type">${esc(c.contract_type)}</span></td>
          <td data-label="Inicio">${esc(fmtDate(c.start_date))}</td>
          <td data-label="Vencimiento">${c.end_date ? `${esc(fmtDate(c.end_date))}${note ? `<div class="ct-sub">${note}</div>` : ''}` : '—'}</td>
          <td data-label="Estado"><span class="ct-badge ct-badge-${STATUS_CLASS[st]}">${st}</span></td>
          <td data-label="Documento">${url ? `<a class="adm-link-btn" href="${esc(url)}" target="_blank" rel="noopener noreferrer"><i data-lucide="link"></i> Ver</a>` : '—'}</td>
          <td class="adm-td-actions" style="white-space:nowrap">
            <button type="button" class="btn-sm-action" data-act="export" data-id="${c.id}"${exportable ? '' : ' disabled title="La plantilla es para contratos laborales (Definido, Temporal o Indefinido)"'}><i data-lucide="download"></i> Exportar</button>
            <button type="button" class="btn-sm-action" data-act="edit" data-id="${c.id}"><i data-lucide="pencil"></i> Editar</button>
            <button type="button" class="btn-sm-action delete-action" data-act="delete" data-id="${c.id}"><i data-lucide="trash-2"></i> Eliminar</button>
          </td>
        </tr>`;
    }).join('');
    utils.renderIcons();
  }

  $('ctSearch').addEventListener('input', renderContracts);
  $('ctStatusFilter').addEventListener('change', renderContracts);
  $('ctKpis').addEventListener('click', (e) => {
    const b = e.target.closest('.ct-kpi');
    if (!b) return;
    $('ctStatusFilter').value = $('ctStatusFilter').value === b.dataset.filter ? '' : b.dataset.filter;
    renderContracts();
  });

  $('contractTableBody').addEventListener('click', async (e) => {
    const b = e.target.closest('button[data-act]');
    if (!b || b.disabled) return;
    const id = Number(b.dataset.id);
    const c = contracts.find((x) => x.id === id);
    if (!c) return;
    b.disabled = true;
    try {
      if (b.dataset.act === 'delete') await deleteContract(c);
      else {
        const detail = await api.get(`/contracts/${id}`);   // el detalle completo se pide solo ahora (queda en auditoría)
        if (b.dataset.act === 'edit') openModal(detail);
        else exportContract(detail);
      }
    } catch (err) {
      utils.showToast(err.message || 'No se pudo completar la acción.', 'error');
    } finally { b.disabled = false; }
  });

  async function deleteContract(c) {
    if (!confirm(`¿Eliminar el contrato de ${fullName(c)}?\n\nSe borra para siempre. Si el contrato terminó, lo normal es dejarlo: queda como Vencido en el historial.`)) return;
    await api.delete(`/contracts/${c.id}`);
    contracts = contracts.filter((x) => x.id !== c.id);
    renderContracts();
    utils.showToast('Contrato eliminado.', 'success');
  }

  // ==========================================================================
  // Contrato en Word
  // ==========================================================================
  function docxRecord(c) {
    return {
      nombre: c.first_name, apellido: c.last_name, genero: c.gender, nacionalidad: c.nationality,
      numId: c.id_number, puesto: c.position, inicio: c.start_date,
      indefinido: c.contract_type === 'Indefinido',
      fin: c.contract_type === 'Indefinido' ? null : c.end_date,
      salario: Number(c.salary),
      tieneDep: c.dependents.length > 0,
      dependientes: c.dependents.map((d) => ({ nombre: d.name, edad: d.age == null ? '' : String(d.age), parentesco: d.relationship })),
      // Sin funciones escritas, las del puesto (las mismas que sugiere el formulario).
      funciones: c.duties || ContractDocx.funcionesPara(c.position || ''),
    };
  }
  function download(blob, filename) {
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 1500);
  }
  function exportContract(c) {
    if (FIXED_TERM.includes(c.contract_type) && !c.end_date) {
      utils.showToast('Falta la fecha de vencimiento para generar el contrato.', 'error');
      return;
    }
    try {
      download(ContractDocx.buildDocx(docxRecord(c)), `Contrato - ${fullName(c).replace(/[\\/:*?"<>|]/g, '')}.docx`);
    } catch (err) {
      console.error(err);
      utils.showToast('No se pudo generar el contrato.', 'error');
    }
  }

  // ==========================================================================
  // CSV (mismos encabezados que el Excel de datos del colaborador)
  // ==========================================================================
  const CSV_COLUMNS = [
    ['Nombre', 'first_name'], ['Apellido', 'last_name'], ['Fecha de nacimiento', 'birth_date', 'date'], ['Nacionalidad', 'nationality'],
    ['Tipo de identificacion', 'id_type'], ['Número de cedula o pasaporte', 'id_number', 'text'], ['DV', 'dv'], ['Estado Civil', 'marital_status'],
    ['Género', 'gender'], ['Teléfono', 'phone', 'text'], ['Dirección', 'address'], ['Email', 'email'], ['Tipo de sangre', 'blood_type'],
    ['Persona de contacto', 'emergency_contact_name'], ['Teléfono de contacto', 'emergency_contact_phone', 'text'],
    ['Parentesco del contacto', 'emergency_contact_relationship'],
    ['Posición', 'position'], ['Tipo de contrato', 'contract_type'], ['Fecha de contratacion', 'start_date', 'date'],
    ['Fecha de finalización de contrato', 'end_date', 'date'], ['Salario base', 'salary'],
    ['Cuenta Bancaria', 'account_number', 'text'], ['Banco', 'bank_name'], ['Tipo de cuenta', 'account_type'],
  ];
  // Un valor que empieza con = + - @ se tomaría como fórmula al abrir el CSV en Excel.
  function csvCell(v) {
    v = String(v ?? '');
    if (/^[=+@]/.test(v) || /^-[^\d]/.test(v)) v = `'${v}`;
    return `"${v.replace(/"/g, '""')}"`;
  }
  $('btnContractsCsv').addEventListener('click', async () => {
    if (!contracts.length) { utils.showToast('No hay contratos para descargar.', 'warning'); return; }
    if (!confirm('El CSV trae cédulas, cuentas bancarias, salarios y datos de salud de todo el personal, sin protección.\n\n¿Descargarlo? La descarga queda registrada.')) return;
    try {
      const all = await api.get('/contracts/export');
      const lines = [CSV_COLUMNS.map(([h]) => csvCell(h)).join(',')];
      all.forEach((c) => {
        lines.push(CSV_COLUMNS.map(([, key, kind]) => {
          const v = c[key];
          if (kind === 'date') return csvCell(fmtDate(v));
          if (kind === 'text' && v) return `="${String(v).replace(/"/g, '""')}"`;   // texto: Excel no lo vuelve número
          return csvCell(v);
        }).join(','));
      });
      download(new Blob([`﻿${lines.join('\r\n')}`], { type: 'text/csv;charset=utf-8' }), `contratos_${today()}.csv`);
    } catch (err) {
      utils.showToast(err.message || 'No se pudo descargar el CSV.', 'error');
    }
  });

  // ==========================================================================
  // Solicitudes (lo que llenaron los colaboradores)
  // ==========================================================================
  /** Va a buscar al formulario público lo que llenaron (llegan cifrados; aquí se abren) y refresca la lista. */
  async function syncIntakes({ quiet = false } = {}) {
    const note = $('ctSyncNote');
    try {
      const r = await api.post('/contracts/intakes/sync', {});
      if (!r.configured) {
        note.textContent = 'El formulario público todavía no está conectado a este sistema (faltan variables en Railway). Mientras tanto no llegarán solicitudes nuevas.';
        note.hidden = false;
      } else if (!r.ok) {
        note.textContent = 'No se pudo contactar el formulario público. Se vuelve a intentar solo; tus solicitudes no se pierden.';
        note.hidden = false;
      } else {
        note.hidden = true;
        if (!quiet && r.imported) utils.showToast(`Llegaron ${r.imported} solicitud${r.imported === 1 ? '' : 'es'} nueva${r.imported === 1 ? '' : 's'}.`, 'success');
        if (r.rejected) utils.showToast(`${r.rejected} envío${r.rejected === 1 ? '' : 's'} se descartó por datos inválidos.`, 'warning');
      }
    } catch (err) { /* la lista local igual se muestra */ }
    await loadIntakes();
  }
  $('btnRefreshIntakes').addEventListener('click', async () => {
    const b = $('btnRefreshIntakes');
    b.disabled = true;
    try { await syncIntakes(); } finally { b.disabled = false; }
  });
  setInterval(() => { if (!document.hidden) syncIntakes({ quiet: true }); }, 60000);

  async function loadIntakes() {
    try {
      intakes = await api.get('/contracts/intakes');
      renderIntakes();
    } catch (err) {
      $('intakeTableBody').innerHTML = `<tr><td colspan="5" class="adm-empty">No se pudieron cargar las solicitudes. ${esc(err.message || '')}</td></tr>`;
    }
  }
  function renderIntakes() {
    const badge = $('ctIntakeCount');
    badge.textContent = intakes.length;
    badge.hidden = intakes.length === 0;
    const body = $('intakeTableBody');
    if (!intakes.length) {
      body.innerHTML = '<tr><td colspan="5" class="adm-empty">No hay solicitudes pendientes. Cuando un colaborador llene su formulario, aparece aquí.</td></tr>';
      return;
    }
    body.innerHTML = intakes.map((i) => `
      <tr>
        <td class="adm-td-main">
          <div class="ct-who">
            <span class="ct-avatar" style="background:${avatarColor(fullName(i))}">${esc(initials(i))}</span>
            <div><strong>${esc(fullName(i))}</strong></div>
          </div>
        </td>
        <td data-label="Identificación">${esc(i.id_type === 'Cedula' ? 'Cédula' : i.id_type)} ${esc(i.id_number_masked)}</td>
        <td data-label="Origen"><span class="ct-origin${i.open_form ? ' open' : ''}">${i.open_form ? 'Formulario abierto' : 'Invitación'}</span></td>
        <td data-label="Recibida">${esc(fmtDateTime(i.created_at))}</td>
        <td class="adm-td-actions" style="white-space:nowrap">
          <button type="button" class="btn-sm-action" data-act="convert" data-id="${i.id}"><i data-lucide="file-plus"></i> Crear contrato</button>
          <button type="button" class="btn-sm-action delete-action" data-act="dismiss" data-id="${i.id}"><i data-lucide="x"></i> Descartar</button>
        </td>
      </tr>`).join('');
    utils.renderIcons();
  }
  $('intakeTableBody').addEventListener('click', async (e) => {
    const b = e.target.closest('button[data-act]');
    if (!b || b.disabled) return;
    const id = Number(b.dataset.id);
    const i = intakes.find((x) => x.id === id);
    if (!i) return;
    b.disabled = true;
    try {
      if (b.dataset.act === 'convert') {
        const detail = await api.get(`/contracts/intakes/${id}`);
        openModal(null, detail);
      } else if (confirm(`¿Descartar la solicitud de ${fullName(i)}?\n\nSe borra para siempre.`)) {
        await api.delete(`/contracts/intakes/${id}`);
        intakes = intakes.filter((x) => x.id !== id);
        renderIntakes();
        utils.showToast('Solicitud descartada.', 'success');
      }
    } catch (err) {
      utils.showToast(err.message || 'No se pudo completar la acción.', 'error');
    } finally { b.disabled = false; }
  });

  // ==========================================================================
  // Invitaciones
  // ==========================================================================
  async function loadInvites() {
    try {
      const list = await api.get('/contracts/invites');
      $('ctInvitesBox').hidden = list.length === 0;
      $('ctInvitesList').innerHTML = list.map((v) => `
        <div class="ct-invite-row">
          <span><strong>${esc(v.label || 'Sin nombre')}</strong><span class="ct-sub"> · vence ${esc(fmtDateTime(v.expires_at))}</span></span>
          <button type="button" class="btn-sm-action delete-action" data-revoke="${v.id}"><i data-lucide="ban"></i> Anular</button>
        </div>`).join('');
      utils.renderIcons();
    } catch (err) { /* la lista es un extra: si falla, el resto sigue */ }
  }
  // Formulario ABIERTO: el enlace sin token que se puede publicar (p. ej. en Instagram). Se enciende y
  // apaga aquí; apagado, el enlace deja de funcionar al instante.
  function renderOpenForm(s) {
    $('ctOpenToggle').checked = !!s.enabled;
    $('ctOpenLinkRow').hidden = !s.enabled;
    $('ctOpenLink').value = s.enabled ? s.link : '';
    $('ctOpenInfo').textContent = s.enabled
      ? `Encendido. Hoy llegaron ${s.today} de ${s.daily_cap} permitidos. Cada solicitud la revisas tú antes de crear un contrato.`
      : 'Apagado: el enlace no recibe nada. Enciéndelo cuando lo publiques.';
  }
  async function loadOpenForm() {
    try { renderOpenForm(await api.get('/contracts/open-form')); }
    catch (err) { $('ctOpenInfo').textContent = err.message || 'No se pudo consultar el formulario abierto.'; $('ctOpenToggle').disabled = true; }
  }
  $('ctOpenToggle').addEventListener('change', async () => {
    const t = $('ctOpenToggle'), want = t.checked;
    t.disabled = true;
    try {
      renderOpenForm(await api.put('/contracts/open-form', { enabled: want }));
      utils.showToast(want ? 'Formulario abierto ENCENDIDO. Ya puedes publicar el enlace.' : 'Formulario abierto apagado.', want ? 'success' : 'info');
    } catch (err) {
      t.checked = !want;
      utils.showToast(err.message || 'No se pudo cambiar el formulario abierto.', 'error');
    } finally { t.disabled = false; }
  });
  $('btnCopyOpen').addEventListener('click', async () => {
    try {
      await navigator.clipboard.writeText($('ctOpenLink').value);
      utils.showToast('Enlace copiado. Ya lo puedes pegar en Instagram.', 'success');
    } catch (err) {
      $('ctOpenLink').select();
      utils.showToast('Selecciónalo y cópialo con Ctrl+C.', 'warning');
    }
  });

  function openInvite() {
    $('formInvite').reset();
    $('ctInviteResult').hidden = true;
    $('ctInviteLink').value = '';
    $('modalInvite').classList.add('active');
    loadInvites();
    loadOpenForm();
  }
  function closeInvite() {
    $('ctInviteLink').value = '';          // el enlace no se queda en pantalla
    $('ctInviteResult').hidden = true;
    $('modalInvite').classList.remove('active');
  }
  $('btnInvite').addEventListener('click', openInvite);
  $('btnInvite2').addEventListener('click', openInvite);
  $('closeModalInvite').addEventListener('click', closeInvite);
  $('formInvite').addEventListener('submit', async (e) => {
    e.preventDefault();
    const btn = $('btnCreateInvite');
    if (btn.disabled) return;
    btn.disabled = true;
    try {
      const inv = await api.post('/contracts/invites', { label: $('ctInviteLabel').value.trim() || null, hours: parseInt($('ctInviteHours').value, 10) });
      $('ctInviteLink').value = inv.link;   // apunta al formulario público (sitio aparte); el token va en el #fragmento y no llega a ningún registro
      $('ctInviteExpiry').textContent = `Vence: ${fmtDateTime(inv.expires_at)} · sirve una sola vez.`;
      $('ctInviteResult').hidden = false;
      $('ctInviteLink').select();
      await loadInvites();
    } catch (err) {
      utils.showToast(err.message || 'No se pudo crear la invitación.', 'error');
    } finally { btn.disabled = false; }
  });
  $('btnCopyInvite').addEventListener('click', async () => {
    try {
      await navigator.clipboard.writeText($('ctInviteLink').value);
      utils.showToast('Enlace copiado. Envíaselo a la persona.', 'success');
    } catch (err) {
      $('ctInviteLink').select();
      utils.showToast('Selecciónalo y cópialo con Ctrl+C.', 'warning');
    }
  });
  $('ctInvitesList').addEventListener('click', async (e) => {
    const b = e.target.closest('button[data-revoke]');
    if (!b || !confirm('¿Anular esta invitación? El enlace dejará de funcionar.')) return;
    try {
      await api.delete(`/contracts/invites/${b.dataset.revoke}`);
      await loadInvites();
      utils.showToast('Invitación anulada.', 'success');
    } catch (err) { utils.showToast(err.message || 'No se pudo anular.', 'error'); }
  });

  // ==========================================================================
  // Formulario de contrato
  // ==========================================================================
  const NATIONALITIES = [['Panameño', 'Panameña'], ['Colombiano', 'Colombiana'], ['Venezolano', 'Venezolana'], ['Dominicano', 'Dominicana'],
    ['Italiano', 'Italiana'], ['Costarricense', 'Costarricense'], ['Nicaragüense', 'Nicaragüense'], ['Peruano', 'Peruana'],
    ['Ecuatoriano', 'Ecuatoriana'], ['Cubano', 'Cubana'], ['Mexicano', 'Mexicana'], ['Argentino', 'Argentina'], ['Español', 'Española'],
    ['Estadounidense', 'Estadounidense']];
  function fillNationalities() {
    const i = $('ctGender').value === 'F' ? 1 : 0;
    const seen = new Set();
    $('ctNationalities').innerHTML = NATIONALITIES.map((n) => n[i]).filter((n) => !seen.has(n) && seen.add(n)).map((n) => `<option value="${esc(n)}">`).join('');
  }
  $('ctGender').addEventListener('change', fillNationalities);
  const updateIdHint = () => { $('ctIdHint').textContent = $('ctIdType').value === 'Pasaporte' ? 'Ej. AV515144' : 'Ej. 8-123-456'; };
  $('ctIdType').addEventListener('change', updateIdHint);
  const updateEndRequired = () => { $('ctEndOpt').textContent = FIXED_TERM.includes($('ctType').value) ? '*' : '(opcional)'; };
  $('ctType').addEventListener('change', updateEndRequired);
  $('ctBank').addEventListener('change', () => {
    const other = $('ctBank').value === '__otro';
    $('ctBankOtherBox').hidden = !other;
    if (!other) $('ctBankOther').value = '';
  });

  const phoneMask = (el) => el.addEventListener('input', () => {
    const d = el.value.replace(/\D/g, '').slice(0, 8);
    el.value = d.length > 4 ? `${d.slice(0, 4)}-${d.slice(4)}` : d;
  });
  phoneMask($('ctPhone'));
  phoneMask($('ctContactPhone'));
  [$('ctAccount'), $('ctDv')].forEach((el) => el.addEventListener('input', () => { el.value = el.value.replace(/\D/g, ''); }));

  // Funciones sugeridas según el puesto, mientras nadie las haya editado a mano.
  function suggestDuties() {
    if (!dutiesDirty) $('ctDuties').value = $('ctPosition').value.trim() ? ContractDocx.funcionesPara($('ctPosition').value) : '';
  }
  $('ctPosition').addEventListener('input', suggestDuties);
  $('ctPosition').addEventListener('change', suggestDuties);
  $('ctDuties').addEventListener('input', () => { dutiesDirty = true; });

  function addDependentRow(d) {
    const row = document.createElement('div');
    row.className = 'ct-dep-row';
    row.innerHTML = '<input class="modal-input ct-dep-name" placeholder="Nombre completo" autocomplete="off" />' +
      '<input class="modal-input ct-dep-age" type="number" min="0" max="120" placeholder="Edad" />' +
      '<input class="modal-input ct-dep-rel" list="ctRelations" placeholder="Parentesco" autocomplete="off" />' +
      '<button type="button" class="ct-dep-remove" aria-label="Quitar dependiente">&times;</button>';
    row.querySelector('.ct-dep-name').value = d ? d.name : '';
    row.querySelector('.ct-dep-age').value = d && d.age != null ? d.age : '';
    row.querySelector('.ct-dep-rel').value = d ? d.relationship : '';
    row.querySelector('.ct-dep-remove').addEventListener('click', () => row.remove());
    $('ctDepsList').appendChild(row);
  }
  $('ctHasDeps').addEventListener('change', () => {
    const yes = $('ctHasDeps').value === 'si';
    $('ctDepsBox').hidden = !yes;
    if (yes && !$('ctDepsList').children.length) addDependentRow();
  });
  $('btnAddDependent').addEventListener('click', () => addDependentRow());

  const FIELD_IDS = {
    first_name: 'ctFirstName', last_name: 'ctLastName', birth_date: 'ctBirth', gender: 'ctGender', nationality: 'ctNationality',
    marital_status: 'ctMarital', blood_type: 'ctBlood', id_type: 'ctIdType', id_number: 'ctIdNumber', dv: 'ctDv', phone: 'ctPhone',
    email: 'ctEmail', address: 'ctAddress', emergency_contact_name: 'ctContactName', emergency_contact_phone: 'ctContactPhone',
    emergency_contact_relationship: 'ctContactRel', account_type: 'ctAccountType', account_number: 'ctAccount',
    position: 'ctPosition', contract_type: 'ctType', salary: 'ctSalary', start_date: 'ctStart', end_date: 'ctEnd',
    notes: 'ctNotes', duties: 'ctDuties', document_url: 'ctUrl',
  };

  function clearErrors() {
    $('formContract').querySelectorAll('.invalid').forEach((n) => n.classList.remove('invalid'));
    $('ctError').style.display = 'none';
  }
  $('formContract').addEventListener('input', (e) => e.target.classList.remove('invalid'));
  $('formContract').addEventListener('change', (e) => e.target.classList.remove('invalid'));

  /** `c`: contrato existente a editar. `intake`: solicitud que se convierte en contrato nuevo. */
  function openModal(c, intake) {
    clearErrors();
    $('formContract').reset();
    $('ctDepsList').innerHTML = '';
    $('ctBankOtherBox').hidden = true;
    const src = c || intake || null;
    $('ctId').value = c ? c.id : '';
    $('ctIntakeId').value = intake ? intake.id : '';
    $('ctModalTitle').innerHTML = `<i data-lucide="file-text"></i> ${c ? 'Editar contrato' : intake ? 'Crear contrato desde solicitud' : 'Nuevo contrato'}`;
    $('ctModalSub').textContent = intake ? 'Revisa los datos que llenó el colaborador y completa puesto, salario y fechas' : 'Datos del colaborador y condiciones del contrato';
    if (src) {
      Object.entries(FIELD_IDS).forEach(([key, id]) => { if (key in src) $(id).value = src[key] == null ? '' : src[key]; });
      const known = Array.from($('ctBank').options).some((o) => o.value === src.bank_name && o.value !== '__otro');
      $('ctBank').value = known ? src.bank_name : '__otro';
      if (!known) { $('ctBankOtherBox').hidden = false; $('ctBankOther').value = src.bank_name; }
      $('ctHasDeps').value = src.dependents.length ? 'si' : 'no';
      src.dependents.forEach(addDependentRow);
      dutiesDirty = Boolean(c);
    } else {
      $('ctHasDeps').value = 'no';
      dutiesDirty = false;
    }
    $('ctDepsBox').hidden = $('ctHasDeps').value !== 'si';
    $('ctBirth').max = today();
    fillNationalities();
    updateIdHint();
    updateEndRequired();
    $('modalContract').classList.add('active');
    $('formContract').querySelector('.modal-body').scrollTop = 0;
    utils.renderIcons();
  }
  function closeModal() {
    $('modalContract').classList.remove('active');
    // Los datos completos no se quedan en la página una vez cerrado el formulario.
    $('formContract').reset();
    $('ctDepsList').innerHTML = '';
    $('ctId').value = '';
    $('ctIntakeId').value = '';
  }
  $('btnNewContract').addEventListener('click', () => openModal(null));
  $('closeModalContract').addEventListener('click', closeModal);
  $('btnCancelContract').addEventListener('click', closeModal);

  function mark(id, bad) {
    $(id).classList.toggle('invalid', !!bad);
    return !!bad;
  }
  function idOk() {
    const v = $('ctIdNumber').value.trim().toUpperCase();
    if (!v) return false;
    if ($('ctIdType').value === 'Pasaporte') return /^[A-Z0-9]{5,15}$/.test(v);
    return /^(\d{1,2}|E|PE|N|PI|AV)(-\d{1,4}){2}$/.test(v) || /^(E|PE|N|PI)-\d{1,4}-\d{1,6}$/.test(v);
  }
  const phoneOk = (v) => /^\d{4}-?\d{3,4}$/.test(v);

  /** Devuelve el mensaje del primer problema, o '' si todo está bien. Marca en rojo cada campo malo. */
  function validate() {
    let bad = false;
    ['ctFirstName', 'ctLastName', 'ctNationality', 'ctAddress', 'ctContactName', 'ctContactRel', 'ctPosition'].forEach((id) => { bad = mark(id, !$(id).value.trim()) || bad; });
    ['ctGender', 'ctMarital', 'ctBlood', 'ctIdType', 'ctBank', 'ctAccountType', 'ctType'].forEach((id) => { bad = mark(id, !$(id).value) || bad; });
    const birth = $('ctBirth').value;
    bad = mark('ctBirth', !birth || birth >= today() || birth < '1900-01-01') || bad;
    bad = mark('ctIdNumber', !idOk()) || bad;
    bad = mark('ctDv', $('ctDv').value !== '' && !/^\d{1,2}$/.test($('ctDv').value)) || bad;
    bad = mark('ctPhone', !phoneOk($('ctPhone').value)) || bad;
    bad = mark('ctContactPhone', !phoneOk($('ctContactPhone').value)) || bad;
    bad = mark('ctEmail', !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test($('ctEmail').value.trim())) || bad;
    bad = mark('ctAccount', !/^\d{6,20}$/.test($('ctAccount').value)) || bad;
    if ($('ctBank').value === '__otro') bad = mark('ctBankOther', !$('ctBankOther').value.trim()) || bad;
    const salary = parseFloat($('ctSalary').value);
    bad = mark('ctSalary', !(salary > 0 && salary < 1000000)) || bad;
    const start = $('ctStart').value, end = $('ctEnd').value;
    bad = mark('ctStart', !start) || bad;
    const fixed = FIXED_TERM.includes($('ctType').value);
    bad = mark('ctEnd', (fixed && !end) || (end && start && end <= start)) || bad;
    const url = $('ctUrl').value.trim();
    bad = mark('ctUrl', url !== '' && !safeUrl(url)) || bad;
    if (fixed) bad = mark('ctDuties', !$('ctDuties').value.trim()) || bad;
    return bad ? 'Revisa los campos marcados en rojo.' : '';
  }

  function buildPayload() {
    const deps = $('ctHasDeps').value === 'si'
      ? Array.from($('ctDepsList').children).map((row) => ({
        name: row.querySelector('.ct-dep-name').value.trim(),
        age: row.querySelector('.ct-dep-age').value === '' ? null : parseInt(row.querySelector('.ct-dep-age').value, 10),
        relationship: row.querySelector('.ct-dep-rel').value.trim() || 'Dependiente',
      })).filter((d) => d.name)
      : [];
    const payload = {
      bank_name: $('ctBank').value === '__otro' ? $('ctBankOther').value.trim() : $('ctBank').value,
      salary: parseFloat($('ctSalary').value).toFixed(2),
      end_date: $('ctEnd').value || null,
      dependents: deps,
      intake_id: $('ctIntakeId').value ? Number($('ctIntakeId').value) : null,
    };
    Object.entries(FIELD_IDS).forEach(([key, id]) => {
      if (key in payload) return;
      const v = $(id).value.trim();
      payload[key] = ['dv', 'notes', 'duties', 'document_url'].includes(key) ? (v || null) : v;
    });
    return payload;
  }

  $('formContract').addEventListener('submit', async (e) => {
    e.preventDefault();
    const errBox = $('ctError');
    errBox.style.display = 'none';
    const problem = validate();
    if (problem) {
      errBox.textContent = `⚠️ ${problem}`;
      errBox.style.display = 'block';
      const first = $('formContract').querySelector('.invalid');
      if (first) first.scrollIntoView({ block: 'center', behavior: 'smooth' });
      return;
    }
    const btn = $('btnSaveContract');
    if (btn.disabled) return;
    const label = btn.innerHTML;
    btn.disabled = true;
    btn.textContent = 'Guardando…';
    const id = $('ctId').value;
    const fromIntake = $('ctIntakeId').value;
    try {
      const saved = id ? await api.put(`/contracts/${id}`, buildPayload()) : await api.post('/contracts', buildPayload());
      closeModal();
      await Promise.all([loadContracts(), fromIntake ? loadIntakes() : Promise.resolve()]);
      if (fromIntake) setTab('contratos');
      utils.showToast(id ? 'Contrato actualizado.' : `Contrato de ${fullName(saved)} guardado.`, 'success');
    } catch (err) {
      errBox.textContent = `⚠️ ${err.message}`;
      errBox.style.display = 'block';
    } finally {
      btn.disabled = false;
      btn.innerHTML = label;
    }
  });

  // ==========================================================================
  // Arranque
  // ==========================================================================
  const results = await Promise.allSettled([loadContracts(), syncIntakes({ quiet: true })]);
  results.forEach((r, i) => { if (r.status === 'rejected') utils.showToast(`No se pudieron cargar ${i ? 'las solicitudes' : 'los contratos'}.`, 'error'); });
  const requestedTab = new URLSearchParams(location.search).get('tab');
  if (requestedTab === 'solicitudes') setTab('solicitudes');
  utils.renderIcons();
});
