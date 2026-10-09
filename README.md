# 🌿 Farmhouse Link

Plataforma interna de **Farmhouse** (Grupo Col Rizado, Panamá) para operar las sucursales desde un solo lugar:
atención al cliente por WhatsApp, pedidos del menú digital, inventario y abastecimiento, operación de sucursal,
comunicación interna, reportes, administración y contratos de colaboradores. Nació como *Farmhouse WhatsApp Center*
y conserva ese nombre en el repositorio.

Un solo backend (FastAPI + MySQL) sirve todas las pantallas; el **hub** (`/hub`) es la puerta de entrada y abre cada
sistema dentro de sí mismo. En producción corre en Railway y la app Android (`mobile/`) abre esa misma URL.

---

## Sistemas

| Ruta | Sistema | Qué hace |
|---|---|---|
| `/hub` | Panel General | Entrada, tarjetas por sistema, menú lateral del sistema abierto, buscador. |
| `/app` | Centro WhatsApp | Bandeja por sucursal con tiempo real (WebSocket), bot con editor visual de flujos, seguimiento automático, respuestas rápidas (`/atajo`), etiquetas de cliente, notas internas, transferencias, cobro por Yappy, pedidos del menú digital. |
| `/menu` · `/pago-yappy` | Menú digital (clientes) | Carta, "Arma tu bowl", carrito, entrega con mapa y pago. Lo abre el bot desde WhatsApp. |
| `/inventario` · `/abastecimiento` · `/merma` · `/consumo` · `/recetas` | Inventario y costos | Existencias, conteos, cargamentos y recepción, traslados entre sucursales, mermas con fotos, consumo, recetas y costo real de insumos, sugerencias de compra, sincronización con Invu POS. |
| `/operacion` · `/tareas` · `/prep` · `/gestion` | Operación de sucursal | Lanzador para tablet, tareas (incl. recurrentes) con fotos, checklist de prep de bowls, solicitudes de insumos e incidentes, cierre de caja, centro de operación para gerencia. |
| `/interno` | Comunicación interna | Chat del equipo por sucursal y directo, con adjuntos. |
| `/link` | Reportes | Ventas (desde Invu), compras, mermas, cierre de mes, varianza de inventario, exportación a Excel, estado de las integraciones. |
| `/administracion` | Administración | Usuarios y roles, dispositivos autorizados (vinculación por código), sucursales. |
| `/contratos` | Contratos (RR.HH.) | Contratos de colaboradores con datos cifrados, exportación a Word, invitaciones al formulario público. |

### Roles
`agent` (sucursal), `supervisor` (de sucursal o global), `admin`, `rrhh`. Los permisos finos viven en
`backend/security/permissions.py` (catálogo `dominio.accion` por rol); el alcance por sucursal en
`backend/security/access_control.py`.

### Dispositivos autorizados
Agentes y supervisores solo operan desde un equipo **vinculado**: el admin registra el equipo y recibe un código de un
solo uso (vence en 24 h); alguien lo escribe en ese equipo en *Dispositivos → Vincular este equipo* y el navegador
guarda un token secreto (en la base solo vive su hash). Ver `backend/services/device_access.py`.

---

## Estructura

```text
farmhouse-whatsapp-center/
├── backend/                 FastAPI
│   ├── main.py              arranque, rutas de las pantallas, tareas en segundo plano
│   ├── config.py            Pydantic Settings (.env)
│   ├── models/  schemas/  services/  security/
│   ├── routers/             un archivo por recurso; inventory/ es un paquete por temas
│   │                        (items, shipments, waste, stock, counts, movements, invu + helpers)
│   ├── migrations/          Alembic (una migración por cambio de esquema, numeradas)
│   ├── seeds/seed_data.py   sucursales, admin inicial, bot (corre en cada deploy, idempotente)
│   ├── tests/               pytest (SQLite en memoria; ~650 pruebas)
│   └── requirements.txt
├── frontend/                HTML + CSS + JS sin framework
│   ├── *.html               una página por sistema (ver tabla)
│   ├── css/tokens.css       colores, tipografía, espacio: EL lugar donde se cambia la apariencia
│   ├── css/base.css         componentes compartidos (.btn, .field, .badge, .table, utilidades u-*)
│   ├── js/core/             api, websocket, utilidades, shell común, app nativa
│   ├── js/components/       piezas que comparten varias pantallas (chat, usuarios, etiquetas…)
│   ├── js/pages/            un archivo por pantalla (whatsapp, hub, inventory, …)
│   └── css/
├── public_intake/           SERVICIO APARTE: formulario público del colaborador (cifrado extremo a extremo)
├── mobile/                  app Android (Capacitor) que abre el hub en producción
├── database/schema.sql      esquema de referencia
├── deploy/                  nginx + systemd (alternativa a Railway)
├── Dockerfile · Procfile · railway.toml
└── sync_menu_from_official.py   trae la carta oficial al catálogo del menú
```

