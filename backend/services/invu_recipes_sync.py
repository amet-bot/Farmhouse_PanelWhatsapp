"""
Recetas de Invu → invu_recipe_lines, para cruzar la merma con el uso real de cada insumo.

Qué se trae, por sucursal (cada una es una base aparte en Invu y el id del plato cambia):
  - la receta de cada plato que se vendió en los últimos RECENT_DAYS días (recipes/ingredients);
  - los ingredientes de cada opción de modificador elegida en ese tiempo (modifier-options/get):
    un bowl "Personalizado" lleva en su receta solo el envase y los cubiertos; lo que tiene
    adentro está en los modificadores ("Pollo Spiced" → 160 g de pollo).

Son unas 300 llamadas por sucursal. Los límites reales que devuelve Invu en sus cabeceras son
más bajos que los de la documentación: ~30 por minuto y ~1.000 por día por usuario de API
(medido: x-minlimit-remaining / x-daylimit-remaining). Por eso va en segundo plano, con una
pausa de 2.2 s entre llamadas (~12 min por sucursal), y una vez por SEMANA: las recetas cambian
poco y así no se come la cuota diaria que también usan las ventas. El análisis lee lo guardado.
Un error en un plato no corta la pasada ni borra lo que ya había de ese plato; si se agota la
cuota del día, esa sucursal se corta ahí y sigue la próxima vez.
"""
import asyncio
import logging
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import func

from config import settings
from database import SessionLocal
from models.branch import Branch
from models.inventory_item import InventoryItem
from models.invu_sales import InvuRecipeLine, InvuSaleLine, InvuSaleModifier
from services import invu_client
from services.invu_sales_sync import hoy_panama

logger = logging.getLogger("farmhouse.invu")

RECENT_DAYS = 120
PAUSE_BETWEEN_CALLS = 2.2           # ~27 por minuto: el tope real de Invu es ~30 (compartido con ventas)
CHECK_INTERVAL_SECONDS = 24 * 60 * 60   # cada día se mira si toca
REFRESH_AFTER = timedelta(days=7)        # y se vuelve a traer si pasó una semana
STARTUP_DELAY_SECONDS = 10 * 60     # después de que ventas y proveedores terminen su primera pasada

_lock = threading.Lock()
_estado: Dict[str, Any] = {"running": False, "started_at": None, "finished_at": None, "last_error": None}


def estado() -> Dict[str, Any]:
    return dict(_estado)


def _cuota_agotada(error: Exception) -> bool:
    """Invu avisó que se terminó la cuota del día (ver invu_client._retry_after_seconds)."""
    return "cuota diaria" in str(error).lower()


def _cantidad(valor: Any) -> Optional[Decimal]:
    try:
        n = Decimal(str(valor))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return n if n > 0 else None


def _elegir_lineas(filas: List[Dict[str, Any]], producto_key: str) -> Dict[int, Dict[str, Any]]:
    """
    Una línea por ingrediente. Una receta puede repetir el ingrediente con cantidades distintas
    por tipo de orden (`order_type_id`, p. ej. delivery lleva otro envase): se toma la general
    (sin tipo de orden) y, si solo hay específicas, la primera. Sumarlas contaría de más.
    """
    elegidas: Dict[int, Dict[str, Any]] = {}
    for f in filas:
        try:
            pid = int(f.get(producto_key))
        except (TypeError, ValueError):
            continue
        general = f.get("order_type_id") in (None, "", 0)
        actual = elegidas.get(pid)
        if actual is None or (general and actual.get("order_type_id") not in (None, "", 0)):
            elegidas[pid] = f
    return elegidas


def _reemplazar(db, branch_id: int, source_type: str, source_id: int, source_name: Optional[str],
                lineas: List[Tuple[int, Optional[str], Optional[str], Decimal, Optional[str]]], ahora: datetime) -> None:
    db.query(InvuRecipeLine).filter(
        InvuRecipeLine.branch_id == branch_id,
        InvuRecipeLine.source_type == source_type,
        InvuRecipeLine.source_invu_id == source_id,
    ).delete(synchronize_session=False)
    for pid, code, name, qty, unit in lineas:
        db.add(InvuRecipeLine(
            branch_id=branch_id, source_type=source_type, source_invu_id=source_id,
            source_name=(source_name or None) and source_name[:200],
            product_invu_id=pid, product_code=(code or None) and str(code)[:50],
            product_name=(name or None) and str(name)[:200],
            quantity=qty, unit_name=(unit or None) and str(unit)[:30], synced_at=ahora,
        ))


