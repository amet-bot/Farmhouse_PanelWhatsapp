/**
 * Operación de Sucursal: contador de tareas pendientes en el mosaico "Tareas".
 * Aparte de tablet.js a propósito: solo pinta el número, y si falla no afecta al resto.
 */
(function () {
  async function refresh() {
    const badge = document.getElementById('tareasBadge');
    if (!badge || typeof api === 'undefined') return;
    try {
      const tasks = await api.get('/ops/tasks?status=pendientes&limit=200');
      const vencidas = tasks.filter((t) => t.overdue).length;
      const total = tasks.length > 99 ? '99+' : String(tasks.length);
      // Las vencidas se escriben en el contador mismo ("3 · 1 vencida"): antes solo estaban en un
      // title, que con el dedo no se ve nunca.
      badge.textContent = vencidas ? `${total} · ${vencidas} vencida${vencidas === 1 ? '' : 's'}` : total;
      badge.hidden = tasks.length === 0;
      badge.classList.toggle('is-late', vencidas > 0);
      badge.setAttribute('aria-label', vencidas
        ? `${tasks.length} pendientes, ${vencidas} vencida${vencidas === 1 ? '' : 's'}`
        : `${tasks.length} pendiente${tasks.length === 1 ? '' : 's'}`);
    } catch (e) { /* sin sesión todavía o sin red: se intenta en el próximo ciclo */ }
  }
  document.addEventListener('DOMContentLoaded', () => {
    setTimeout(refresh, 800);
    setInterval(() => { if (!document.hidden) refresh(); }, 60000);
    document.addEventListener('visibilitychange', () => { if (!document.hidden) refresh(); });
  });
})();
