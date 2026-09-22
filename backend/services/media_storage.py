"""
Farmhouse WhatsApp Center - Almacenamiento de archivos multimedia entrantes
Guarda en disco los archivos (fotos, videos, audios, documentos) que llegan
por WhatsApp y devuelve una URL relativa servida por FastAPI en /media/...
"""
import uuid
import mimetypes
from pathlib import Path

MEDIA_ROOT = Path(__file__).resolve().parent.parent / "media" / "incoming"
MEDIA_ROOT.mkdir(parents=True, exist_ok=True)

# Marcador guardado en Message.error_detail (columna ya existente, reutilizada) cuando la
# descarga de un adjunto de WhatsApp falla definitivamente (tanto el intento rápido inline como
# el reintento en background). Permite al frontend distinguir "todavía descargando" de "falló,
# mostrar botón Reintentar" sin agregar una columna de estado nueva.
MEDIA_DOWNLOAD_FAILED_MARKER = "media_download_failed"

def save_media_bytes(data: bytes, mime_type: str) -> str:
    ext = mimetypes.guess_extension(mime_type or "") or ""
    if ext == ".jpe":
        ext = ".jpg"
    filename = f"{uuid.uuid4().hex}{ext}"
    filepath = MEDIA_ROOT / filename
    with open(filepath, "wb") as f:
        f.write(data)
    return f"/media/incoming/{filename}"


# ==========================================================================
# Adjuntos de Comunicación Interna
# ==========================================================================
# Carpeta separada de "incoming" a propósito: lo de incoming llega de WhatsApp y se autoriza
# mirando la sucursal de la conversación; esto lo sube el personal y se autoriza mirando quién
# participa del hilo. Tenerlos mezclados obligaría a adivinar el origen por el nombre del
# archivo para saber qué regla aplicar (ver routers/media.py).
INTERNAL_ROOT = Path(__file__).resolve().parent.parent / "media" / "internal"
INTERNAL_ROOT.mkdir(parents=True, exist_ok=True)

# Tope por archivo. No es un límite técnico sino de sentido común: esto es para una foto de un
# faltante o una factura, no para mandar videos. Subirlo obliga a mirar el disco del contenedor.
MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024  # 10 MB

# Lista blanca, no lista negra: lo que no está acá se rechaza. Evita que el sistema termine
# sirviendo ejecutables o HTML (que el navegador ejecutaría en el origen de la app).
ALLOWED_ATTACHMENT_MIMES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "image/heic": ".heic",
    "application/pdf": ".pdf",
}


def save_internal_attachment(data: bytes, mime_type: str) -> str:
    """
    Guarda un adjunto del chat interno y devuelve su URL relativa.

    El nombre en disco es un uuid, nunca el que trajo el archivo: el original se guarda aparte
    en la base para mostrarlo y descargarlo. Así un nombre con barras, con puntos o repetido no
    puede pisar otro archivo ni salirse de la carpeta.
    """
    ext = ALLOWED_ATTACHMENT_MIMES.get(mime_type) or mimetypes.guess_extension(mime_type or "") or ""
    filename = f"{uuid.uuid4().hex}{ext}"
    with open(INTERNAL_ROOT / filename, "wb") as f:
        f.write(data)
    return f"/media/internal/{filename}"
