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

def save_media_bytes(data: bytes, mime_type: str) -> str:
    ext = mimetypes.guess_extension(mime_type or "") or ""
    if ext == ".jpe":
        ext = ".jpg"
    filename = f"{uuid.uuid4().hex}{ext}"
    filepath = MEDIA_ROOT / filename
    with open(filepath, "wb") as f:
        f.write(data)
    return f"/media/incoming/{filename}"
