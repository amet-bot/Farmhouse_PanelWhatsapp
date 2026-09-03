import os
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Optional, List

BASE_DIR = Path(__file__).resolve().parent

DEFAULT_DEV_SECRET = "farmhouse_secret_jwt_key_super_secure_2026_pa"

class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(BASE_DIR / ".env"),
        env_file_encoding="utf-8-sig",
        case_sensitive=True,
        extra="ignore"
    )

    ENVIRONMENT: str = "development"
    APP_NAME: str = "Farmhouse WhatsApp Center"
    API_V1_STR: str = "/api"
    
    HOST: str = "0.0.0.0"
    PORT: int = 8000
    DEBUG: bool = True
    
    # MySQL Database Configuration
    DB_SERVER: str = "localhost"
    DB_PORT: int = 3306
    DB_NAME: str = "FarmhouseWhatsAppCenter"
    DB_USER: str = "root"
    DB_PASSWORD: str = ""

    DATABASE_URL: Optional[str] = None

    # JWT Authentication
    SECRET_KEY: str = DEFAULT_DEV_SECRET
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 480

    # CORS Configuration
    ALLOWED_ORIGINS: str = "http://127.0.0.1:8000,http://localhost:8000,http://127.0.0.1:5500,http://localhost:5500,http://127.0.0.1:3000,http://localhost:3000,null"

    # WhatsApp Service (mock | meta)
    WHATSAPP_MODE: str = "mock"
    META_WA_API_URL: str = "https://graph.facebook.com/v20.0"
    META_WA_PHONE_NUMBER_ID: Optional[str] = None
    META_WA_ACCESS_TOKEN: Optional[str] = None
    META_WA_VERIFY_TOKEN: str = "farmhouse_meta_verify_token_secure_2026"
    META_APP_SECRET: Optional[str] = None
    META_CATALOG_ID: Optional[str] = None
    # Número real (E.164, solo dígitos) de la línea de WhatsApp Business de Farmhouse,
    # usado para construir el enlace wa.me del botón "Enviar Pedido a WhatsApp" en /menu.
    META_WA_DISPLAY_NUMBER: Optional[str] = None
    # URL pública base del panel (sin slash final), usada para armar enlaces como el del
    # Menú Digital que el bot manda por WhatsApp.
    PUBLIC_BASE_URL: str = "https://farmhousepanelwhatsapp-production.up.railway.app"

    # Web Push (Notificaciones push del navegador vía VAPID)
    VAPID_PUBLIC_KEY: Optional[str] = None
    VAPID_PRIVATE_KEY: Optional[str] = None
    VAPID_CLAIM_SUB: str = "mailto:admin@farmhouse.pa"

    def get_allowed_origins(self) -> List[str]:
        if not self.ALLOWED_ORIGINS:
            return ["http://127.0.0.1:8000", "http://localhost:8000"]
        origins = [origin.strip() for origin in self.ALLOWED_ORIGINS.split(",") if origin.strip()]
        if self.ENVIRONMENT == "production":
            origins = [o for o in origins if o.lower() != "null"]
        return origins

    def get_database_url(self) -> str:
        db_url = (
            self.DATABASE_URL
            or os.environ.get("DATABASE_URL")
            or os.environ.get("MYSQL_URL")
            or os.environ.get("MYSQLPRIVATE_URL")
        )
        if db_url:
            if db_url.startswith("mysql://"):
                db_url = db_url.replace("mysql://", "mysql+pymysql://", 1)
            if "charset=" not in db_url:
                separator = "&" if "?" in db_url else "?"
                db_url = f"{db_url}{separator}charset=utf8mb4"
            return db_url

        # Soporte nativo para variables individuales inyectadas por Railway / Docker
        server = os.environ.get("MYSQLHOST") or os.environ.get("MYSQL_HOST") or self.DB_SERVER
        port = os.environ.get("MYSQLPORT") or os.environ.get("MYSQL_PORT") or str(self.DB_PORT)
        user = os.environ.get("MYSQLUSER") or os.environ.get("MYSQL_USER") or self.DB_USER
        password = os.environ.get("MYSQLPASSWORD") or os.environ.get("MYSQL_PASSWORD") or self.DB_PASSWORD
        dbname = os.environ.get("MYSQLDATABASE") or os.environ.get("MYSQL_DATABASE") or self.DB_NAME

        pwd_part = f":{password}" if password else ""
        return (
            f"mysql+pymysql://{user}{pwd_part}@"
            f"{server}:{port}/{dbname}?charset=utf8mb4"
        )

    def validate_production_security(self) -> None:
        """Valida la configuración de seguridad registrando avisos sin abortar el arranque."""
        if self.ENVIRONMENT == "production":
            if self.SECRET_KEY == DEFAULT_DEV_SECRET or len(self.SECRET_KEY) < 32:
                import logging
                logging.getLogger("farmhouse.config").warning("[Security Warning] Se recomienda configurar un SECRET_KEY seguro de al menos 32 caracteres.")
        
        if self.WHATSAPP_MODE == "meta":
            import logging
            log = logging.getLogger("farmhouse.config")
            if not self.META_WA_PHONE_NUMBER_ID or not self.META_WA_PHONE_NUMBER_ID.strip():
                log.warning("[Config Warning] WHATSAPP_MODE=meta pero META_WA_PHONE_NUMBER_ID no está configurado.")
            if not self.META_WA_ACCESS_TOKEN or not self.META_WA_ACCESS_TOKEN.strip():
                log.warning("[Config Warning] WHATSAPP_MODE=meta pero META_WA_ACCESS_TOKEN no está configurado.")
            if not self.META_APP_SECRET or not self.META_APP_SECRET.strip():
                log.warning("[Config Warning] META_APP_SECRET no está configurado. La validación de firma de webhooks funcionará en modo desarrollo/tolerante.")

def mask_secret(secret: Optional[str], keep_chars: int = 4) -> str:
    """Enmascara cadenas sensibles para que no se filtren en logs."""
    if not secret:
        return "<no configurado>"
    if len(secret) <= keep_chars * 2:
        return "***"
    return f"{secret[:keep_chars]}...{secret[-keep_chars:]}"

def mask_phone(phone: Optional[str]) -> str:
    """Enmascara números telefónicos en logs de auditoría."""
    if not phone:
        return "-"
    clean = str(phone).strip()
    if len(clean) <= 6:
        return "***"
    return f"{clean[:4]}****{clean[-2:]}"

settings = Settings()
