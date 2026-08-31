"""
Farmhouse WhatsApp Center - Script de limpieza de datos de prueba
Borra TODAS las conversaciones, mensajes, comandas y contactos.
NO toca sucursales, usuarios ni dispositivos.

Uso: python backend/scripts/reset_test_data.py
(Ejecutar desde la carpeta backend, con el entorno virtual activado
y el archivo .env apuntando a la base de datos real)
"""
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

import shutil
from database import SessionLocal
from models.message import Message
from models.order import Order
from models.conversation import Conversation
from models.contact import Contact

def main():
    db = SessionLocal()
    try:
        msg_count = db.query(Message).delete()
        order_count = db.query(Order).delete()
        conv_count = db.query(Conversation).delete()
        contact_count = db.query(Contact).delete()
        db.commit()

        print(f"Mensajes borrados: {msg_count}")
        print(f"Comandas borradas: {order_count}")
        print(f"Conversaciones borradas: {conv_count}")
        print(f"Contactos borrados: {contact_count}")

        # Borrar también las fotos/archivos guardados en disco
        media_dir = BASE_DIR / "media" / "incoming"
        if media_dir.exists():
            shutil.rmtree(media_dir)
            media_dir.mkdir(parents=True, exist_ok=True)
            print(f"Carpeta de archivos multimedia limpiada: {media_dir}")

        print("Listo. Sucursales, usuarios y dispositivos NO fueron tocados.")
    finally:
        db.close()

if __name__ == "__main__":
    main()
