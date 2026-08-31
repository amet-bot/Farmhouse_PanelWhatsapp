import logging
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from config import settings, mask_secret
from routers import (
    auth,
    branches,
    users,
    devices,
    contacts,
    conversations,
    messages,
    orders,
    media,
    websocket,
    webhooks
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logging.getLogger("httpx").setLevel(logging.INFO)
logger = logging.getLogger("farmhouse.main")

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Inicialización y validación de seguridad de la aplicación."""
    try:
        settings.validate_production_security()
    except ValueError as e:
        logger.error(f"[Startup Security Error] {e}")
        if settings.ENVIRONMENT == "production":
            raise e

    if settings.WHATSAPP_MODE == "meta":
        token = str(settings.META_WA_ACCESS_TOKEN or "").strip()
        phone_id = str(settings.META_WA_PHONE_NUMBER_ID or "").strip()
        if not token or not phone_id:
            logger.warning("[Startup Config] WHATSAPP_MODE=meta pero META_WA_ACCESS_TOKEN o META_WA_PHONE_NUMBER_ID están vacíos.")
        elif not token.isascii():
            logger.error("[Startup Config] ERROR: META_WA_ACCESS_TOKEN contiene caracteres no-ASCII inválidos.")
        elif token.startswith("<") or "PEGO_AQUI" in token:
            logger.warning("[Startup Config] AVISO: META_WA_ACCESS_TOKEN contiene un placeholder. Pega tu token real de Meta.")
        else:
            logger.info(f"[Startup Config] WhatsApp Cloud API configurado correctamente para Phone ID: {phone_id} (Token: {mask_secret(token)})")
    else:
        logger.info("[Startup Config] Ejecutando en modo WHATSAPP_MODE=mock (simulación local).")

    yield

# Farmhouse WhatsApp Center - FastAPI Backend Server
app = FastAPI(
    title=settings.APP_NAME,
    description="Backend oficial para Farmhouse WhatsApp Center (FastAPI + MySQL + WebSockets)",
    version="2.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan
)

# Configuración de CORS estricto con orígenes explícitos configurables
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.get_allowed_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Middleware de Encabezados de Seguridad y Protección CSRF (Puntos 3 y 27)
@app.middleware("http")
async def security_and_csrf_middleware(request: Request, call_next):
    # 1. Protección CSRF en mutaciones de estado
    if request.method in ["POST", "PUT", "DELETE", "PATCH"]:
        path = request.url.path
        # Excluir webhooks externos de Meta (que se autentican por firma HMAC-SHA256)
        if path.startswith(settings.API_V1_STR) and not "/webhooks" in path:
            csrf_header = request.headers.get("X-Requested-With")
            auth_header = request.headers.get("Authorization")
            if not (csrf_header and csrf_header.lower() == "xmlhttprequest") and not auth_header:
                logger.warning(f"Intento de mutación CSRF bloqueado en '{path}' desde IP {request.client.host if request.client else 'local'}")
                return JSONResponse(
                    status_code=status.HTTP_403_FORBIDDEN,
                    content={"detail": "Encabezado de protección CSRF requerido (X-Requested-With: XMLHttpRequest)."}
                )

    response = await call_next(request)

    # 2. Encabezados de Seguridad Estrictos (Punto 27)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "geolocation=(), camera=(), microphone=()"
    if settings.ENVIRONMENT == "production":
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"

    return response

# Montaje de routers REST API
app.include_router(auth.router, prefix=settings.API_V1_STR)
app.include_router(branches.router, prefix=settings.API_V1_STR)
app.include_router(users.router, prefix=settings.API_V1_STR)
app.include_router(devices.router, prefix=settings.API_V1_STR)
app.include_router(contacts.router, prefix=settings.API_V1_STR)
app.include_router(conversations.router, prefix=settings.API_V1_STR)
app.include_router(messages.router, prefix=settings.API_V1_STR)
app.include_router(orders.router, prefix=settings.API_V1_STR)
app.include_router(media.router, prefix=settings.API_V1_STR)
app.include_router(webhooks.router, prefix=settings.API_V1_STR)
app.include_router(webhooks.router)
app.include_router(websocket.router)

# -----------------------------------------------------------------------------
# Endpoints de Monitoreo y Salud de la API
# -----------------------------------------------------------------------------
@app.get("/api/health")
def health_check():
    return {
        "status": "ok",
        "app": settings.APP_NAME,
        "version": "2.0.0",
        "environment": settings.ENVIRONMENT,
        "docs": "/docs"
    }

# -----------------------------------------------------------------------------
# Montaje estático del frontend para acceso local y producción (Railway / Docker)
# -----------------------------------------------------------------------------
frontend_dir = Path(__file__).resolve().parent.parent / "frontend"
if frontend_dir.exists():
    if (frontend_dir / "assets").exists():
        app.mount("/assets", StaticFiles(directory=str(frontend_dir / "assets")), name="assets")
    if (frontend_dir / "css").exists():
        app.mount("/css", StaticFiles(directory=str(frontend_dir / "css")), name="css")
    if (frontend_dir / "js").exists():
        app.mount("/js", StaticFiles(directory=str(frontend_dir / "js")), name="js")

    @app.get("/", include_in_schema=False)
    def serve_root():
        return FileResponse(str(frontend_dir / "index.html"))

    @app.get("/app", include_in_schema=False)
    def serve_frontend():
        return FileResponse(str(frontend_dir / "index.html"))
else:
    @app.get("/")
    def fallback_root():
        return {
            "app": settings.APP_NAME,
            "version": "2.0.0",
            "status": "operational",
            "docs": "/docs"
        }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host=settings.HOST, port=settings.PORT, reload=settings.DEBUG)
