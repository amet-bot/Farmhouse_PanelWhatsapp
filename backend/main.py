import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.exc import IntegrityError
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
    webhooks,
    push,
    menu,
    payments,
    bot_flows,
    inventory,
    internal_chat,
    link,
    transfers,
    ops,
    prep,
)
from services.bot_followup import run_followup_sweep_loop
from services import invu_sync, invu_sales_sync

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
    except Exception as e:
        logger.warning(f"[Startup Security Notice] {e}")

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

    # Único loop en segundo plano del proyecto (ver services/bot_followup.py): NUNCA debe
    # arrancar bajo pytest — TestClient(app) usado como context manager (ver tests/conftest.py)
    # dispara este lifespan en cada uno de los 130+ tests, y este loop usa su propio
    # SessionLocal (no el override de sesión de test), así que arrancaría contra la base de
    # datos real de desarrollo/producción en segundo plano durante toda la suite.
    followup_task = None
    if "PYTEST_CURRENT_TEST" not in os.environ:
        followup_task = asyncio.create_task(run_followup_sweep_loop())

    # Segundo loop: trae los proveedores de Invu al arrancar y una vez por día. Misma guarda de
    # pytest y, además, no arranca si la integración no tiene credenciales (ver invu_sync).
    invu_task = None
    if invu_sync.debe_arrancar_loop():
        invu_task = asyncio.create_task(invu_sync.run_provider_sync_loop())

    # Tercer loop: las ventas de cada sucursal desde Invu (Farmhouse Link). Arranca si al menos
    # una sucursal tiene su usuario de API; misma guarda de pytest (ver invu_sales_sync).
    ventas_task = None
    if invu_sales_sync.debe_arrancar_loop():
        ventas_task = asyncio.create_task(invu_sales_sync.run_sales_sync_loop())

    yield

    for task in (followup_task, invu_task, ventas_task):
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

