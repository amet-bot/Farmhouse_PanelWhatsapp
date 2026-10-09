# Farmhouse Link · app de Android

La app abre el sistema **en vivo** (Railway): mismo servidor, misma base de datos y mismos
usuarios que el navegador. Lo que se publica en Railway aparece solo en la app, sin reinstalarla.
El APK solo hay que volver a sacarlo si cambia algo del teléfono (ícono, permisos, Firebase).

Lo que agrega sobre el navegador:
- **Notificaciones nativas "a la vista"**: canal de importancia alta (aparecen arriba de la
  pantalla, suenan, vibran y se ven con el teléfono bloqueado). Tocar una abre su pantalla.
- Botón "atrás" del teléfono dentro del sistema, cámara directa, ícono propio.

El código web de la app vive en `frontend/js/core/native-app.js` (viaja con Railway).

## Compilar el APK (Windows)

Herramientas (ya instaladas en esta computadora, fuera del proyecto):
- Java 21: `C:\Users\PC\android-tools\jdk\jdk-21.0.12.1+1`
- SDK de Android: `C:\Users\PC\android-tools\sdk` (`android/local.properties` apunta ahí)

```bash
cd mobile
npm install
export JAVA_HOME="C:/Users/PC/android-tools/jdk/jdk-21.0.12.1+1"
npm run apk
```

Queda en `android/app/build/outputs/apk/debug/app-debug.apk`.

`npm run apk` corre antes `scripts/prepare-config.js`, que escribe `capacitor.config.json`
(dirección del sistema y si hay Firebase).

## Instalarla en un celular

Mandar el `app-debug.apk` al teléfono (WhatsApp, Drive, cable), abrirlo y permitir
"Instalar apps desconocidas" para esa app cuando lo pida. Al abrir Farmhouse Link por primera vez
pide permiso para notificaciones y cámara.

## Conectar las notificaciones (Firebase, una sola vez)

1. En https://console.firebase.google.com crear un proyecto (cuenta de la empresa).
2. Agregar una app **Android** con el paquete `pa.farmhouse.link` y bajar
   `google-services.json` → copiarlo a `mobile/android/app/google-services.json`
   (no se sube al repositorio: está en `.gitignore`).
3. Configuración del proyecto → Cuentas de servicio → **Generar nueva clave privada** (baja un
   JSON). Ese JSON completo va en Railway como variable `FIREBASE_SERVICE_ACCOUNT_JSON`
   (también se acepta en base64). Nunca en el repositorio.
4. Volver a compilar el APK (`npm run apk`) e instalarlo.
5. En la app: Perfil → **Probar notificaciones**.

Sin Firebase la app funciona igual, solo que sin avisos nativos: `prepare-config.js` no pone la
marca `FHPush` y la página no pide el permiso (sin Firebase, pedirlo cierra la app).
