"""
Genera TODAS las claves del formulario del colaborador, de una vez. Se corre en tu computadora (no en
Railway) y la salida va a tu pantalla: cópiala a las variables de cada proyecto y guárdala en tu gestor
de contraseñas. No la pegues en chats, correos ni repositorios.

    pip install cryptography
    python public_intake/tools/gen_keys.py

Cada clave va a UN solo lugar (y la de administración, a dos). La llave PRIVADA solo existe en Farmhouse
Link: es lo que impide que el servicio público (o quien lo comprometa) pueda abrir un solo dato.
"""
import base64
import secrets

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec


def main() -> None:
    private = ec.generate_private_key(ec.SECP256R1())
    pem = private.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    public_raw = private.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    admin_key = secrets.token_urlsafe(48)

    print("=" * 78)
    print("PROYECTO DE FARMHOUSE LINK (el sistema central)  ->  Railway > Variables")
    print("=" * 78)
    print("DATA_ENCRYPTION_KEY=" + Fernet.generate_key().decode())
    print("INTAKE_PRIVATE_KEY=" + base64.b64encode(pem).decode())
    print("INTAKE_ADMIN_KEY=" + admin_key)
    print("INTAKE_SERVICE_URL=https://<la-direccion-del-proyecto-del-formulario>")
    print()
    print("=" * 78)
    print("PROYECTO DEL FORMULARIO (public_intake)  ->  Railway > Variables")
    print("=" * 78)
    print("PUBLIC_KEY=" + base64.urlsafe_b64encode(public_raw).decode().rstrip("="))
    print("ADMIN_KEY=" + admin_key)
    print("ENVIRONMENT=production")
    print()
    print("IMPORTANTE: INTAKE_ADMIN_KEY (central) y ADMIN_KEY (formulario) son LA MISMA clave.")
    print("INTAKE_PRIVATE_KEY y DATA_ENCRYPTION_KEY: guarda una copia fuera de Railway; sin ellas")
    print("los datos cifrados no se pueden recuperar.")


if __name__ == "__main__":
    main()
