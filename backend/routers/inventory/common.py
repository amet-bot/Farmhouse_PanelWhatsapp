"""
Inventario · Imports, router, constantes y helpers compartidos por todo el paquete.

Parte del paquete routers/inventory (antes un solo archivo de 3 000 líneas). Todos los
endpoints se registran en el mismo `router`, así que las rutas no cambian.
"""
import logging
from decimal import Decimal
from typing import List, Optional

from fastapi import APIRouter

from models.shipment import Shipment, ShipmentItem
from schemas.inventory import (
    ShipmentPhotoResponse, ShipmentResponse, ShipmentItemResponse,
)


logger = logging.getLogger("farmhouse.inventory")


router = APIRouter(prefix="/inventory", tags=["Inventario"])


# Vocabulario de la merma. Lista cerrada y no texto libre porque el sentido del módulo es poder
# decir "este mes se perdió tanto por vencimiento": con motivos escritos a mano cada sucursal
# inventa el suyo y no suma nada. "Otro" existe para lo que no entra, y la nota recoge el detalle.
# El orden es el que se ve en el formulario: primero lo que más pasa.
WASTE_REASONS = (
    # Primero porque es lo más común en cocina: lo que se saca al limpiar (piel y grasa del pollo,
    # cáscaras). Es merma de PROCESO, esperada; el resto de la lista es merma evitable, y el
    # análisis las separa (ver PROCESS_WASTE_REASONS).
    ("recorte", "Residuo o recorte al limpiar"),
    ("vencido", "Vencido"),
    ("danado", "Dañado o golpeado"),
    ("error_preparacion", "Error de preparación"),
    ("derrame", "Derrame o rotura"),
    ("devolucion", "Devolución de cliente"),
    ("consumo_interno", "Consumo interno"),
    ("faltante", "Faltante o robo"),
    ("otro", "Otro"),
)

WASTE_REASON_LABELS = dict(WASTE_REASONS)

# Merma de proceso: parte de preparar el insumo, se mide contra lo que se limpió (rendimiento).
PROCESS_WASTE_REASONS = {"recorte"}



def _reclamo_de_linea(line: ShipmentItem) -> Optional[Decimal]:
    """Lo que hay que reclamarle al proveedor por un renglón: (facturado − llegó) × costo."""
    if line.invoiced_quantity is None or line.unit_cost is None:
        return None
    falta = Decimal(line.invoiced_quantity) - Decimal(line.quantity)
    if falta <= 0:
        return None
    return (falta * Decimal(line.unit_cost)).quantize(Decimal("0.01"))



def _linea_con_problema(line: ShipmentItem) -> bool:
    return line.line_status not in (None, "ok")



def _serialize_shipment(shipment: Shipment) -> ShipmentResponse:
    items: List[ShipmentItemResponse] = []
    total_cost = Decimal("0.00")
    has_cost = False
    reclamo_total = Decimal("0")
    for line in shipment.items:
        if line.unit_cost is not None:
            total_cost += (Decimal(line.quantity) * Decimal(line.unit_cost))
            has_cost = True
        reclamo = _reclamo_de_linea(line)
        reclamo_total += reclamo or Decimal("0")
        items.append(ShipmentItemResponse(
            id=line.id,
            inventory_item_id=line.inventory_item_id,
            item_name=line.inventory_item.name,
            unit=line.inventory_item.unit,
            quantity=line.quantity,
            unit_cost=line.unit_cost,
            invoiced_quantity=line.invoiced_quantity,
            line_status=line.line_status,
            line_note=line.line_note,
            claim_value=reclamo,
        ))
    return ShipmentResponse(
        id=shipment.id,
        branch_id=shipment.branch_id,
        branch_name=shipment.branch.name,
        received_by_user_id=shipment.received_by_user_id,
        received_by_name=shipment.received_by_user.name,
        received_at=shipment.received_at,
        supplier_id=shipment.supplier_id,
        supplier_name=(shipment.supplier.name if shipment.supplier else None),
        notes=shipment.notes,
        created_at=shipment.created_at,
        items=items,
        total_cost=total_cost.quantize(Decimal("0.01")) if has_cost else None,
        invoice_number=shipment.invoice_number,
        has_issues=shipment.has_issues,
        issues_count=sum(1 for l in shipment.items if _linea_con_problema(l)),
        claim_total=(reclamo_total.quantize(Decimal("0.01")) if reclamo_total else None),
        incident_id=shipment.incident_id,
        expected_shipment_id=(shipment.expected_shipment.id if shipment.expected_shipment else None),
        photos=[
            ShipmentPhotoResponse(
                id=p.id, content_type=p.content_type, size_bytes=p.size_bytes,
                uploaded_by_name=None, created_at=p.created_at,
            ) for p in shipment.photos
        ],
    )
