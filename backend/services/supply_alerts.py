"""
Alerta de stock bajo: una vez al día (a partir de las 7:00 de Panamá), por sucursal, se avisa a
los encargados qué insumos están por debajo de su mínimo (ItemBranchSetting.min_quantity).
Solo insumos con mínimo cargado: sin mínimo no hay con qué comparar. Se manda una vez por día
y sucursal (marca en auditoría "alert.low_stock").
"""
import asyncio
import json
import logging
from datetime import datetime
from decimal import Decimal
from typing import Optional

from database import SessionLocal
from models.audit import AuditEvent
from models.branch import Branch
from models.inventory_item import InventoryItem
from models.supply import ItemBranchSetting
from services.audit import log_audit_event
from services.branch_hours import PANAMA_TZ
from services.push_service import notify_branch_staff

logger = logging.getLogger("farmhouse.supply_alerts")

ALERT_HOUR = 7
CHECK_INTERVAL_SECONDS = 30 * 60
STARTUP_DELAY_SECONDS = 180


def low_stock_rows(db, branch_id: int) -> list:
    """Insumos por debajo de su mínimo en esa sucursal: [{item, stock, min, par, unit, missing}]."""
    from routers.inventory import _existencia_map   # import diferido: routers importa services

    settings = db.query(ItemBranchSetting).filter(ItemBranchSetting.branch_id == branch_id, ItemBranchSetting.min_quantity.isnot(None)).all()
    if not settings:
        return []
    ids = [s.inventory_item_id for s in settings]
    items = {i.id: i for i in db.query(InventoryItem).filter(InventoryItem.id.in_(ids), InventoryItem.active == True).all()}  # noqa: E712
    stock = _existencia_map(db, branch_id, list(items))
    rows = []
    for s in settings:
        item = items.get(s.inventory_item_id)
        if item is None:
            continue
        actual = Decimal(stock.get(item.id, Decimal("0")))
        minimo = Decimal(s.min_quantity)
        if actual < minimo:
            rows.append({
                "inventory_item_id": item.id, "name": item.name, "unit": item.unit, "category": item.category,
                "stock": actual.quantize(Decimal("0.001")), "min_quantity": minimo, "par_quantity": s.par_quantity,
                "missing": (minimo - actual).quantize(Decimal("0.001")),
                "supplier_id": s.supplier_id, "supplier_name": s.supplier.name if s.supplier else None,
            })
    rows.sort(key=lambda r: -float(r["missing"] / r["min_quantity"]) if r["min_quantity"] else 0)
    return rows


def _already_sent(db, branch_id: int, day: str) -> bool:
    for ev in db.query(AuditEvent).filter(AuditEvent.action == "alert.low_stock", AuditEvent.branch_id == branch_id).order_by(AuditEvent.id.desc()).limit(3).all():
        try:
            if json.loads(ev.metadata_json or "{}").get("date") == day:
                return True
        except ValueError:
            continue
    return False


def send_if_due(now: Optional[datetime] = None, db=None) -> int:
    """Manda la alerta de las sucursales que la deban. Devuelve cuántas avisó."""
    now = now or datetime.now(PANAMA_TZ)
    if now.hour < ALERT_HOUR:
        return 0
    propia = db is None
    db = db or SessionLocal()
    enviadas = 0
    try:
        day = now.date().isoformat()
        for b in db.query(Branch).filter(Branch.active == True).all():  # noqa: E712
            if b.code == "CAT" or _already_sent(db, b.id, day):
                continue
            rows = low_stock_rows(db, b.id)
            if not rows:
                continue
            detalle = ", ".join(f"{r['name']} ({r['stock']:g} de {r['min_quantity']:g} {r['unit']})" for r in rows[:4])
            if len(rows) > 4:
                detalle += f" y {len(rows) - 4} más"
            n = len(rows)
            notify_branch_staff(
                db, b.id, f"Stock bajo · {b.name}", f"{n} insumo{'s' if n != 1 else ''} bajo el mínimo: {detalle}",
                f"/abastecimiento?tab=sugerido&branch={b.id}", tag=f"fh-lowstock-{b.id}", managers_only=True,
            )
            log_audit_event(db, None, b.id, "alert.low_stock", "branch", b.id, {"date": day, "items": n, "detail": detalle[:300]})
            db.commit()
            enviadas += 1
        return enviadas
    finally:
        if propia:
            db.close()


async def run_low_stock_loop() -> None:
    await asyncio.sleep(STARTUP_DELAY_SECONDS)
    while True:
        try:
            await asyncio.to_thread(send_if_due)
        except Exception:
            logger.exception("[SupplyAlerts] Error al revisar el stock bajo.")
        await asyncio.sleep(CHECK_INTERVAL_SECONDS)
