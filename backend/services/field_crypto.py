"""
Cifrado de campos sensibles en la base de datos (datos personales, de salud y bancarios de los
colaboradores). Quien llegue a la base (un respaldo robado, una consulta directa, un volcado) ve
texto cifrado, no cédulas ni cuentas.

- Algoritmo: Fernet (AES-128-CBC + HMAC-SHA256, con marca de tiempo), de `cryptography`. Cada valor
  se cifra con su propio IV: dos personas con el mismo dato no dejan el mismo texto cifrado.
- Clave: DATA_ENCRYPTION_KEY (una clave Fernet). Para rotarla se pone la nueva en
  DATA_ENCRYPTION_KEY y la anterior en DATA_ENCRYPTION_KEY_PREVIOUS: se lee con las dos y se escribe
  con la nueva.
- Sin clave en PRODUCCIÓN: falla cerrado (EncryptionNotConfigured). Guardar datos sensibles sin cifrar
  "porque no había clave" es peor que no guardarlos. En desarrollo y pruebas se deriva una clave de
  SECRET_KEY para no pedir configuración.
- Buscar por un dato cifrado (la cédula) no se puede en SQL; para eso está el índice ciego:
  HMAC-SHA256 del dato normalizado. Con él se busca "esta cédula exacta" y se detectan duplicados sin
  guardar la cédula en claro. Usa la clave MÁS ANTIGUA configurada, así rotar el cifrado no invalida
  los índices (no quites la clave anterior sin recalcularlos).

Uso: `Column(EncryptedText)`, `Column(EncryptedDate)`, `Column(EncryptedDecimal)`. El resto del código
sigue leyendo y escribiendo str / date / Decimal como siempre.
"""
import base64
import hashlib
import hmac
import logging
from datetime import date
from decimal import Decimal
from functools import lru_cache
from typing import List, Optional

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from sqlalchemy.types import Text, TypeDecorator

from config import settings

logger = logging.getLogger("farmhouse.crypto")

PREFIX = "enc:v1:"


class EncryptionNotConfigured(RuntimeError):
    """En producción falta DATA_ENCRYPTION_KEY (o no es una clave Fernet válida)."""


def _configured_keys() -> List[str]:
    keys = [k.strip() for k in (settings.DATA_ENCRYPTION_KEY, settings.DATA_ENCRYPTION_KEY_PREVIOUS) if k and k.strip()]
    if keys:
        return keys
    if settings.ENVIRONMENT == "production":
        raise EncryptionNotConfigured("Falta DATA_ENCRYPTION_KEY: los datos sensibles no se pueden guardar ni leer.")
    # Desarrollo y pruebas: clave derivada, estable mientras SECRET_KEY no cambie.
    digest = hashlib.sha256(f"farmhouse-field-encryption:{settings.SECRET_KEY}".encode("utf-8")).digest()
    return [base64.urlsafe_b64encode(digest).decode("ascii")]


@lru_cache(maxsize=8)
def _build(keys: tuple) -> MultiFernet:
    try:
        return MultiFernet([Fernet(k.encode("ascii")) for k in keys])
    except (ValueError, TypeError) as exc:
        raise EncryptionNotConfigured("DATA_ENCRYPTION_KEY no es una clave Fernet válida.") from exc


def _cipher() -> MultiFernet:
    return _build(tuple(_configured_keys()))


def encrypt_text(value: str) -> str:
    return PREFIX + _cipher().encrypt(value.encode("utf-8")).decode("ascii")


def decrypt_text(token: str) -> str:
    if not token.startswith(PREFIX):
        # Algo que no se cifró con esta capa: no se devuelve como si fuera un dato válido.
        raise ValueError("Valor sin cifrar en una columna cifrada.")
    try:
        return _cipher().decrypt(token[len(PREFIX):].encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        logger.error("[Crypto] No se pudo descifrar un valor: la clave no coincide o el dato está alterado.")
        raise ValueError("No se pudo descifrar el dato (clave incorrecta o dato alterado).") from exc


def blind_index(value: Optional[str]) -> Optional[str]:
    """HMAC-SHA256 (hex) del valor normalizado (mayúsculas, sin espacios). None si no hay valor."""
    if value is None:
        return None
    normalized = "".join(str(value).split()).upper()
    if not normalized:
        return None
    key = hashlib.sha256(("farmhouse-blind-index:" + _configured_keys()[-1]).encode("utf-8")).digest()
    return hmac.new(key, normalized.encode("utf-8"), hashlib.sha256).hexdigest()


class EncryptedText(TypeDecorator):
    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return None if value is None else encrypt_text(str(value))

    def process_result_value(self, value, dialect):
        return None if value is None else decrypt_text(value)


class EncryptedDate(TypeDecorator):
    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        return encrypt_text(value.isoformat() if isinstance(value, date) else str(value))

    def process_result_value(self, value, dialect):
        return None if value is None else date.fromisoformat(decrypt_text(value))


class EncryptedDecimal(TypeDecorator):
    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return None if value is None else encrypt_text(str(Decimal(str(value))))

    def process_result_value(self, value, dialect):
        return None if value is None else Decimal(decrypt_text(value))


def mask_id(value: Optional[str]) -> str:
    """Para listas: deja ver lo justo para reconocer a la persona (8-753-442 -> 8-•••-442)."""
    v = (value or "").strip()
    if len(v) <= 5:
        return "•" * len(v)
    return v[:2] + "•" * (len(v) - 5) + v[-3:]
