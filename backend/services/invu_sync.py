"""
Sincronización de proveedores desde Invu POS.

Va en un solo sentido: **Invu manda**. Los proveedores se dan de alta allá, con su RUC y su
contacto, y acá se guarda una copia para que el panel pueda autocompletar y mostrar sin
depender de que la API esté arriba en ese momento.

Cómo empareja lo que ya existía: por `invu_id` cuando el proveedor ya se sincronizó alguna vez,
y si no, por nombre sin distinguir mayúsculas. Lo segundo importa una sola vez: los proveedores
del panel se cargaron a mano antes de que existiera esta integración, y sin ese paso la primera
sincronización crearía un "Verduras del Valle" al lado del "verduras del valle" que ya estaba, y
los cargamentos viejos quedarían colgando del duplicado.

Un proveedor sin `invu_id` después de sincronizar es uno que solo existe en el panel. No se
borra ni se desactiva: puede ser un proveedor chico que nunca se cargó en Invu, y hacerlo
desaparecer se llevaría por delante la referencia de los cargamentos que lo mencionan.
"""
import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from sqlalchemy import func

from database import SessionLocal
from models.supplier import Supplier
from services import invu_client

logger = logging.getLogger("farmhouse.invu")

# Una vez al día alcanza: un proveedor nuevo no es algo que pase cada hora, y el botón de
# "Sincronizar" está ahí para cuando alguien acaba de cargar uno y lo quiere ya.
SYNC_INTERVAL_SECONDS = 24 * 60 * 60

# Espera antes de la primera pasada al arrancar: que el servidor termine de levantar y conteste
# el health check antes de ponerse a hablar con una API de afuera.
STARTUP_DELAY_SECONDS = 20

# Ver _sync_en_segundo_plano: diferencia de huso entre synced_at (UTC) y updated_at de Invu.
INCREMENTAL_MARGIN = timedelta(days=1)


def _texto(valor: Any, tope: int) -> Optional[str]:
    """Invu manda vacíos de varias formas: None, "", "0" y "0000-00-00" (ver sus convenciones)."""
    if valor is None:
        return None
    texto = str(valor).strip()
    if not texto or texto in ("0", "0000-00-00", "null"):
        return None
    return texto[:tope]


def _primer_telefono(fila: Dict[str, Any]) -> Optional[str]:
    """Invu guarda hasta tres teléfonos; el panel muestra uno. Se toma el primero que exista."""
    for clave in ("phone_1", "phone_2", "phone_3"):
        numero = _texto(fila.get(clave), 30)
        if numero:
            return numero
    return None


def _entero(valor: Any) -> Optional[int]:
    try:
        numero = int(str(valor).strip())
    except (TypeError, ValueError):
        return None
    return numero or None


def _aplicar(proveedor: Supplier, fila: Dict[str, Any], ahora: datetime) -> bool:
    """Copia los campos de Invu al proveedor local. Devuelve si algo cambió de verdad."""
    nuevos = {
        "invu_id": _entero(fila.get("id")),
        "name": _texto(fila.get("name"), 150) or proveedor.name,
        "phone": _primer_telefono(fila),
        "code": _texto(fila.get("code"), 50),
        "tax_id": _texto(fila.get("tax_id"), 50),
        "contact_name": _texto(fila.get("contact_name"), 150),
        "email": _texto(fila.get("email"), 150),
        "delivery_day": _entero(fila.get("delivery_day")),
        # `status` de Invu: 1 activo. Cualquier otra cosa lo apaga acá, pero nunca lo borra —
        # los cargamentos viejos lo siguen nombrando.
        "active": str(fila.get("status", 1)).strip() in ("1", "true", "True"),
    }

    cambio = False
    for campo, valor in nuevos.items():
        if getattr(proveedor, campo) != valor:
            setattr(proveedor, campo, valor)
            cambio = True

    proveedor.synced_at = ahora
    return cambio


def _nombre_libre(db, nombre: str, fila: Dict[str, Any], propio_id: Optional[int]) -> str:
    """`nombre`, o `nombre (S29)` si otro proveedor del panel ya se llama así."""
    def ocupado(candidato: str) -> bool:
        q = db.query(Supplier.id).filter(func.lower(Supplier.name) == candidato.lower())
        if propio_id is not None:
            q = q.filter(Supplier.id != propio_id)
        return q.first() is not None

    if not ocupado(nombre):
        return nombre
    sufijo = _texto(fila.get("code"), 20) or f"Invu {_entero(fila.get('id'))}"
    return f"{nombre[:150 - len(sufijo) - 3]} ({sufijo})"


