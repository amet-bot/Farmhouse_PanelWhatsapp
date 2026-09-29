/**
 * Escribe capacitor.config.json antes de cada build.
 *
 * La app abre el sistema EN VIVO (Railway): mismo servidor, misma base y mismos usuarios que el
 * navegador, y lo que se publica ahí aparece solo en la app, sin reinstalarla.
 *
 * Las notificaciones nativas pasan por Firebase. Si todavía no está android/app/google-services.json,
 * pedir el permiso de notificaciones cierra la app; por eso la marca "FHPush" en el user-agent solo
 * se agrega cuando el archivo existe, y la página web solo registra notificaciones si la ve.
 */
const fs = require('fs');
const path = require('path');

const SERVER_URL = process.env.FH_SERVER_URL || 'https://farmhousepanelwhatsapp-production.up.railway.app';
const conFirebase = fs.existsSync(path.join(__dirname, '..', 'android', 'app', 'google-services.json'));

const config = {
  appId: 'pa.farmhouse.link',
  appName: 'Farmhouse Link',
  webDir: 'www',
  server: {
    url: `${SERVER_URL}/hub`,
    cleartext: false,
  },
  android: {
    appendUserAgent: `FarmhouseLinkApp/1.0${conFirebase ? ' FHPush' : ''}`,
    allowMixedContent: false,
  },
  plugins: {
    PushNotifications: { presentationOptions: ['badge', 'sound', 'alert'] },
  },
};

fs.writeFileSync(path.join(__dirname, '..', 'capacitor.config.json'), JSON.stringify(config, null, 2) + '\n');
console.log(`capacitor.config.json listo · ${config.server.url} · notificaciones: ${conFirebase ? 'sí (Firebase)' : 'no (falta google-services.json)'}`);