# Farmhouse Link - FastAPI Backend Server (el módulo de WhatsApp es "Atención al Cliente")
app = FastAPI(
    title=settings.APP_NAME,
    description="Backend oficial de Farmhouse Link (FastAPI + MySQL + WebSockets)",
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
    # SAMEORIGIN (no DENY): el Panel General abre cada sistema dentro de su propia pantalla
    # (iframe del mismo origen). Sigue bloqueando que cualquier OTRO sitio enmarque el panel
    # (clickjacking).
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=()"
    if settings.ENVIRONMENT == "production":
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"

    return response

# Red de seguridad: cualquier violación de restricción de MySQL (UNIQUE, FK, etc.)
# que no haya sido validada explícitamente en el endpoint no debe filtrarse como 500 crudo.
@app.exception_handler(IntegrityError)
async def integrity_error_handler(request: Request, exc: IntegrityError):
    logger.error(f"[IntegrityError] {request.method} {request.url.path}: {exc.orig}")
    return JSONResponse(
        status_code=status.HTTP_409_CONFLICT,
        content={"detail": "La operación viola una restricción de integridad de la base de datos (dato duplicado o referencia inválida)."}
    )

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
app.include_router(media.router)
app.include_router(push.router, prefix=settings.API_V1_STR)
app.include_router(menu.router, prefix=settings.API_V1_STR)
app.include_router(payments.router, prefix=settings.API_V1_STR)
app.include_router(webhooks.router, prefix=settings.API_V1_STR)
app.include_router(webhooks.router)
app.include_router(bot_flows.router, prefix=settings.API_V1_STR)
app.include_router(inventory.router, prefix=settings.API_V1_STR)
app.include_router(internal_chat.router, prefix=settings.API_V1_STR)
app.include_router(link.router, prefix=settings.API_V1_STR)
app.include_router(transfers.router, prefix=settings.API_V1_STR)
app.include_router(ops.router, prefix=settings.API_V1_STR)
app.include_router(prep.router, prefix=settings.API_V1_STR)
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
    static_catalog_dir = frontend_dir / "assets" / "catalog"
    static_catalog_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/static/catalog", StaticFiles(directory=str(static_catalog_dir)), name="static_catalog")
    if (frontend_dir / "css").exists():
        app.mount("/css", StaticFiles(directory=str(frontend_dir / "css")), name="css")
    if (frontend_dir / "js").exists():
        app.mount("/js", StaticFiles(directory=str(frontend_dir / "js")), name="js")

    # "/" es ahora el Panel General (hub de sistemas): WhatsApp Center pasó a ser uno de varios
    # sistemas internos, no el punto de entrada único. Ver push_service.py, que enlaza a "/app"
    # (no a "/") para que un clic en una notificación abra la conversación directo, sin pasar
    # primero por el hub.
    @app.get("/", include_in_schema=False)
    def serve_root():
        return FileResponse(str(frontend_dir / "hub.html"), headers={"Cache-Control": "no-cache, no-store, must-revalidate"})

    @app.get("/hub", include_in_schema=False)
    def serve_hub():
        return FileResponse(str(frontend_dir / "hub.html"), headers={"Cache-Control": "no-cache, no-store, must-revalidate"})

    @app.get("/app", include_in_schema=False)
    def serve_frontend():
        return FileResponse(str(frontend_dir / "index.html"), headers={"Cache-Control": "no-cache, no-store, must-revalidate"})

    if (frontend_dir / "menu.html").exists():
        @app.get("/menu", include_in_schema=False)
        def serve_menu():
            return FileResponse(str(frontend_dir / "menu.html"), headers={"Cache-Control": "no-cache, no-store, must-revalidate"})

    if (frontend_dir / "yappy_payment.html").exists():
        @app.get("/pago-yappy", include_in_schema=False)
        def serve_yappy_payment():
            return FileResponse(str(frontend_dir / "yappy_payment.html"), headers={"Cache-Control": "no-cache, no-store, must-revalidate"})

    if (frontend_dir / "inventory.html").exists():
        @app.get("/inventario", include_in_schema=False)
        def serve_inventory():
            return FileResponse(str(frontend_dir / "inventory.html"), headers={"Cache-Control": "no-cache, no-store, must-revalidate"})

    if (frontend_dir / "internal.html").exists():
        @app.get("/interno", include_in_schema=False)
        def serve_internal():
            return FileResponse(str(frontend_dir / "internal.html"), headers={"Cache-Control": "no-cache, no-store, must-revalidate"})

    if (frontend_dir / "link.html").exists():
        @app.get("/link", include_in_schema=False)
        def serve_link():
            return FileResponse(str(frontend_dir / "link.html"), headers={"Cache-Control": "no-cache, no-store, must-revalidate"})

    if (frontend_dir / "tablet.html").exists():
        @app.get("/operacion", include_in_schema=False)
        def serve_tablet():
            return FileResponse(str(frontend_dir / "tablet.html"), headers={"Cache-Control": "no-cache, no-store, must-revalidate"})

    if (frontend_dir / "prep.html").exists():
        @app.get("/prep", include_in_schema=False)
        def serve_prep():
            return FileResponse(str(frontend_dir / "prep.html"), headers={"Cache-Control": "no-cache, no-store, must-revalidate"})

    if (frontend_dir / "administracion.html").exists():
        @app.get("/administracion", include_in_schema=False)
        def serve_administracion():
            return FileResponse(str(frontend_dir / "administracion.html"), headers={"Cache-Control": "no-cache, no-store, must-revalidate"})

    if (frontend_dir / "manifest.json").exists():
        @app.get("/manifest.json", include_in_schema=False)
        def serve_manifest():
            return FileResponse(str(frontend_dir / "manifest.json"), media_type="application/manifest+json")

    if (frontend_dir / "sw.js").exists():
        @app.get("/sw.js", include_in_schema=False)
        def serve_service_worker():
            # Debe servirse desde la raíz (no /js/sw.js) para que su scope cubra todo el sitio.
            return FileResponse(str(frontend_dir / "sw.js"), media_type="application/javascript")
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