def sync_providers(db, updated_after: Optional[datetime] = None) -> Dict[str, Any]:
    """
    Trae los proveedores de Invu y los deja al día en la base local.

    `updated_after` hace la pasada incremental. Se deja decidir a quien llama: el sweep diario
    pide solo lo que cambió desde la última sincronización, y el botón del panel pide todo,
    porque quien lo aprieta justamente sospecha que algo no está.
    """
    if not invu_client.is_configured():
        raise invu_client.InvuNotConfigured()

    ahora = datetime.now(timezone.utc)
    creados = enlazados = actualizados = 0
    vistos = 0

    for fila in invu_client.iter_providers(updated_after=updated_after):
        invu_id = _entero(fila.get("id"))
        nombre = _texto(fila.get("name"), 150)
        if not invu_id or not nombre:
            continue
        vistos += 1

        proveedor = db.query(Supplier).filter(Supplier.invu_id == invu_id).first()
        nuevo = False
        enlazado = False

        if not proveedor:
            # Primera vez: puede ser uno que ya estaba cargado a mano con el mismo nombre.
            proveedor = db.query(Supplier).filter(
                func.lower(Supplier.name) == nombre.lower(),
                Supplier.invu_id.is_(None),
            ).first()
            if proveedor:
                enlazado = True

        # El nombre es único en el panel y en Invu no: hay dos "Fruteria Mimi" (S24 y S29,
        # archivado). Sin esto el segundo chocaba contra el primero y la pasada ENTERA fallaba,
        # sin guardar ninguno de los 41. El repetido queda con su código de Invu al lado.
        nombre_final = _nombre_libre(db, nombre, fila, proveedor.id if proveedor else None)
        if not proveedor:
            proveedor = Supplier(name=nombre_final)
            db.add(proveedor)
            nuevo = True

        cambio = _aplicar(proveedor, {**fila, "name": nombre_final}, ahora)
        # La sesión no hace autoflush: sin esto la fila siguiente no "ve" a esta al buscar
        # nombres repetidos (y el choque recién aparecía en el commit del final).
        db.flush()

        if nuevo:
            creados += 1
        elif enlazado:
            enlazados += 1
        elif cambio:
            actualizados += 1

    db.commit()

    resumen = {
        "recibidos": vistos,
        "creados": creados,
        "enlazados": enlazados,
        "actualizados": actualizados,
        "sincronizado_en": ahora,
    }
    logger.info(f"[Invu] Proveedores sincronizados: {resumen}")
    return resumen


def ultima_sincronizacion(db) -> Optional[datetime]:
    return db.query(func.max(Supplier.synced_at)).scalar()


def _sync_en_segundo_plano() -> None:
    """Una pasada con su propia sesión, sin tumbar nada si Invu no contesta."""
    db = SessionLocal()
    try:
        desde = ultima_sincronizacion(db)
        # Con margen: `synced_at` se guarda en UTC y el `updated_at` de Invu no dice su huso
        # (en hora de Panamá serían 5 h menos). Pidiendo "cambiados después de <hora UTC>" se perdían para siempre
        # los proveedores creados o editados en las horas siguientes a cada pasada. Un día de
        # margen repasa unos pocos de más, que no cambian nada.
        if desde:
            desde = desde - INCREMENTAL_MARGIN
        sync_providers(db, updated_after=desde)
    except invu_client.InvuNotConfigured:
        logger.info("[Invu] Integración no configurada; no hay proveedores que sincronizar.")
    except Exception:
        logger.exception("[Invu] Falló la sincronización automática de proveedores.")
    finally:
        db.close()


async def run_provider_sync_loop() -> None:
    """
    Sincroniza al arrancar y una vez por día.

    Es el segundo loop en segundo plano del proyecto (el otro es el seguimiento del bot, ver
    services/bot_followup.py) y sigue sus mismas reglas: nunca arranca bajo pytest, y una
    excepción en una pasada no tumba el loop. La llamada bloqueante va a un hilo aparte para no
    frenar el event loop mientras espera a una API de afuera.
    """
    if not invu_client.is_configured():
        logger.info("[Invu] Sin credenciales: el panel sigue administrando sus proveedores.")
        return

    await asyncio.sleep(STARTUP_DELAY_SECONDS)
    while True:
        try:
            await asyncio.to_thread(_sync_en_segundo_plano)
        except Exception:
            logger.exception("[Invu] Error en una pasada de la sincronización de proveedores.")
        await asyncio.sleep(SYNC_INTERVAL_SECONDS)


def debe_arrancar_loop() -> bool:
    """Misma guarda que el loop del bot: bajo pytest ningún loop toca la base real."""
    return "PYTEST_CURRENT_TEST" not in os.environ and invu_client.is_configured()
