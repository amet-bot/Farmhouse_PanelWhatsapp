/**
 * Farmhouse Link — Componentes del Panel General (hub)
 *
 * El proyecto no usa framework (JS plano cargado con <script>), así que los "componentes" son
 * funciones puras que devuelven HTML: cada una arma una pieza de la pantalla y hub.js decide qué
 * datos pasarles. Todo texto que viene del servidor pasa por utils.escapeHtml.
 *
 *   DesktopSidebar   → sidebarNav(items, activeId)
 *   ModuleCard       → moduleCard(module)            (grilla de escritorio)
 *   BrandCard        → brandCard(span)
 *   MobileModuleCard → mobileModuleCard(module)      (accesos rápidos 2x2)
 *   RecentActivity   → activityRow(entry) / activityEmpty()
 *   MobilePendingList→ pendingRow(entry) / pendingEmpty()
 *   AppSwitcher      → appSwitcher(items, activeId)  (sistema abierto: cambiar a otro)
 *   AppNav           → appNavList(entries) / appNavLoading()  (menú del sistema abierto)
 *   Ilustraciones    → heroArt(), leafArt()
 */
(function () {
  const esc = (s) => utils.escapeHtml(s ?? '');
  const icon = (name) => `<i data-lucide="${esc(name)}" aria-hidden="true"></i>`;

  function sidebarNav(items, activeId) {
    return items.map((item) => {
      const active = item.id === activeId;
      return `
        <a class="hub-nav-item${active ? ' active' : ''}" href="${esc(item.route)}" data-nav-id="${esc(item.id)}"
           ${active ? 'aria-current="page"' : ''} title="${esc(item.label)}">
          ${icon(item.icon)}<span>${esc(item.label)}</span>
        </a>`;
    }).join('');
  }

  function moduleCard(m) {
    return `
      <a class="hub-module-card" href="${esc(m.route)}" data-module-id="${esc(m.id)}"
         data-search="${esc(`${m.name} ${m.description} ${m.tags.join(' ')}`.toLowerCase())}">
        <span class="hub-module-top">
          <span class="hub-module-icon">${icon(m.icon)}</span>
          <span class="hub-module-arrow">${icon('chevron-right')}</span>
        </span>
        <strong class="hub-module-title">${esc(m.name)}</strong>
        <span class="hub-module-desc">${esc(m.description)}</span>
        <span class="hub-module-tags">${m.tags.map((t) => `<span class="hub-tag">${esc(t)}</span>`).join('')}</span>
      </a>`;
  }

  /** `span` = cuántas columnas ocupa (completa la última fila de la grilla de 3). */
  function brandCard(span) {
    return `
      <div class="hub-brand-card" style="--span:${Number(span) || 1}" data-span="${Number(span) || 1}" aria-hidden="true">
        <p>Un sistema<br>para hacer crecer<br> tu operación</p>
        <span class="hub-brand-bar"></span>
        <div class="hub-brand-art">${leafArt()}</div>
      </div>`;
  }

  function mobileModuleCard(m) {
    return `
      <a class="hub-m-card" href="${esc(m.route)}">
        <span class="hub-m-card-top">
          <span class="hub-module-icon hub-module-icon-lg">${icon(m.icon)}</span>
          <span class="hub-m-card-arrow">${icon('chevron-right')}</span>
        </span>
        <strong>${esc(m.shortName || m.name)}</strong>
        <small>${esc(m.shortDescription || m.description)}</small>
      </a>`;
  }

  /** entry: { icon, tone, title, subtitle, when, badge, badgeTone, route } */
  function activityRow(e) {
    return `
      <a class="hub-activity-row" href="${esc(e.route)}">
        <span class="hub-activity-icon tone-${esc(e.tone)}">${icon(e.icon)}</span>
        <span class="hub-activity-dot tone-${esc(e.tone)}" aria-hidden="true"></span>
        <span class="hub-activity-text">
          <strong>${esc(e.title)}</strong>
          <small>${esc(e.subtitle)}</small>
        </span>
        <time class="hub-activity-when">${esc(e.when)}</time>
        <span class="hub-badge tone-${esc(e.badgeTone || e.tone)}">${esc(e.badge)}</span>
      </a>`;
  }

  function activityEmpty(text) {
    return `<p class="hub-block-empty">${icon('sprout')}<span>${esc(text)}</span></p>`;
  }

  /** entry: { icon, tone, title, subtitle, route } */
  function pendingRow(e) {
    return `
      <a class="hub-pending-row" href="${esc(e.route)}">
        <span class="hub-activity-icon tone-${esc(e.tone)}">${icon(e.icon)}</span>
        <span class="hub-activity-text">
          <strong>${esc(e.title)}</strong>
          <small>${esc(e.subtitle)}</small>
        </span>
        <span class="hub-pending-arrow">${icon('chevron-right')}</span>
      </a>`;
  }

  /** Fila de íconos para saltar de un sistema a otro con uno ya abierto (sin pasar por Inicio). */
  function appSwitcher(items, activeId) {
    return items.map((item) => {
      const active = item.id === activeId;
      return `
        <a class="hub-switch-item${active ? ' active' : ''}" href="${esc(item.route)}" data-nav-id="${esc(item.id)}"
           ${active ? 'aria-current="page"' : ''} title="${esc(item.label)}" aria-label="${esc(item.label)}">
          ${icon(item.icon)}
        </a>`;
    }).join('');
  }

  /**
   * Menú del sistema abierto, leído de la página de ese sistema (hub.js). entries:
   *   { type: 'section', label }
   *   { type: 'item', idx, label, icon, dot, badge, active, disabled, soon, soonTag }
   * `idx` es la posición del botón original: el clic se le reenvía a ese botón.
   */
  function appNavList(entries) {
    if (!entries.length) return '';
    return entries.map((e) => {
      if (e.type === 'section') return `<p class="hub-appnav-section">${esc(e.label)}</p>`;
      const lead = e.icon
        ? icon(e.icon)
        : `<span class="hub-appnav-dot" style="background:${esc(e.dot || 'currentColor')}" aria-hidden="true"></span>`;
      const tail = e.soon
        ? (e.soonTag ? '<span class="hub-appnav-soon">Pronto</span>' : '')
        : (e.badge !== '' && e.badge != null ? `<span class="hub-appnav-badge">${esc(e.badge)}</span>` : '');
      return `
        <button type="button" class="hub-appnav-item${e.active ? ' active' : ''}${e.soon || e.disabled ? ' soon' : ''}" data-idx="${Number(e.idx)}"
                title="${esc(e.label)}"${e.active ? ' aria-current="true"' : ''}${e.disabled ? ' disabled' : ''}>
          ${lead}<span class="hub-appnav-label">${esc(e.label)}</span>${tail}
        </button>`;
    }).join('');
  }

  function appNavLoading() {
    return '<span class="hub-appnav-skel"></span><span class="hub-appnav-skel"></span><span class="hub-appnav-skel"></span>';
  }

  // ---- Ilustraciones: SVG en línea, en los tonos del tema (currentColor/variables) ----
  function heroArt() {
    return `
      <svg viewBox="0 0 420 150" preserveAspectRatio="xMaxYMax meet" focusable="false">
        <circle cx="352" cy="34" r="16" class="art-sun"/>
        <path d="M0 150 C 70 96, 150 118, 230 92 S 360 70, 420 84 L420 150 Z" class="art-hill-back"/>
        <path d="M60 150 C 140 112, 220 132, 300 108 S 390 100, 420 110 L420 150 Z" class="art-hill-mid"/>
        <g class="art-field">
          <path d="M150 150 L 250 116" /><path d="M190 150 L 272 118" /><path d="M232 150 L 296 120" />
          <path d="M276 150 L 318 122" /><path d="M318 150 L 340 124" />
        </g>
        <g class="art-barn">
          <path d="M262 92 L 292 70 L 322 92 Z" class="art-roof"/>
          <rect x="268" y="92" width="48" height="26" rx="2" class="art-wall"/>
          <rect x="286" y="100" width="12" height="18" rx="1" class="art-door"/>
          <rect x="272" y="97" width="8" height="7" rx="1" class="art-door"/>
          <rect x="304" y="97" width="8" height="7" rx="1" class="art-door"/>
        </g>
        <g class="art-trees">
          <ellipse cx="236" cy="96" rx="11" ry="15"/><rect x="235" y="108" width="2" height="8"/>
          <ellipse cx="340" cy="86" rx="8" ry="20"/><rect x="339" y="102" width="2" height="10"/>
          <ellipse cx="362" cy="84" rx="7" ry="22"/><rect x="361" y="102" width="2" height="10"/>
          <ellipse cx="384" cy="88" rx="9" ry="18"/><rect x="383" y="102" width="2" height="9"/>
          <ellipse cx="206" cy="108" rx="9" ry="11"/>
        </g>
      </svg>`;
  }

  function leafArt() {
    return `
      <svg viewBox="0 0 120 140" focusable="false">
        <path d="M60 138 C 58 100, 60 70, 66 40" class="art-stem"/>
        <path d="M64 70 C 30 66, 14 40, 18 14 C 46 18, 66 40, 64 70 Z" class="art-leaf"/>
        <path d="M66 52 C 92 44, 110 22, 108 2 C 84 4, 64 22, 66 52 Z" class="art-leaf art-leaf-2"/>
        <path d="M62 100 C 88 98, 104 80, 104 62 C 80 62, 62 78, 62 100 Z" class="art-leaf art-leaf-3"/>
      </svg>`;
  }

  window.HubComponents = {
    sidebarNav, moduleCard, brandCard, mobileModuleCard,
    activityRow, activityEmpty, pendingRow, heroArt, leafArt,
    appSwitcher, appNavList, appNavLoading,
  };
})();
