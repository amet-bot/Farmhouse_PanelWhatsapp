/**
 * Farmhouse WhatsApp Center - Módulo de Notificaciones Avanzadas
 * Incluye:
 * 1. Generador de Audio con Web Audio API (Chime armónico cristalino sin archivos externos)
 * 2. Notificaciones nativas de escritorio (Browser Notifications) con enlace al chat
 * 3. Tarjeta flotante interactiva (Toast con avatar, vista previa y botón "Abrir chat")
 * 4. Título de pestaña parpadeante y contador de mensajes no leídos
 * 5. Control de volumen y silencio persistido en localStorage
 */

const notificationModule = {
  audioCtx: null,
  soundEnabled: true,
  unreadCount: 0,
  titleFlashInterval: null,
  originalTitle: 'Farmhouse WhatsApp Center',

  init() {
    this.originalTitle = document.title || 'Farmhouse WhatsApp Center';
    const savedSound = localStorage.getItem('fh_sound_enabled');
    this.soundEnabled = savedSound !== null ? savedSound === 'true' : true;

    // Desbloquear AudioContext en la primera interacción del usuario (política de navegadores modernos)
    const unlockAudio = () => {
      this.getAudioContext();
      document.removeEventListener('click', unlockAudio);
      document.removeEventListener('keydown', unlockAudio);
      document.removeEventListener('touchstart', unlockAudio);
    };
    document.addEventListener('click', unlockAudio);
    document.addEventListener('keydown', unlockAudio);
    document.addEventListener('touchstart', unlockAudio);

    const btn = document.getElementById('btnToggleSound');
    if (btn) {
      btn.addEventListener('click', () => this.toggleSound());
    }

    this.updateSoundToggleBtn();
  },

  getAudioContext() {
    if (!this.audioCtx) {
      const AudioContextClass = window.AudioContext || window.webkitAudioContext;
      if (AudioContextClass) {
        this.audioCtx = new AudioContextClass();
      }
    }
    if (this.audioCtx && this.audioCtx.state === 'suspended') {
      this.audioCtx.resume();
    }
    return this.audioCtx;
  },

  toggleSound() {
    this.soundEnabled = !this.soundEnabled;
    localStorage.setItem('fh_sound_enabled', this.soundEnabled);
    this.updateSoundToggleBtn();
    if (this.soundEnabled) {
      this.playMessageSound();
      utils.showToast('🔊 Notificaciones sonoras activadas', 'success');
    } else {
      utils.showToast('🔇 Notificaciones sonoras silenciadas', 'info');
    }
  },

  updateSoundToggleBtn() {
    const btn = document.getElementById('btnToggleSound');
    if (!btn) return;
    if (this.soundEnabled) {
      btn.innerHTML = '<span class="nav-icon"><i data-lucide="volume-2"></i></span><span class="btn-text">Sonido</span>';
      btn.classList.add('active');
      btn.title = 'Sonido activado (clic para silenciar)';
    } else {
      btn.innerHTML = '<span class="nav-icon"><i data-lucide="volume-x"></i></span><span class="btn-text">Mudo</span>';
      btn.classList.remove('active');
      btn.title = 'Sonido silenciado (clic para activar)';
    }
    utils.renderIcons();
  },

  /**
   * Reproduce una campana armónica clara y suave (chime cristalino de 2 notas)
   */
  playMessageSound() {
    if (!this.soundEnabled) return;
    try {
      const ctx = this.getAudioContext();
      if (!ctx) return;

      const now = ctx.currentTime;
      // Nota 1 (880 Hz - A5)
      const osc1 = ctx.createOscillator();
      const gain1 = ctx.createGain();
      osc1.type = 'sine';
      osc1.frequency.setValueAtTime(880, now);
      gain1.gain.setValueAtTime(0.35, now);
      gain1.gain.exponentialRampToValueAtTime(0.001, now + 0.35);
      osc1.connect(gain1);
      gain1.connect(ctx.destination);
      osc1.start(now);
      osc1.stop(now + 0.35);

      // Nota 2 (1320 Hz - E6) ligeramente desfasada
      const osc2 = ctx.createOscillator();
      const gain2 = ctx.createGain();
      osc2.type = 'sine';
      osc2.frequency.setValueAtTime(1320, now + 0.08);
      gain2.gain.setValueAtTime(0.4, now + 0.08);
      gain2.gain.exponentialRampToValueAtTime(0.001, now + 0.55);
      osc2.connect(gain2);
      gain2.connect(ctx.destination);
      osc2.start(now + 0.08);
      osc2.stop(now + 0.55);
    } catch (e) {
      console.warn('[Audio Notification] Error reproduciendo sonido:', e);
    }
  },

  /**
   * Reproduce un sonido de alerta alegre para pedidos nuevos
   */
  playOrderSound() {
    if (!this.soundEnabled) return;
    try {
      const ctx = this.getAudioContext();
      if (!ctx) return;

      const now = ctx.currentTime;
      const notes = [587.33, 739.99, 880.00, 1174.66]; // D5 -> F#5 -> A5 -> D6
      notes.forEach((freq, idx) => {
        const osc = ctx.createOscillator();
        const gain = ctx.createGain();
        const start = now + idx * 0.09;
        osc.type = 'triangle';
        osc.frequency.setValueAtTime(freq, start);
        gain.gain.setValueAtTime(0.3, start);
        gain.gain.exponentialRampToValueAtTime(0.001, start + 0.3);
        osc.connect(gain);
        gain.connect(ctx.destination);
        osc.start(start);
        osc.stop(start + 0.3);
      });
    } catch (e) {
      console.warn('[Audio Order Notification] Error:', e);
    }
  },

  /**
   * Muestra notificación integral: Sonido + Tarjeta Flotante + Desktop Push + Flash de pestaña
   */
  notifyIncomingMessage(data) {
    const contactName = data.contact_name || 'Cliente';
    const msgContent = (data.message && data.message.content) ? data.message.content : 'Nuevo mensaje recibido';
    const convId = data.conversation_id;
    const isCurrentChatOpen = chatModule.currentConversation && Number(chatModule.currentConversation.id) === Number(convId);

    // 1. Sonido siempre
    this.playMessageSound();

    // 2. Si el chat no está abierto o la pestaña está inactiva, activar alerta en pestaña
    if (!isCurrentChatOpen || document.visibilityState !== 'visible') {
      this.unreadCount++;
      this.startTitleAlert(contactName);
    }

    // 3. Notificación nativa del navegador (si está en segundo plano o minimizado)
    if (document.visibilityState !== 'visible' && 'Notification' in window && Notification.permission === 'granted') {
      try {
        const notif = new Notification(`💬 ${contactName} • Farmhouse`, {
          body: msgContent,
          icon: '/assets/images/farmhouse-logo.png',
          badge: '/assets/images/farmhouse-logo.png',
          tag: `conv-${convId}`,
          renotify: true
        });
        notif.onclick = () => {
          window.focus();
          if (convId) chatModule.loadConversation(Number(convId));
          notif.close();
        };
      } catch (e) {
        console.warn('[Desktop Notification Error]', e);
      }
    }

    // 4. Tarjeta flotante interactiva en la esquina superior derecha
    this.showFloatingCard({
      title: contactName,
      preview: msgContent,
      contactName: contactName,
      convId: convId,
      type: 'message'
    });
  },

  notifyNewOrder(data) {
    const contactName = data.contact_name || 'Cliente';
    const orderCode = data.order?.order_code || 'FH-Orden';
    const convId = data.conversation_id;

    this.playOrderSound();
    this.unreadCount++;
    this.startTitleAlert(`🧾 ${orderCode}`);

    if (document.visibilityState !== 'visible' && 'Notification' in window && Notification.permission === 'granted') {
      try {
        const notif = new Notification(`🧾 Nuevo Pedido: ${orderCode}`, {
          body: `Cliente: ${contactName} ha confirmado su pedido.`,
          icon: '/assets/images/farmhouse-logo.png',
          tag: `order-${orderCode}`,
          renotify: true
        });
        notif.onclick = () => {
          window.focus();
          if (convId) chatModule.loadConversation(Number(convId));
          notif.close();
        };
      } catch (e) {}
    }

    this.showFloatingCard({
      title: `🧾 Pedido ${orderCode}`,
      preview: `${contactName} envió un nuevo pedido`,
      contactName: contactName,
      convId: convId,
      type: 'order'
    });
  },

  showFloatingCard({ title, preview, contactName, convId, type = 'message' }) {
    let container = document.getElementById('floatingNotificationStack');
    if (!container) {
      container = document.createElement('div');
      container.id = 'floatingNotificationStack';
      container.className = 'floating-notif-stack';
      document.body.appendChild(container);
    } else {
      // Mantener solo la notificación más reciente para no tapar la pantalla del celular
      const existingCards = container.querySelectorAll('.floating-notif-card');
      existingCards.forEach((c) => c.remove());
    }

    const card = document.createElement('div');
    card.className = `floating-notif-card notif-type-${type}`;
    const initials = utils.getInitials(contactName);
    const color = utils.getAvatarColor(contactName);

    card.innerHTML = `
      <div class="floating-notif-avatar" style="background:${color}22; color:${color}; border: 1.5px solid ${color}">
        ${utils.escapeHtml(initials)}
      </div>
      <div class="floating-notif-body">
        <div class="floating-notif-header">
          <strong class="floating-notif-title">${utils.escapeHtml(title)}</strong>
          <button class="floating-notif-close" title="Cerrar">&times;</button>
        </div>
        <div class="floating-notif-text">${utils.escapeHtml(preview)}</div>
        <div class="floating-notif-action">
          <button class="btn-notif-open" data-conv-id="${convId}">
            <span>Abrir conversación</span> <i data-lucide="arrow-right"></i>
          </button>
        </div>
      </div>
      <div class="floating-notif-progress"></div>
    `;

    // Botón de abrir chat
    const btnOpen = card.querySelector('.btn-notif-open');
    if (btnOpen) {
      btnOpen.addEventListener('click', (e) => {
        e.stopPropagation();
        if (convId) {
          chatModule.loadConversation(Number(convId));
        }
        card.classList.add('dismissed');
        setTimeout(() => card.remove(), 250);
      });
    }

    // Clic en la tarjeta abre el chat
    card.addEventListener('click', () => {
      if (convId) {
        chatModule.loadConversation(Number(convId));
      }
      card.classList.add('dismissed');
      setTimeout(() => card.remove(), 250);
    });

    // Botón de cerrar
    const btnClose = card.querySelector('.floating-notif-close');
    if (btnClose) {
      btnClose.addEventListener('click', (e) => {
        e.stopPropagation();
        card.classList.add('dismissed');
        setTimeout(() => card.remove(), 250);
      });
    }

    container.appendChild(card);
    utils.renderIcons();

    // Auto-eliminar a los 6 segundos
    setTimeout(() => {
      if (card && card.parentElement) {
        card.classList.add('dismissed');
        setTimeout(() => card.remove(), 250);
      }
    }, 6000);
  },

  startTitleAlert(label) {
    clearInterval(this.titleFlashInterval);
    let toggle = false;
    this.titleFlashInterval = setInterval(() => {
      toggle = !toggle;
      document.title = toggle
        ? `(${this.unreadCount}) 💬 ${label}`
        : `🔔 Nuevo mensaje - Farmhouse`;
    }, 1200);
  },

  clearTitleAlert() {
    clearInterval(this.titleFlashInterval);
    this.titleFlashInterval = null;
    this.unreadCount = 0;
    document.title = this.originalTitle;
  }
};
