/**
 * Administración — pestañas de sistema (solo admin): Respaldos de la base de datos.
 * Datos: GET /system/backups, POST /system/backups, GET /system/backups/{nombre}.
 */
document.addEventListener('DOMContentLoaded', async () => {
  const $ = (id) => document.getElementById(id);
  const esc = (s) => utils.escapeHtml(s ?? '');
  const user = await auth.checkSession();
  if (!user || !(user.permissions || []).includes('system.backup')) return;

  const tabBtn = document.querySelector('#adminTabs .admin-tab[data-tab="respaldos"]');
  if (tabBtn) tabBtn.hidden = false;

  const size = (n) => (n >= 1048576 ? `${(n / 1048576).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1024))} KB`);
  const fmtFecha = (iso) => {
    const d = new Date(iso);
    return Number.isNaN(d.getTime()) ? '—' : d.toLocaleString('es-PA', { dateStyle: 'medium', timeStyle: 'short' });
  };
  let cargado = false;

  async function loadBackups() {
    const body = $('backupTableBody');
    try {
      const data = await api.get('/system/backups');
      $('backupNote').textContent = data.supported ? `${data.schedule} Se guardan los últimos ${data.keep_days}.` : 'Esta instalación no usa MySQL: los respaldos automáticos están apagados.';
      $('btnBackupNow').disabled = !data.supported;
      if (!data.backups.length) {
        body.innerHTML = '<tr><td colspan="6" class="adm-empty">Todavía no hay respaldos. El primero se hace esta madrugada, o tócalo ahora con "Hacer respaldo ahora".</td></tr>';
        return;
      }
      body.innerHTML = data.backups.map((b, i) => `
        <tr>
          <td class="adm-mono">${esc(b.name)}${i === 0 ? '<span class="adm-tag">el más nuevo</span>' : ''}</td>
          <td>${esc(fmtFecha(b.created_at))}</td>
          <td>${b.tables}</td>
          <td>${Number(b.rows).toLocaleString('es-PA')}</td>
          <td>${size(b.size)}</td>
          <td><button type="button" class="adm-link-btn" data-download="${esc(b.name)}"><i data-lucide="download"></i> Bajar</button></td>
        </tr>`).join('');
      utils.renderIcons();
    } catch (err) {
      body.innerHTML = `<tr><td colspan="6" class="adm-empty">No se pudieron cargar los respaldos. ${esc(err.message || '')}</td></tr>`;
    }
  }

  // Se baja con la misma sesión que usa api.js (cookie + dispositivo).
  $('backupTableBody').addEventListener('click', async (e) => {
    const b = e.target.closest('button[data-download]');
    if (!b) return;
    b.disabled = true;
    try {
      const headers = { 'X-Requested-With': 'XMLHttpRequest' };
      const deviceId = api.getDeviceId();
      if (deviceId) headers['X-Device-ID'] = deviceId;
      const res = await fetch(`${api.baseUrl}/system/backups/${encodeURIComponent(b.dataset.download)}`, { credentials: 'include', headers });
      if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || `Error ${res.status}`);
      const blob = await res.blob();
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = b.dataset.download;
      document.body.appendChild(a);
      a.click();
      setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 1000);
    } catch (err) {
      utils.showToast(err.message || 'No se pudo bajar el respaldo.', 'error');
    } finally { b.disabled = false; }
  });

  $('btnBackupNow').addEventListener('click', async () => {
    const btn = $('btnBackupNow');
    btn.disabled = true;
    const label = btn.innerHTML;
    btn.textContent = 'Respaldando…';
    try {
      const r = await api.post('/system/backups', {});
      utils.showToast(`Respaldo listo: ${r.tables} tablas, ${Number(r.rows).toLocaleString('es-PA')} filas.`, 'success');
      await loadBackups();
    } catch (err) {
      utils.showToast(err.message || 'No se pudo hacer el respaldo.', 'error');
    } finally { btn.disabled = false; btn.innerHTML = label; utils.renderIcons(); }
  });

  document.addEventListener('admin:tab', (e) => {
    if (e.detail === 'respaldos' && !cargado) { cargado = true; loadBackups(); }
  });
  if (new URLSearchParams(location.search).get('tab') === 'respaldos' && tabBtn) tabBtn.click();
});
