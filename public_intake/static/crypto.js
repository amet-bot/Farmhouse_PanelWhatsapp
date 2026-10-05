/**
 * Cifrado de extremo a extremo del formulario del colaborador.
 *
 * Los datos se cifran AQUÍ, en el navegador, con la llave pública de Farmhouse Link: el servidor que
 * recibe el formulario solo ve un sobre que no puede abrir. La llave privada vive únicamente en
 * Farmhouse Link (services/intake_crypto.py, que descifra con el mismo esquema).
 *
 * Esquema (v1): ECDH P-256 con una llave efímera por envío -> HKDF-SHA256 (sal aleatoria, info
 * "farmhouse-intake-v1") -> AES-256-GCM (IV aleatorio, dato asociado "farmhouse-intake-v1").
 * Sobre: { v, epk, salt, iv, ct }, todo en base64url (epk = punto público sin comprimir, 65 bytes).
 */
(function (root) {
  'use strict';
  var INFO = 'farmhouse-intake-v1';
  var enc = new TextEncoder();

  function b64uEncode(bytes) {
    var s = '';
    bytes = new Uint8Array(bytes);
    for (var i = 0; i < bytes.length; i++) s += String.fromCharCode(bytes[i]);
    return btoa(s).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
  }
  function b64uDecode(str) {
    var s = str.replace(/-/g, '+').replace(/_/g, '/');
    while (s.length % 4) s += '=';
    var bin = atob(s), out = new Uint8Array(bin.length);
    for (var i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
    return out;
  }

  /** publicKeyB64u: punto público P-256 sin comprimir (65 bytes) en base64url. obj: lo que se cifra. */
  async function encrypt(publicKeyB64u, obj) {
    var subtle = root.crypto.subtle;
    var serverKey = await subtle.importKey('raw', b64uDecode(publicKeyB64u), { name: 'ECDH', namedCurve: 'P-256' }, false, []);
    var eph = await subtle.generateKey({ name: 'ECDH', namedCurve: 'P-256' }, true, ['deriveBits']);
    var shared = await subtle.deriveBits({ name: 'ECDH', public: serverKey }, eph.privateKey, 256);
    var salt = root.crypto.getRandomValues(new Uint8Array(16));
    var hkdf = await subtle.importKey('raw', shared, 'HKDF', false, ['deriveKey']);
    var aes = await subtle.deriveKey({ name: 'HKDF', hash: 'SHA-256', salt: salt, info: enc.encode(INFO) }, hkdf, { name: 'AES-GCM', length: 256 }, false, ['encrypt']);
    var iv = root.crypto.getRandomValues(new Uint8Array(12));
    var ct = await subtle.encrypt({ name: 'AES-GCM', iv: iv, additionalData: enc.encode(INFO) }, aes, enc.encode(JSON.stringify(obj)));
    var epk = await subtle.exportKey('raw', eph.publicKey);
    return { v: 1, epk: b64uEncode(epk), salt: b64uEncode(salt), iv: b64uEncode(iv), ct: b64uEncode(ct) };
  }

  function leadingZeroBits(bytes) {
    var bits = 0;
    for (var i = 0; i < bytes.length; i++) {
      if (bytes[i] === 0) { bits += 8; continue; }
      bits += Math.clz32(bytes[i]) - 24;
      break;
    }
    return bits;
  }

  /**
   * Prueba de trabajo del formulario ABIERTO: busca un número tal que SHA-256("reto:número") empiece con
   * `bits` ceros. Para una persona son un par de segundos (corre mientras llena el formulario); para un
   * robot que quiera enviar miles, es un costo real. Cede el turno cada tanto para no congelar la página.
   */
  async function solvePow(challenge, bits) {
    var subtle = root.crypto.subtle, nonce = 0;
    for (;;) {
      var hash = new Uint8Array(await subtle.digest('SHA-256', enc.encode(challenge + ':' + nonce)));
      if (leadingZeroBits(hash) >= bits) return String(nonce);
      nonce++;
      if (nonce % 1500 === 0) await new Promise(function (r) { setTimeout(r, 0); });
    }
  }

  root.IntakeCrypto = { encrypt: encrypt, solvePow: solvePow, b64uEncode: b64uEncode, b64uDecode: b64uDecode };
})(typeof self !== 'undefined' ? self : globalThis);
