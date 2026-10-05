"""
Descifra los sobres del formulario público del colaborador (servicio aparte: public_intake/).

El navegador del colaborador cifra con la llave PÚBLICA (frontend en public_intake/static/crypto.js):
ECDH P-256 con una llave efímera por envío -> HKDF-SHA256 -> AES-256-GCM. Solo este sistema tiene la
llave PRIVADA (INTAKE_PRIVATE_KEY, un PEM en base64): ni el servicio público ni su base de datos pueden
abrir un sobre. Si la llave no está configurada, no se descifra nada (falla cerrado).
"""
import base64
import json
import logging
from functools import lru_cache

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from config import settings

logger = logging.getLogger("farmhouse.intake_crypto")

INFO = b"farmhouse-intake-v1"


class IntakeCryptoError(Exception):
    """El sobre no se pudo abrir (sobre alterado o mal formado)."""


class IntakeKeyError(IntakeCryptoError):
    """Problema de CONFIGURACIÓN (falta la llave privada o no es válida), no del sobre: no se debe descartar nada."""


def _b64u(value: str) -> bytes:
    value = value or ""
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


@lru_cache(maxsize=4)
def _load_private(pem_b64: str):
    try:
        return serialization.load_pem_private_key(base64.b64decode(pem_b64), password=None)
    except Exception as exc:
        raise IntakeKeyError("INTAKE_PRIVATE_KEY no es una llave privada válida.") from exc


def is_configured() -> bool:
    return bool((settings.INTAKE_PRIVATE_KEY or "").strip())


def decrypt_envelope(envelope: dict) -> dict:
    """Devuelve el JSON que el colaborador cifró. Lanza IntakeCryptoError si no se puede abrir."""
    key_b64 = (settings.INTAKE_PRIVATE_KEY or "").strip()
    if not key_b64:
        raise IntakeKeyError("Falta INTAKE_PRIVATE_KEY.")
    private = _load_private(key_b64)
    try:
        if envelope.get("v") != 1:
            raise IntakeCryptoError("Versión de sobre desconocida.")
        epk = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), _b64u(envelope["epk"]))
        shared = private.exchange(ec.ECDH(), epk)
        key = HKDF(algorithm=hashes.SHA256(), length=32, salt=_b64u(envelope["salt"]), info=INFO).derive(shared)
        plain = AESGCM(key).decrypt(_b64u(envelope["iv"]), _b64u(envelope["ct"]), INFO)
        data = json.loads(plain.decode("utf-8"))
    except IntakeCryptoError:
        raise
    except (InvalidTag, ValueError, KeyError, TypeError) as exc:
        raise IntakeCryptoError("Sobre inválido o alterado.") from exc
    if not isinstance(data, dict):
        raise IntakeCryptoError("Contenido inesperado.")
    return data