---

## Desarrollo local

Requisitos: Python 3.12+, MySQL 8.

```bash
cd backend
cp .env.example .env            # completar DATABASE_URL, SECRET_KEY y lo que se vaya a probar
pip install -r requirements.txt
alembic upgrade head
python seeds/seed_data.py
uvicorn main:app --reload --host 127.0.0.1 --port 8000
```

Abrir <http://127.0.0.1:8000/hub>. Sin credenciales de Meta, el backend arranca en `WHATSAPP_MODE=mock` y simula
los envíos. El usuario inicial es `admin` con la clave de `ADMIN_INITIAL_PASSWORD` (o la de desarrollo de
`seeds/seed_data.py`); cámbiala en producción.

### Pruebas

```bash
cd backend && python -m pytest -q
cd public_intake && python -m pytest -q
```

Las pruebas usan SQLite en memoria pero leen `backend/.env` para la configuración de WhatsApp: en un worktree limpio
hay que copiar ese archivo.

---

## Integraciones

- **Meta WhatsApp Cloud API**: webhooks en `routers/webhooks.py`, envío en `services/whatsapp_service.py`. Cada
  conversación guarda el `phone_number_id` por el que entró (base para varios números).
- **Invu POS**: usuarios de API por sucursal (`config.py`). Sincroniza ventas, ítems, recetas y proveedores
  (`services/invu_*`). El envío de pedidos a la pantalla de Invu (`invu_order_push.py`) está construido pero
  **apagado** hasta la certificación con Invu.
- **Yappy (Botón de Pago)**: `services/yappy_payment.py`, probado en UAT; en producción sigue apagado hasta tener
  credenciales comerciales.
- **Google Maps**: dirección y sucursal más cercana en el menú digital.
- **Push**: Web Push (VAPID) y Firebase para la app Android.
- **Formulario del colaborador**: Farmhouse Link *va a buscar* los sobres cifrados a `public_intake/` con una clave de
  administración y los abre con su llave privada; el servicio público no puede leerlos. Ver `public_intake/README.md`.

### Tareas en segundo plano (arrancan con el servidor)
Seguimiento del bot, sincronizaciones con Invu (proveedores, ventas, recetas), resumen semanal, alertas de stock bajo y
de operación, tareas recurrentes, respaldo de la base de datos.

---

## Despliegue

Producción en **Railway** con `Dockerfile`; `Procfile` corre `alembic upgrade head` y el seed antes de `uvicorn`.
Health check en `/api/health`. El servicio central se despliega al hacer push a `main`; el formulario público es
otro proyecto de Railway y se sube con `railway up --service formulario` desde `public_intake/`.

Variables de entorno: ver `backend/.env.example` (Meta, base de datos, Invu por sucursal, Yappy, VAPID, Firebase,
cifrado de contratos, formulario público).

---

## Convenciones

- Español en interfaz, mensajes de error, comentarios y commits (`tipo(área): qué cambia y por qué`).
- Sin frameworks de frontend; cada pantalla carga `?v=` en sus scripts para romper la caché al cambiar.
- Diseño: solo tokens de `css/tokens.css` (nada de colores o tamaños sueltos); tamaños de letra `var(--fs-*)`,
  mínimo 11 px; Inter en toda la plataforma. El sistema completo está en `design/prototype-v1.html`.
- Cada cambio de esquema es una migración nueva que *revisa* la anterior (cadena única).
- Las pruebas acompañan cada bloque de trabajo; `python -m pytest -q` debe quedar en verde antes de hacer push.
