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
        if self.DATABASE_URL:
            return self.DATABASE_URL
        pwd_part = f":{self.DB_PASSWORD}" if self.DB_PASSWORD else ""
        return (
            f"mysql+pymysql://{self.DB_USER}{pwd_part}@"
            f"{self.DB_SERVER}:{self.DB_PORT}/{self.DB_NAME}?charset=utf8mb4"
        )

    def validate_production_security(self) -> None:
        """Valida que la configuración de producción no use secretos o configuraciones inseguras."""
        if self.ENVIRONMENT == "production":
            if self.SECRET_KEY == DEFAULT_DEV_SECRET or len(self.SECRET_KEY) < 32:
                raise ValueError("ERROR DE SEGURIDAD CRÍTICO: En producción debes configurar un SECRET_KEY seguro y aleatorio de al menos 32 caracteres.")
            if "null" in [o.lower() for o in self.ALLOWED_ORIGINS.split(",")]:
                raise ValueError("ERROR DE SEGURIDAD: El origen CORS 'null' no está permitido en entorno de producción.")
        
        if self.WHATSAPP_MODE == "meta":
            if not self.META_WA_PHONE_NUMBER_ID or not self.META_WA_PHONE_NUMBER_ID.strip():
                raise ValueError("ERROR: WHATSAPP_MODE está en 'meta' pero META_WA_PHONE_NUMBER_ID no está configurado.")
            if not self.META_WA_ACCESS_TOKEN or not self.META_WA_ACCESS_TOKEN.strip():
                raise ValueError("ERROR: WHATSAPP_MODE está en 'meta' pero META_WA_ACCESS_TOKEN no está configurado.")
            if not self.META_APP_SECRET or not self.META_APP_SECRET.strip():
                raise ValueError("ERROR CRÍTICO: WHATSAPP_MODE está en 'meta' pero META_APP_SECRET no está configurado. Es obligatorio para validar firmas de webhooks.")

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
