# Buzón público del formulario del colaborador

Servicio **aparte** de Farmhouse Link (otro proyecto de Railway) con el formulario donde cada colaborador
llena sus datos: personales, tipo de sangre, persona de contacto, banco y dependientes. Existe para que
**lo que está expuesto a internet no toque Farmhouse Link**.

```
 Colaborador / Instagram                    Internet                       Farmhouse Link (privado)
 ┌───────────────────────┐   sobre cifrado   ┌─────────────────────┐   va a buscarlos    ┌──────────────────┐
 │ /colaborador          │ ────────────────► │  public_intake      │ ◄────────────────── │ /contratos       │
 │ cifra en el navegador │                   │  (este servicio)    │   los abre con la   │ llave PRIVADA    │
 │ con la llave PÚBLICA  │                   │  no puede abrirlos  │   llave privada     │ base de datos    │
 └───────────────────────┘                   └─────────────────────┘   y los borra de    └──────────────────┘
                                                                        aquí
```

## Por qué protege a Farmhouse Link

- **Sin conexión a Farmhouse Link.** No tiene su base de datos, ni sus claves, ni acceso de ningún tipo.
  Su base de datos propia solo guarda hashes de invitaciones y sobres cifrados.
- **No puede leer lo que recibe.** Los datos se cifran en el navegador del colaborador (ECDH P-256 +
  AES-256-GCM) con una llave pública. La llave **privada** vive solo en Farmhouse Link. Si alguien
  tomara este servicio o su base de datos, no vería ni una cédula.
- **Farmhouse Link no recibe nada de internet.** Él mismo viene a buscar los sobres (con una clave de
  administración) y los borra de aquí al importarlos. No se abre ningún puerto ni endpoint nuevo en el
  sistema central.
- **Cada sobre se valida al importarlo**, con las mismas reglas del contrato. Uno alterado o armado a
  mano se descarta.
- **Sin cookies, sin sesión, sin terceros.** Política de seguridad estricta (solo scripts y estilos del
  propio sitio, sin enmarcar, sin Referer, sin indexar), sin caché en las respuestas y sin CORS.
- **Docs y esquema de la API apagados.** Contenedor sin privilegios de administrador.

Lo que **no** puede evitar (y es el límite de cualquier formulario público): si alguien tomara este
servicio podría servir una página alterada y cambiar la llave pública para leer lo que se envíe *después*.
Aun así no llegaría a Farmhouse Link ni a nada ya guardado. Para detectarlo, Farmhouse Link no importa un
sobre que no pueda abrir con su llave privada: un cambio de llave aparece como «envíos descartados».

## Dos formas de usarlo

1. **Invitación personal.** En Contratos → «Invitar colaborador» creas un enlace de **un solo uso** que
   vence (24 h, 3 días o 7). Se lo mandas a la persona. Aquí solo se guarda el hash del enlace.
2. **Formulario abierto** (para publicar, p. ej., en Instagram). En el mismo cuadro enciendes
   «Formulario abierto» y copias el enlace sin token. Cualquiera con el enlace puede llenarlo, así que lleva
   protecciones extra:
   - **Prueba de trabajo** que hace el navegador mientras la persona llena el formulario (frena robots sin
     captchas ni servicios de terceros).
   - **Tiempo mínimo** de llenado (20 s) y reto de **un solo uso** que vence a los 30 min.
   - **3 envíos por IP y hora**, **150 por día** (`OPEN_DAILY_CAP`) y **500 pendientes** como máximo.
   - **Interruptor**: lo apagas desde Contratos y el enlace deja de recibir al instante.
   - Cada solicitud la revisas tú antes de crear el contrato; en la lista aparece su origen.

## Puesta en marcha

1. **Genera las claves** en tu computadora (nada de pegarlas en chats):

   ```bash
   pip install cryptography
   python public_intake/tools/gen_keys.py
   ```

   Imprime qué va en cada proyecto. Guarda una copia en tu gestor de contraseñas.

2. **Proyecto nuevo en Railway** (no uses el de Farmhouse Link):
   - New Project → Deploy from GitHub → este repositorio.
   - Settings → **Root Directory = `public_intake`** (usa su propio `Dockerfile` y `railway.toml`).
   - Agrega una base de datos propia (plugin MySQL) o un volumen para SQLite, y pon `DATABASE_URL`.
   - Variables: `PUBLIC_KEY`, `ADMIN_KEY`, `ENVIRONMENT=production` (ver `.env.example`).
   - Genera el dominio público. Si quieres uno propio (p. ej. `registro.tudominio.com`), agrégalo ahí.

3. **En el proyecto de Farmhouse Link** agrega las variables que imprimió el script:
   `DATA_ENCRYPTION_KEY`, `INTAKE_PRIVATE_KEY`, `INTAKE_ADMIN_KEY` e `INTAKE_SERVICE_URL`
   (la dirección `https://...` del paso 2). `INTAKE_ADMIN_KEY` y `ADMIN_KEY` son **la misma** clave.

4. Entra a **Contratos**, crea una invitación de prueba y llénala. Debe aparecer en «Solicitudes».

## Pruebas

```bash
cd public_intake
python -m pytest tests -q
```

Cubren: invitación de un solo uso, respuestas idénticas para enlaces inválidos/vencidos/usados, límites de
intentos, tope de tamaño, señuelo anti-robots, administración con clave larga y bloqueo por intentos, el
formulario abierto (reto firmado, de un solo uso, tiempo mínimo, topes por IP, día y pendientes), política
de seguridad de la página y que el servicio no importe nada de Farmhouse Link. En `backend/tests/test_contracts.py`
hay además una prueba de que el `crypto.js` del navegador y Farmhouse Link son compatibles.

## Rotar claves

- `INTAKE_ADMIN_KEY` / `ADMIN_KEY`: cambia las dos al mismo tiempo.
- Llave pública/privada: genera un par nuevo, actualiza `PUBLIC_KEY` aquí e `INTAKE_PRIVATE_KEY` allá, y
  recoge antes los sobres pendientes (los cifrados con la llave vieja no se podrían abrir).
