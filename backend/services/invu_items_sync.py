"""
Sincronización de insumos desde Invu POS (sus "Ingredientes", admin.invupos.com/producto).

Mismo espíritu que la de proveedores (services/invu_sync.py), con dos diferencias pedidas a
propósito:
  - El panel SIGUE pudiendo crear insumos a mano (al recibir un cargamento hace falta sobre la
    marcha). Si después aparece en Invu con el mismo nombre, se empareja en vez de duplicarse.
  - Los archivados en Invu no se crean acá: solo se apagan los que ya estaban sincronizados.
    Invu tiene muchos archivados con el mismo nombre que uno activo (versiones viejas), y
    traerlos llenaría el catálogo de repetidos.

Qué se trae de cada uno: código (P204), nombre, unidad de inventario, categoría, tipo
(preparación de la casa si tiene subreceta, materia prima si no) y el costo de Invu como
referencia. La UNIDAD de un insumo que ya tiene movimientos no se pisa: sus cargamentos, mermas
y conteos están anotados en esa unidad, y cambiarla ("kg" → "gramos") haría leer mal todo lo
registrado. Se toma la de Invu solo al crearlo o si todavía no se usó.
"""
import logging
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Optional

from sqlalchemy import func

from models.inventory_item import InventoryItem, KIND_HOUSE, KIND_RAW
from models.inventory_movement import InventoryMovement
from models.shipment import ShipmentItem
from models.stock_count import StockCountItem
from models.transfer import TransferItem
from models.waste import WasteItem
from services import invu_client
from services.invu_sync import _entero, _texto

logger = logging.getLogger("farmhouse.invu")

# Invu devuelve "Required Unit" (id 1) como unidad de relleno en algunos ingredientes.
_UNIDADES_SIN_SENTIDO = {"", "required unit"}
UNIDAD_POR_DEFECTO = "unidad"


def _costo(valor: Any) -> Optional[Decimal]:
    try:
        costo = Decimal(str(valor))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return costo.quantize(Decimal("0.0001")) if costo > 0 else None


def _unidad(fila: Dict[str, Any], unidades: Dict[int, str]) -> Optional[str]:
    for clave in ("inventory_unit_id", "unit_id"):
        nombre = unidades.get(_entero(fila.get(clave)) or 0, "")
        if nombre.strip().lower() not in _UNIDADES_SIN_SENTIDO:
            return nombre.strip()[:30]
    return None


def _tiene_movimientos(db, item_id: int) -> bool:
    for modelo in (ShipmentItem, WasteItem, StockCountItem, TransferItem, InventoryMovement):
        if db.query(modelo.id).filter(modelo.inventory_item_id == item_id).first():
            return True
    return False


def _nombre_libre(db, nombre: str, fila: Dict[str, Any], propio_id: Optional[int]) -> str:
    """`nombre`, o `nombre (P204)` si otro insumo del panel ya se llama así (el nombre es único)."""
    def ocupado(candidato: str) -> bool:
        q = db.query(InventoryItem.id).filter(func.lower(InventoryItem.name) == candidato.lower())
        if propio_id is not None:
            q = q.filter(InventoryItem.id != propio_id)
        return q.first() is not None

    if not ocupado(nombre):
        return nombre
    sufijo = _texto(fila.get("code"), 20) or f"Invu {_entero(fila.get('id'))}"
    return f"{nombre[:150 - len(sufijo) - 3]} ({sufijo})"


def sync_items(db, updated_after: Optional[datetime] = None) -> Dict[str, Any]:
    """
    Trae los ingredientes de Invu y deja el catálogo de insumos al día.

    `updated_after` hace la pasada incremental (la diaria); el botón del panel pide todo.
    """
    if not invu_client.is_configured():
        raise invu_client.InvuNotConfigured()

    ahora = datetime.now(timezone.utc)
    unidades = invu_client.units()
    categorias = invu_client.ingredient_categories()
    creados = enlazados = actualizados = apagados = 0
    vistos = 0

    for fila in invu_client.iter_ingredients(updated_after=updated_after):
        invu_id = _entero(fila.get("id"))
        nombre = _texto(fila.get("name"), 150)
        if not invu_id or not nombre:
            continue
        vistos += 1
        activo_en_invu = str(fila.get("status", 1)).strip() in ("1", "true", "True")

        item = db.query(InventoryItem).filter(InventoryItem.invu_id == invu_id).first()

        if not activo_en_invu:
            # Archivado allá: se apaga si ya estaba sincronizado; nunca se crea ni se borra
            # (los cargamentos viejos lo siguen nombrando).
            if item and item.active:
                item.active = False
                item.synced_at = ahora
                apagados += 1
                db.flush()
            continue

        nuevo = enlazado = False
        if not item:
            # Primera vez: puede ser uno que ya estaba cargado a mano con el mismo nombre.
            item = db.query(InventoryItem).filter(
                func.lower(InventoryItem.name) == nombre.lower(),
                InventoryItem.invu_id.is_(None),
            ).first()
            enlazado = item is not None

        nombre_final = _nombre_libre(db, nombre, fila, item.id if item else None)
        unidad = _unidad(fila, unidades)
        if not item:
            item = InventoryItem(name=nombre_final, unit=unidad or UNIDAD_POR_DEFECTO)
            db.add(item)
            nuevo = True

        nuevos = {
            "invu_id": invu_id,
            "name": nombre_final,
            "code": _texto(fila.get("code"), 50),
            "category": (categorias.get(_entero(fila.get("category_id")) or 0) or None),
            "kind": KIND_HOUSE if _entero(fila.get("subrecipe_id")) else KIND_RAW,
            "reference_cost": _costo(fila.get("cost")),
            "active": True,
        }
        if nuevos["category"]:
            nuevos["category"] = nuevos["category"][:50]
        if unidad and not nuevo and item.unit != unidad and not _tiene_movimientos(db, item.id):
            nuevos["unit"] = unidad

        cambio = False
        for campo, valor in nuevos.items():
            actual = getattr(item, campo)
            if campo == "reference_cost" and actual is not None and valor is not None:
                igual = Decimal(actual) == valor
            else:
                igual = actual == valor
            if not igual:
                setattr(item, campo, valor)
                cambio = True
        item.synced_at = ahora
        # La sesión no hace autoflush: sin esto la fila siguiente no "ve" a esta al buscar
        # nombres repetidos.
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
        "apagados": apagados,
        "sincronizado_en": ahora,
    }
    logger.info(f"[Invu] Insumos sincronizados: {resumen}")
    return resumen


def ultima_sincronizacion(db) -> Optional[datetime]:
    return db.query(func.max(InventoryItem.synced_at)).scalar()
