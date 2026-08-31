# 🌿 Farmhouse WhatsApp Center

Sistema centralizado de administración de conversaciones de WhatsApp para **Farmhouse**, con enrutamiento inteligente entre sus 6 sucursales (`Costa del Este`, `San Francisco`, `Clayton`, `Obarrio`, `Vía Porras`, `Catering`), control de acceso dual por dispositivos autorizados (`FH-DEVICE-XXXXXX`) y actualización en tiempo real con WebSockets segmentados.

---

## 📁 Estructura del Proyecto

```text
farmhouse-whatsapp-center/
│
├── backend/
│   ├── main.py                 # Punto de entrada FastAPI
│   ├── config.py               # Configuración Pydantic Settings
│   ├── database.py             # Engine SQLAlchemy y sesión SessionLocal
│   ├── requirements.txt        # Dependencias de Python
│   ├── .env.example            # Plantilla de variables de entorno
│   ├── models/                 # Modelos relacionales (Branch, User, Device, Contact, Conv, Message, Order)
│   ├── schemas/                # Schemas Pydantic (Validación y Serialización)
│   ├── routers/                # Endpoints de API REST y WebSockets
│   ├── services/               # RoutingService, WebSocketManager, WhatsAppService
│   ├── security/               # Autenticación JWT, Hashing bcrypt y Dual Access Control
│   ├── seeds/                  # Script para sembrar datos iniciales (seed_data.py)
│   └── migrations/             # Migraciones versionadas de Alembic
│
├── frontend/
│   ├── index.html              # Interfaz web de usuario
│   ├── css/
│   │   └── style.css           # Estilos visuales responsive
│   └── js/
│       └── app.js              # Controlador cliente (API, Auth, WebSockets, Dispositivos, Chat)
│
├── database/
│   └── schema.sql              # Esquema SQL relacional documentado
│
├── README.md
└── .gitignore
```

---

## 🚀 Puesta en Marcha

### 1. Backend (FastAPI + MySQL 8.0+)
1. Configura el archivo `backend/.env` con tus credenciales de MySQL (`DATABASE_URL=mysql+pymysql://root:@localhost:3306/FarmhouseWhatsAppCenter?charset=utf8mb4`).
2. Instala dependencias y corre migraciones / seeds:
   ```bash
   cd backend
   pip install -r requirements.txt
   alembic upgrade head
   python seeds/seed_data.py
   ```
3. Inicia el servidor:
   ```bash
   uvicorn main:app --reload --host 127.0.0.1 --port 8000
   ```

### 2. Frontend y Acceso al Sistema
* **Opción Principal (Recomendada):** Abre en tu navegador [http://127.0.0.1:8000/](http://127.0.0.1:8000/) *(el backend sirve la aplicación automáticamente en la raíz)*.
* **Opción Alternativa:** Abre `frontend/index.html` directamente en tu navegador (`file://`) o con un servidor local (*Live Server* en `http://127.0.0.1:5500`).

#### Credenciales de Acceso Inicial:
* **Administrador:** `admin@farmhouse.pa` / `Admin123!`
