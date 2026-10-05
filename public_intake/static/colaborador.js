/**
 * Farmhouse — formulario público del colaborador (servicio aparte, public_intake/).
 *
 * Página independiente a propósito: sin sesión, sin terceros y sin scripts en línea (ver la política
 * de seguridad en main.py). Los datos se cifran en el navegador (crypto.js) antes de enviarse.
 *
 * - Solo funciona con un enlace de invitación (/colaborador#t=TOKEN). El token viaja en el
 *   #fragmento: el navegador no lo manda al servidor ni a ningún registro, y apenas se lee se quita de
 *   la barra de direcciones (no queda en el historial ni se ve si alguien comparte pantalla).
 * - No guarda nada en el equipo (ni localStorage, ni cookies): lo escrito solo vive en la página.
 * - Envía el sobre cifrado a POST /api/submit; la invitación sirve una sola vez.
 */
(function () {
  'use strict';
  var $ = function (id) { return document.getElementById(id); };
  var API = '/api';

  // El token se lee una vez y se quita de la URL.
  var token = '';
  (function readToken() {
    var m = /(?:^#|&)t=([A-Za-z0-9_\-]{20,200})/.exec(window.location.hash || '');
    token = m ? m[1] : '';
    try { history.replaceState(null, '', window.location.pathname); } catch (e) { /* sin historial */ }
  })();

  function show(name) {
    ['stateLoading', 'stateInvalid', 'stateDone', 'form'].forEach(function (id) { $(id).hidden = id !== name; });
    window.scrollTo(0, 0);
  }

  function post(path, body) {
    return fetch(API + path, {
      method: 'POST',
      credentials: 'omit',                      // nada de cookies: es una página sin sesión
      cache: 'no-store',
      referrerPolicy: 'no-referrer',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body)
    });
  }
  function getPublicKey() {
    return fetch(API + '/public-key', { credentials: 'omit', cache: 'no-store', referrerPolicy: 'no-referrer' })
      .then(function (res) { if (!res.ok) throw new Error('key'); return res.json(); })
      .then(function (data) { return data.key; });
  }

  // ---- Dos formas de entrar ----
  //  1) Con invitación personal (#t=TOKEN): sirve una sola vez.
  //  2) Formulario ABIERTO (el enlace sin token, p. ej. el de Instagram): solo si Farmhouse Link lo
  //     encendió. Pide una prueba de trabajo (un cálculo corto que corre mientras la persona llena el
  //     formulario) para frenar a los robots, sin captchas ni servicios de terceros.
  var openMode = false, challenge = '', powPromise = null;

  function showForm(label) {
    if (label) { $('hello').textContent = 'Hola, ' + label; $('hello').hidden = false; }
    show('form');
    fillNationalities();
    updateIdHint();
  }
  function getJson(path) {
    return fetch(API + path, { credentials: 'omit', cache: 'no-store', referrerPolicy: 'no-referrer' })
      .then(function (res) { if (!res.ok) throw new Error('http ' + res.status); return res.json(); });
  }
  function newChallenge() {
    return getJson('/challenge').then(function (c) {
      challenge = c.challenge;
      powPromise = IntakeCrypto.solvePow(c.challenge, c.bits);
      powPromise.catch(function () { /* se maneja al enviar */ });
    });
  }

  if (token) {
    post('/check', { token: token }).then(function (res) {
      if (!res.ok) { show('stateInvalid'); return null; }
      return res.json();
    }).then(function (data) { if (data) showForm(data.label); })
      .catch(function () { show('stateInvalid'); });
  } else {
    getJson('/mode').then(function (m) {
      if (!m.open) { show('stateInvalid'); return null; }
      openMode = true;
      return newChallenge().then(function () { showForm(null); });
    }).catch(function () { show('stateInvalid'); });
  }

  // ---- Utilidades de formulario ----
  var NATIONALITIES = [['Panameño', 'Panameña'], ['Colombiano', 'Colombiana'], ['Venezolano', 'Venezolana'], ['Dominicano', 'Dominicana'],
    ['Italiano', 'Italiana'], ['Costarricense', 'Costarricense'], ['Nicaragüense', 'Nicaragüense'], ['Peruano', 'Peruana'],
    ['Ecuatoriano', 'Ecuatoriana'], ['Cubano', 'Cubana'], ['Mexicano', 'Mexicana'], ['Argentino', 'Argentina'], ['Español', 'Española'],
    ['Estadounidense', 'Estadounidense']];
  function fillNationalities() {
    var i = $('gender').value === 'F' ? 1 : 0, seen = {};
    $('nationalities').textContent = '';
    NATIONALITIES.forEach(function (n) {
      if (seen[n[i]]) return;
      seen[n[i]] = 1;
      var o = document.createElement('option');
      o.value = n[i];
      $('nationalities').appendChild(o);
    });
  }
  $('gender').addEventListener('change', fillNationalities);
  function updateIdHint() { $('idHint').textContent = $('idType').value === 'Pasaporte' ? 'Ej. AV515144' : 'Ej. 8-123-456'; }
  $('idType').addEventListener('change', updateIdHint);
  $('bank').addEventListener('change', function () {
    var other = $('bank').value === '__otro';
    $('bankOtherBox').hidden = !other;
    if (!other) $('bankOther').value = '';
  });
  function mask(el) {
    el.addEventListener('input', function () {
      var d = el.value.replace(/\D/g, '').slice(0, 8);
      el.value = d.length > 4 ? d.slice(0, 4) + '-' + d.slice(4) : d;
    });
  }
  mask($('phone')); mask($('contactPhone'));
  [$('account'), $('dv')].forEach(function (el) { el.addEventListener('input', function () { el.value = el.value.replace(/\D/g, ''); }); });

  // Dependientes
  function addDep() {
    var row = document.createElement('div');
    row.className = 'dep-row';
    var n = document.createElement('input'); n.className = 'dn'; n.placeholder = 'Nombre completo'; n.maxLength = 150; n.autocomplete = 'off';
    var a = document.createElement('input'); a.className = 'da'; a.type = 'number'; a.min = '0'; a.max = '120'; a.placeholder = 'Edad';
    var r = document.createElement('input'); r.className = 'dr'; r.placeholder = 'Parentesco'; r.setAttribute('list', 'relations'); r.maxLength = 40; r.autocomplete = 'off';
    var x = document.createElement('button'); x.type = 'button'; x.textContent = '×'; x.setAttribute('aria-label', 'Quitar dependiente');
    x.addEventListener('click', function () { row.remove(); });
    row.appendChild(n); row.appendChild(a); row.appendChild(r); row.appendChild(x);
    $('depsList').appendChild(row);
  }
  $('hasDeps').addEventListener('change', function () {
    var yes = $('hasDeps').value === 'si';
    $('depsBox').hidden = !yes;
    if (yes && !$('depsList').children.length) addDep();
  });
  $('addDep').addEventListener('click', addDep);
  $('birth').max = new Date().toISOString().slice(0, 10);

  // ---- Validación (el servidor vuelve a validar todo) ----
  function setBad(id, msg) {
    var field = $(id).closest('.field');
    field.classList.toggle('bad', !!msg);
    var err = field.querySelector('.err');
    if (err) err.textContent = msg || '';
    return !!msg;
  }
  function req(id, msg) { return setBad(id, !$(id).value.trim() ? (msg || 'Este dato es obligatorio.') : ''); }
  function idOk() {
    var v = $('idNumber').value.trim().toUpperCase();
    if (!v) return false;
    if ($('idType').value === 'Pasaporte') return /^[A-Z0-9]{5,15}$/.test(v);
    return /^(\d{1,2}|E|PE|N|PI|AV)(-\d{1,4}){2}$/.test(v) || /^(E|PE|N|PI)-\d{1,4}-\d{1,6}$/.test(v);
  }
  var phoneOk = function (v) { return /^\d{4}-?\d{3,4}$/.test(v); };

  function validate() {
    var bad = false;
    ['firstName', 'lastName', 'nationality', 'address', 'contactName', 'contactRel'].forEach(function (id) { bad = req(id) || bad; });
    ['gender', 'marital', 'blood', 'idType', 'bank', 'accountType'].forEach(function (id) { bad = req(id, 'Selecciona una opción.') || bad; });
    var b = $('birth').value, today = new Date().toISOString().slice(0, 10);
    bad = setBad('birth', !b || b >= today || b < '1900-01-01' ? 'Ingresa una fecha de nacimiento válida.' : '') || bad;
    bad = setBad('idNumber', idOk() ? '' : ($('idType').value === 'Pasaporte' ? 'Pasaporte inválido (letras y números).' : 'Formato de cédula: 8-123-456.')) || bad;
    bad = setBad('dv', $('dv').value !== '' && !/^\d{1,2}$/.test($('dv').value) ? 'Solo 1 o 2 dígitos.' : '') || bad;
    bad = setBad('phone', phoneOk($('phone').value) ? '' : 'Ingresa 7 u 8 dígitos.') || bad;
    bad = setBad('contactPhone', phoneOk($('contactPhone').value) ? '' : 'Ingresa 7 u 8 dígitos.') || bad;
    bad = setBad('email', /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test($('email').value.trim()) ? '' : 'Ingresa un correo válido.') || bad;
    bad = setBad('account', /^\d{6,20}$/.test($('account').value) ? '' : 'Solo números (6 a 20 dígitos).') || bad;
    if ($('bank').value === '__otro') bad = req('bankOther', 'Indica el banco.') || bad;
    var consentBad = !$('consent').checked;
    $('consentErr').textContent = consentBad ? 'Debes confirmar y autorizar para enviar.' : '';
    $('consentErr').classList.toggle('on', consentBad);
    return !(bad || consentBad);
  }
  $('form').addEventListener('input', function (e) {
    var f = e.target.closest && e.target.closest('.field');
    if (f) f.classList.remove('bad');
  });

  function payload() {
    var deps = $('hasDeps').value === 'si'
      ? Array.prototype.map.call($('depsList').children, function (row) {
        var age = row.querySelector('.da').value;
        return { name: row.querySelector('.dn').value.trim(), age: age === '' ? null : parseInt(age, 10), relationship: row.querySelector('.dr').value.trim() || 'Dependiente' };
      }).filter(function (d) { return d.name; })
      : [];
    return {
      first_name: $('firstName').value.trim(), last_name: $('lastName').value.trim(), birth_date: $('birth').value,
      gender: $('gender').value, nationality: $('nationality').value.trim(), marital_status: $('marital').value,
      blood_type: $('blood').value, id_type: $('idType').value, id_number: $('idNumber').value.trim(),
      dv: $('dv').value.trim() || null, phone: $('phone').value, email: $('email').value.trim(), address: $('address').value.trim(),
      emergency_contact_name: $('contactName').value.trim(), emergency_contact_phone: $('contactPhone').value,
      emergency_contact_relationship: $('contactRel').value.trim(),
      bank_name: $('bank').value === '__otro' ? $('bankOther').value.trim() : $('bank').value,
      account_type: $('accountType').value, account_number: $('account').value,
      dependents: deps
    };
  }

  function showError(msg) { var box = $('formError'); box.textContent = msg; box.hidden = false; box.scrollIntoView({ block: 'center' }); }
  function serverMessage(data) {
    // FastAPI manda los errores de validación como lista; se muestra el primero en español.
    try {
      if (Array.isArray(data.detail) && data.detail.length) return String(data.detail[0].msg || '').replace(/^Value error, /, '');
      if (typeof data.detail === 'string') return data.detail;
    } catch (e) { /* respuesta rara */ }
    return '';
  }

  $('form').addEventListener('submit', function (e) {
    e.preventDefault();
    $('formError').hidden = true;
    if (!validate()) {
      var first = document.querySelector('.field.bad, #consentErr.on');
      if (first) first.scrollIntoView({ block: 'center', behavior: 'smooth' });
      return;
    }
    var btn = $('btnSend');
    if (btn.disabled) return;
    btn.disabled = true;
    btn.textContent = 'Enviando…';
    getPublicKey().then(function (key) { return IntakeCrypto.encrypt(key, payload()); })
      .then(function (envelope) {
        if (!openMode) return post('/submit', { token: token, website: $('website').value, envelope: envelope });
        // Formulario abierto: la prueba de trabajo ya se estuvo calculando mientras llenaba.
        return powPromise.then(function (nonce) {
          return post('/submit-open', { challenge: challenge, nonce: nonce, website: $('website').value, envelope: envelope });
        });
      })
      .then(function (res) {
      if (res.status === 201) {
        $('form').reset();           // no queda nada escrito en la página
        $('depsList').textContent = '';
        token = ''; challenge = ''; powPromise = null;
        show('stateDone');
        return null;
      }
      if (res.status === 404) { token = ''; show('stateInvalid'); return null; }
      return res.json().catch(function () { return {}; }).then(function (data) {
        if (res.status === 429) showError(serverMessage(data) || 'Demasiados intentos. Espera unos minutos e intenta de nuevo.');
        else showError(serverMessage(data) || 'No se pudo enviar. Revisa los datos e intenta de nuevo.');
        // Cada reto sirve una sola vez: tras un rechazo se pide uno nuevo para poder reintentar.
        if (openMode && res.status !== 429) return newChallenge().catch(function () { /* se verá al reintentar */ });
      });
    }).catch(function () {
      showError('No hay conexión. Revisa el internet e intenta de nuevo.');
    }).then(function () {
      btn.disabled = false;
      btn.textContent = 'Enviar mis datos';
    });
  });
})();
