/**
 * Farmhouse - Panel General (Hub de Sistemas)
 * Login liviano + grilla de acceso a los distintos sistemas internos. Reutiliza auth.js/api.js
 * tal cual los usa el Centro WhatsApp (misma sesión por cookie), pero sin cargar ninguno de los
 * módulos propios de esa app (chat, conversaciones, dispositivos, push...).
 */

document.addEventListener('DOMContentLoaded', async () => {
  const screenLogin = document.getElementById('hubLoginScreen');
  const screenMain = document.getElementById('hubMain');
  const loginForm = document.getElementById('loginForm');
  const btnLoginSubmit = document.getElementById('btnLoginSubmit');
  const loginErrorBox = document.getElementById('loginError');
  const btnTogglePassword = document.getElementById('btnTogglePassword');
  const passwordInput = document.getElementById('password');
  const usernameInput = document.getElementById('username');
  const btnThemeToggle = document.getElementById('btnThemeToggle');
  const themeIconSlot = document.getElementById('themeIconSlot');
  const themeLabel = document.querySelector('#btnThemeToggle .theme-label');
  const btnLogout = document.getElementById('btnLogout');

  // Tema Claro/Oscuro: mismo criterio y misma clave de localStorage que app.js, para que el
  // tema elegido se mantenga al moverse entre el hub y cualquier sistema.
  function applyTheme(theme) {
    document.documentElement.setAttribute('data-theme', theme);
    localStorage.setItem('fh_theme', theme);
    if (themeIconSlot) themeIconSlot.innerHTML = `<i data-lucide="${theme === 'dark' ? 'sun' : 'moon'}"></i>`;
    if (themeLabel) themeLabel.textContent = theme === 'dark' ? 'Claro' : 'Oscuro';
    utils.renderIcons();
  }
  applyTheme(localStorage.getItem('fh_theme') || 'light');

  btnThemeToggle?.addEventListener('click', () => {
    const current = document.documentElement.getAttribute('data-theme');
    applyTheme(current === 'dark' ? 'light' : 'dark');
  });

  function showLoginScreen(errorMessage = '') {
    screenLogin.hidden = false;
    screenMain.hidden = true;
    if (loginErrorBox) {
      if (errorMessage) {
        loginErrorBox.textContent = errorMessage;
        loginErrorBox.style.display = 'block';
      } else {
        loginErrorBox.style.display = 'none';
      }
    }
    utils.renderIcons();
  }

  function showHub(user) {
    screenLogin.hidden = true;
    screenMain.hidden = false;
    document.getElementById('hubAgentName').textContent = user.name;
    document.getElementById('hubAgentRole').textContent = `${user.role.toUpperCase()}${user.branch ? ' • ' + user.branch.name : ''}`;
    document.getElementById('hubAgentAvatar').textContent = utils.getInitials(user.name);
    utils.renderIcons();
  }

  if (btnTogglePassword && passwordInput) {
    btnTogglePassword.addEventListener('click', () => {
      if (passwordInput.type === 'password') {
        passwordInput.type = 'text';
        btnTogglePassword.innerHTML = '<i data-lucide="eye-off"></i>';
      } else {
        passwordInput.type = 'password';
        btnTogglePassword.innerHTML = '<i data-lucide="eye"></i>';
      }
      utils.renderIcons();
    });
  }

  async function handleLoginSubmit(e) {
    if (e) e.preventDefault();
    const username = usernameInput ? usernameInput.value.trim().toLowerCase() : '';
    const password = passwordInput ? passwordInput.value.trim() : '';

    if (!username || !password) {
      if (loginErrorBox) {
        loginErrorBox.textContent = 'Por favor ingresa tu nombre de usuario y contraseña.';
        loginErrorBox.style.display = 'block';
      }
      return;
    }

    try {
      if (loginErrorBox) loginErrorBox.style.display = 'none';
      if (btnLoginSubmit) {
        btnLoginSubmit.disabled = true;
        btnLoginSubmit.textContent = 'Validando credenciales...';
      }
      const data = await auth.login(username, password);
      showHub(data.user);
    } catch (err) {
      if (loginErrorBox) {
        loginErrorBox.textContent = err.message || 'Nombre de usuario o contraseña incorrectos.';
        loginErrorBox.style.display = 'block';
      }
    } finally {
      if (btnLoginSubmit) {
        btnLoginSubmit.disabled = false;
        btnLoginSubmit.textContent = 'Iniciar sesión';
      }
    }
  }
  loginForm?.addEventListener('submit', handleLoginSubmit);

  btnLogout?.addEventListener('click', async () => {
    await auth.logout();
    if (passwordInput) passwordInput.value = '';
    showLoginScreen();
  });

  window.addEventListener('auth:unauthorized', () => {
    showLoginScreen('Tu sesión expiró. Inicia sesión nuevamente.');
  });

  // Tarjetas de sistema: solo las que tienen data-href son clicables (las "Pronto" no lo llevan).
  document.querySelectorAll('.hub-card[data-href]').forEach((card) => {
    const go = () => { window.location.href = card.dataset.href; };
    card.addEventListener('click', go);
    card.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' || e.key === ' ') {
        e.preventDefault();
        go();
      }
    });
  });

  const existingUser = await auth.checkSession();
  if (existingUser) {
    showHub(existingUser);
  } else {
    showLoginScreen();
  }
  utils.renderIcons();
});