def sync_recipes(db, branch_codes: Optional[List[str]] = None, pause: float = PAUSE_BETWEEN_CALLS) -> Dict[str, Any]:
    """Trae las recetas de los platos y modificadores vendidos en cada sucursal configurada."""
    credenciales = settings.invu_branch_credentials()
    if not credenciales:
        raise invu_client.InvuNotConfigured()

    ahora = datetime.now(timezone.utc)
    desde = hoy_panama() - timedelta(days=RECENT_DAYS)
    unidades = invu_client.units()
    productos = {i.invu_id: (i.code, i.name) for i in db.query(InventoryItem).filter(InventoryItem.invu_id.isnot(None))}
    resumen: Dict[str, Any] = {}

    for code in settings.INVU_SALES_BRANCH_CODES:
        if code not in credenciales or (branch_codes and code not in branch_codes):
            continue
        branch = db.query(Branch).filter(Branch.code == code).first()
        if not branch:
            continue
        cred = invu_client.Credenciales(*credenciales[code])

        platos = db.query(InvuSaleLine.invu_item_id, func.max(InvuSaleLine.name)).filter(
            InvuSaleLine.branch_id == branch.id, InvuSaleLine.business_date >= desde,
            InvuSaleLine.counted == True, InvuSaleLine.invu_item_id.isnot(None),  # noqa: E712
        ).group_by(InvuSaleLine.invu_item_id).all()
        modificadores = db.query(InvuSaleModifier.invu_modifier_id, func.max(InvuSaleModifier.name)).join(
            InvuSaleLine, InvuSaleLine.id == InvuSaleModifier.line_id
        ).filter(
            InvuSaleLine.branch_id == branch.id, InvuSaleLine.business_date >= desde,
            InvuSaleModifier.invu_modifier_id.isnot(None),
        ).group_by(InvuSaleModifier.invu_modifier_id).all()

        cuenta = {"platos": 0, "modificadores": 0, "sin_receta": 0, "errores": 0}
        for item_id, nombre in platos:
            try:
                filas = invu_client.recipe_ingredients(cred, item_id)
            except invu_client.InvuError as e:
                cuenta["errores"] += 1
                logger.warning(f"[Invu] Receta de '{nombre}' ({code}) no disponible: {e}")
                if _cuota_agotada(e):
                    cuenta["cortado"] = True
                    break
                time.sleep(pause)
                continue
            lineas = []
            for pid, f in _elegir_lineas(filas or [], "product_id").items():
                qty = _cantidad(f.get("quantity"))
                if qty is not None:
                    lineas.append((pid, f.get("product_code"), f.get("product_name"), qty, f.get("unit_name")))
            _reemplazar(db, branch.id, "item", int(item_id), nombre, lineas, ahora)
            db.commit()
            cuenta["platos" if lineas else "sin_receta"] += 1
            time.sleep(pause)

        for option_id, nombre in ([] if cuenta.get("cortado") else modificadores):
            try:
                opcion = invu_client.modifier_option_ingredients(cred, option_id)
            except invu_client.InvuError as e:
                cuenta["errores"] += 1
                logger.warning(f"[Invu] Modificador '{nombre}' ({code}) no disponible: {e}")
                if _cuota_agotada(e):
                    cuenta["cortado"] = True
                    break
                time.sleep(pause)
                continue
            lineas = []
            for pid, f in _elegir_lineas((opcion or {}).get("ingredients") or [], "product_id").items():
                qty = _cantidad(f.get("quantity"))
                if qty is None:
                    continue
                pcode, pname = productos.get(pid, (None, None))
                unidad = unidades.get(int(f["unit_id"])) if f.get("unit_id") else None
                lineas.append((pid, pcode, pname, qty, unidad))
            _reemplazar(db, branch.id, "modifier", int(option_id), nombre, lineas, ahora)
            db.commit()
            if lineas:
                cuenta["modificadores"] += 1
            time.sleep(pause)

        resumen[code] = cuenta
        logger.info(f"[Invu] Recetas de {code}: {cuenta}")
    return resumen


def ultima_sincronizacion(db) -> Optional[datetime]:
    return db.query(func.max(InvuRecipeLine.synced_at)).scalar()


def _correr() -> None:
    """Una pasada completa con su propia sesión; nunca dos a la vez."""
    if not _lock.acquire(blocking=False):
        return
    _estado.update(running=True, started_at=datetime.now(timezone.utc), last_error=None)
    db = SessionLocal()
    try:
        sync_recipes(db)
    except invu_client.InvuNotConfigured:
        logger.info("[Invu] Sin credenciales de sucursal: no hay recetas que traer.")
    except Exception as e:  # noqa: BLE001 — una pasada que falla no tumba el servidor
        _estado["last_error"] = str(e)[:300]
        logger.exception("[Invu] Falló la sincronización de recetas.")
    finally:
        db.close()
        _estado.update(running=False, finished_at=datetime.now(timezone.utc))
        _lock.release()


def lanzar_en_segundo_plano() -> bool:
    """Arranca una pasada en un hilo aparte (el botón del panel). False si ya había una corriendo."""
    if _estado["running"]:
        return False
    threading.Thread(target=_correr, name="invu-recipes", daemon=True).start()
    return True


def _toca_actualizar() -> bool:
    db = SessionLocal()
    try:
        ultima = ultima_sincronizacion(db)
    finally:
        db.close()
    if ultima is None:
        return True
    if ultima.tzinfo is None:
        ultima = ultima.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - ultima >= REFRESH_AFTER


async def run_recipes_sync_loop() -> None:
    """
    Al arrancar (con demora) y después una vez por día se fija si toca: trae las recetas si
    nunca se trajeron o si ya pasó una semana. Nunca bajo pytest (ver debe_arrancar_loop).
    """
    await asyncio.sleep(STARTUP_DELAY_SECONDS)
    while True:
        try:
            if await asyncio.to_thread(_toca_actualizar):
                await asyncio.to_thread(_correr)
        except Exception:
            logger.exception("[Invu] Error en una pasada de recetas.")
        await asyncio.sleep(CHECK_INTERVAL_SECONDS)


def debe_arrancar_loop() -> bool:
    return "PYTEST_CURRENT_TEST" not in os.environ and bool(settings.invu_branch_credentials())
