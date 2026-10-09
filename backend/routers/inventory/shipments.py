"""
Inventario · Cargamentos recibidos: registro, borrado, insights.

Parte del paquete routers/inventory (antes un solo archivo de 3 000 líneas). Todos los
endpoints se registran en el mismo `router`, así que las rutas no cambian.
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import List, Optional

from fastapi import BackgroundTasks, Depends, HTTPException, Query, status
from sqlalchemy import func
from sqlalchemy.orm import Session, selectinload

from database import get_db
from models.branch import Branch
from models.inventory_item import InventoryItem
from models.supplier import Supplier
from models.ops import Incident
from models.shipment import ExpectedShipment, Shipment, ShipmentItem
from models.user import User
from models.inventory_movement import InventoryMovement
from schemas.inventory import (
    SHIPMENT_LINE_STATUSES, ShipmentCreate, ShipmentResponse, ShipmentInsightItem, ShipmentInsights,
)
from services import invu_sales_sync
from services.audit import log_audit_event
from security.auth import get_current_authorized_user
from security.access_control import check_target_branch_valid

from .common import _linea_con_problema, _reclamo_de_linea, _serialize_shipment, logger, router
from .helpers import _avisar_diferencias_background, _chequear_quien_borra, _chequear_sin_conteo_posterior, _describir_problema, _dias_utc, _existencia_map, _hay_a_quien_avisar, _insumos_con_receta, _uso_por_ventas, _visible_branch_filter



@router.post("/shipments", response_model=ShipmentResponse, status_code=status.HTTP_201_CREATED)
def create_shipment(
    shipment_in: ShipmentCreate,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_authorized_user),
):
    """
    Registra lo que llegó. Recibir contra factura es opcional por renglón: con lo facturado, el
    servidor deduce si faltó o sobró; "equivocado" y "dañado" los marca quien recibe. `quantity`
    es siempre lo que entró de verdad a la existencia (puede ser 0 si no llegó nada de eso).

    Si algún renglón no llegó como decía la factura se abre sola una incidencia en Operación de
    Sucursal y se avisa por notificación a los encargados, sin depender del grupo de WhatsApp.
    """
    # Igual que conversations.py: agente y supervisor local solo su propia sucursal (Punto 3).
    if current_user.role == "agent":
        if shipment_in.branch_id != current_user.branch_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="No tienes permiso para registrar cargamentos en otra sucursal."
            )
    elif current_user.role == "supervisor" and current_user.branch_id:
        if shipment_in.branch_id != current_user.branch_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="No tienes permiso para registrar cargamentos en otra sucursal."
            )

    check_target_branch_valid(db, shipment_in.branch_id)

    if shipment_in.supplier_id is not None:
        supplier = db.query(Supplier).filter(Supplier.id == shipment_in.supplier_id).first()
        if not supplier:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="El proveedor seleccionado no existe."
            )

    item_ids = [line.inventory_item_id for line in shipment_in.items]
    found_items = db.query(InventoryItem).filter(InventoryItem.id.in_(item_ids)).all()
    missing = set(item_ids) - {i.id for i in found_items}
    if missing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Ítem(s) de inventario no encontrados: {sorted(missing)}"
        )

    esperado = None
    if shipment_in.expected_shipment_id is not None:
        esperado = db.query(ExpectedShipment).filter(ExpectedShipment.id == shipment_in.expected_shipment_id).first()
        if not esperado or esperado.branch_id != shipment_in.branch_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="El cargamento agendado no existe en esta sucursal.")
        if esperado.status != "pendiente":
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Ese cargamento agendado ya se recibió o se canceló.")

    nombres = {i.id: i.name for i in found_items}
    estados: List[Optional[str]] = []
    for line in shipment_in.items:
        nombre = nombres[line.inventory_item_id]
        estado = (line.line_status or "").strip().lower() or None
        if estado is not None and estado not in SHIPMENT_LINE_STATUSES:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"{nombre}: estado no válido.")
        if estado in ("equivocado", "danado"):
            pass
        elif line.invoiced_quantity is not None:
            q, fac = Decimal(line.quantity), Decimal(line.invoiced_quantity)
            estado = "falto" if q < fac else ("sobro" if q > fac else "ok")
        elif estado in ("falto", "sobro"):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                                detail=f"{nombre}: para decir que faltó o sobró, anotá cuánto dice la factura.")
        if Decimal(line.quantity) == 0 and estado not in ("falto", "equivocado", "danado"):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                                detail=f"{nombre}: la cantidad que llegó no puede ser cero.")
        estados.append(estado)
    comparado = bool(shipment_in.invoice_number) or any(e is not None for e in estados)

    shipment = Shipment(
        branch_id=shipment_in.branch_id,
        received_by_user_id=current_user.id,
        received_at=shipment_in.received_at or datetime.now(timezone.utc),
        supplier_id=shipment_in.supplier_id,
        notes=(shipment_in.notes or None),
        invoice_number=((shipment_in.invoice_number or "").strip() or None),
    )
    for line, estado in zip(shipment_in.items, estados):
        shipment.items.append(ShipmentItem(
            inventory_item_id=line.inventory_item_id,
            quantity=line.quantity,
            unit_cost=line.unit_cost,
            invoiced_quantity=line.invoiced_quantity,
            line_status=(estado or ("ok" if comparado else None)),
            line_note=((line.line_note or "").strip() or None),
        ))
    problemas = [l for l in shipment.items if _linea_con_problema(l)]
    shipment.has_issues = bool(problemas) if comparado else None

    db.add(shipment)
    db.flush()  # asigna shipment.id antes de generar los movimientos del libro (Fase 4)
    for line in shipment.items:
        if Decimal(line.quantity) == 0:
            continue   # no llegó nada de eso: no hay movimiento que anotar
        db.add(InventoryMovement(
            branch_id=shipment.branch_id,
            inventory_item_id=line.inventory_item_id,
            movement_type="in",
            quantity=line.quantity,
            unit_cost=line.unit_cost,
            occurred_at=shipment.received_at,
            source_type="shipment",
            source_id=shipment.id,
            created_by_user_id=current_user.id,
        ))
    aviso = None
    if problemas:
        proveedor = db.query(Supplier.name).filter(Supplier.id == shipment.supplier_id).scalar() if shipment.supplier_id else None
        lineas = [_describir_problema(l) for l in problemas]
        reclamo = sum((_reclamo_de_linea(l) or Decimal("0") for l in problemas), Decimal("0"))
        grave = any(l.line_status in ("equivocado", "danado") for l in problemas) or reclamo >= Decimal("50")
        titulo = f"Cargamento con diferencias{f' · {proveedor}' if proveedor else ''}"[:150]
        detalle = "\n".join(f"- {x}" for x in lineas)
        if shipment.invoice_number:
            detalle = f"Factura {shipment.invoice_number}\n" + detalle
        if reclamo:
            detalle += f"\nPara reclamar: ${reclamo.quantize(Decimal('0.01'))}"
        incidencia = Incident(
            branch_id=shipment.branch_id, reported_by_user_id=current_user.id,
            title=titulo, description=f"Cargamento #{shipment.id}\n{detalle}",
            severity=("alta" if grave else "media"),
        )
        db.add(incidencia)
        db.flush()
        shipment.incident_id = incidencia.id
        sucursal = db.query(Branch.name).filter(Branch.id == shipment.branch_id).scalar()
        aviso = (
            f"Cargamento con diferencias · {sucursal}",
            f"{proveedor + ': ' if proveedor else ''}" + "; ".join(lineas),
            f"/inventario?view=cargamentos&shipment={shipment.id}",
            f"fh-shipment-{shipment.id}",
        )
    if esperado is not None:
        esperado.status = "recibido"
        esperado.shipment_id = shipment.id

    log_audit_event(
        db, current_user.id, shipment.branch_id, "shipment.create", "shipment", shipment.id,
        {"items": len(shipment.items), "supplier_id": shipment.supplier_id,
         "has_issues": shipment.has_issues, "incident_id": shipment.incident_id,
         "expected_shipment_id": esperado.id if esperado else None}
    )
    db.commit()
    db.refresh(shipment)
    logger.info(f"Cargamento #{shipment.id} registrado en sucursal {shipment.branch_id} por {current_user.name}"
                + (f" con {len(problemas)} diferencia(s)" if problemas else ""))
    respuesta = _serialize_shipment(shipment)
    respuesta.insights = _shipment_insights(db, shipment)
    if aviso is not None:
        respuesta.notified = _hay_a_quien_avisar(db, shipment.branch_id)
        background_tasks.add_task(_avisar_diferencias_background, shipment.branch_id, *aviso)
    return respuesta



def _shipment_insights(db: Session, shipment: Shipment) -> ShipmentInsights:
    """
    Pone el cargamento en contexto. Por insumo: el precio contra la compra anterior en esta
    sucursal, contra lo más barato que pagó otra sucursal hace poco y contra el costo de Invu; la
    existencia de hoy y, con recetas, para cuántos días alcanza. Y el gasto: la semana de la
    sucursal contra la anterior y el mes con este proveedor. Como en la merma, "hoy" es el día en
    que se recibió.
    """
    tz = invu_sales_sync.PANAMA_TZ
    recibido = shipment.received_at if shipment.received_at.tzinfo else shipment.received_at.replace(tzinfo=timezone.utc)
    dia = recibido.astimezone(tz).date()
    semana = _dias_utc(dia - timedelta(days=6), dia)
    previa = _dias_utc(dia - timedelta(days=13), dia - timedelta(days=7))
    mes = _dias_utc(dia - timedelta(days=29), dia)
    noventa = _dias_utc(dia - timedelta(days=89), dia)
    recibido_utc = recibido.astimezone(timezone.utc).replace(tzinfo=None)

    def gasto(rango, *extra):
        valor = db.query(func.coalesce(func.sum(ShipmentItem.quantity * ShipmentItem.unit_cost), 0)).join(
            Shipment, Shipment.id == ShipmentItem.shipment_id
        ).filter(
            Shipment.branch_id == shipment.branch_id,
            Shipment.received_at >= rango[0], Shipment.received_at < rango[1],
            ShipmentItem.unit_cost.isnot(None), *extra,
        ).scalar()
        return Decimal(valor or 0).quantize(Decimal("0.01"))

    item_ids = [l.inventory_item_id for l in shipment.items]
    existencias = _existencia_map(db, shipment.branch_id, item_ids)
    ahora = datetime.now(timezone.utc).replace(tzinfo=None)
    uso14 = _uso_por_ventas(db, shipment.branch_id, ahora - timedelta(days=14), ahora)
    con_receta = _insumos_con_receta(db, shipment.branch_id)

    items: List[ShipmentInsightItem] = []
    sin_costo = 0
    for line in shipment.items:
        item = line.inventory_item
        costo = Decimal(line.unit_cost) if line.unit_cost is not None else None
        if costo is None:
            sin_costo += 1

        anterior = db.query(ShipmentItem, Shipment).join(Shipment, Shipment.id == ShipmentItem.shipment_id).filter(
            Shipment.branch_id == shipment.branch_id,
            ShipmentItem.inventory_item_id == item.id,
            ShipmentItem.unit_cost.isnot(None),
            Shipment.id != shipment.id,
            Shipment.received_at <= recibido_utc,
        ).order_by(Shipment.received_at.desc(), Shipment.id.desc()).first()
        prev_costo = Decimal(anterior[0].unit_cost) if anterior else None
        cambio = ((costo - prev_costo) / prev_costo * 100).quantize(Decimal("0.1")) if (costo and prev_costo) else None

        # Lo último que pagó cada otra sucursal en 90 días; se queda con lo más barato.
        otras = db.query(ShipmentItem, Shipment).join(Shipment, Shipment.id == ShipmentItem.shipment_id).filter(
            Shipment.branch_id != shipment.branch_id,
            ShipmentItem.inventory_item_id == item.id,
            ShipmentItem.unit_cost.isnot(None),
            Shipment.received_at >= noventa[0], Shipment.received_at < noventa[1],
        ).order_by(Shipment.received_at.desc(), Shipment.id.desc()).all()
        ultima_por_sucursal: dict = {}
        for si, sh in otras:
            ultima_por_sucursal.setdefault(sh.branch_id, (si, sh))
        mejor = min(ultima_por_sucursal.values(), key=lambda par: Decimal(par[0].unit_cost), default=None)

        stock = Decimal(existencias.get(item.id, Decimal("0")))
        por_dia = (uso14.get(item.id, Decimal("0")) / 14) if item.id in con_receta else None
        items.append(ShipmentInsightItem(
            inventory_item_id=item.id, name=item.name, unit=item.unit,
            quantity=line.quantity, unit_cost=line.unit_cost,
            prev_unit_cost=prev_costo,
            prev_received_at=(anterior[1].received_at if anterior else None),
            prev_supplier=(anterior[1].supplier.name if anterior and anterior[1].supplier else None),
            change_pct=cambio,
            best_other_cost=(Decimal(mejor[0].unit_cost) if mejor else None),
            best_other_branch=(mejor[1].branch.name if mejor else None),
            best_other_supplier=(mejor[1].supplier.name if mejor and mejor[1].supplier else None),
            best_other_received_at=(mejor[1].received_at if mejor else None),
            reference_cost=item.effective_cost,
            stock_now=stock.quantize(Decimal("0.001")),
            used_per_day=(por_dia.quantize(Decimal("0.001")) if por_dia is not None else None),
            days_left=((stock / por_dia).quantize(Decimal("0.1")) if por_dia and por_dia > 0 and stock > 0 else None),
        ))

    # Historial del proveedor, todas las sucursales, 90 días: cuántas entregas comparadas contra
    # factura vinieron con diferencias y cuánto faltó en $.
    prov_total = prov_fallas = None
    prov_reclamo = None
    if shipment.supplier_id:
        comparados = db.query(Shipment).options(selectinload(Shipment.items)).filter(
            Shipment.supplier_id == shipment.supplier_id,
            Shipment.has_issues.isnot(None),
            Shipment.received_at >= noventa[0], Shipment.received_at < noventa[1],
        ).all()
        prov_total = len(comparados)
        prov_fallas = sum(1 for s in comparados if s.has_issues)
        prov_reclamo = sum((_reclamo_de_linea(l) or Decimal("0") for s in comparados for l in s.items),
                           Decimal("0")).quantize(Decimal("0.01"))

    total = None
    if any(l.unit_cost is not None for l in shipment.items):
        total = sum((Decimal(l.quantity) * Decimal(l.unit_cost) for l in shipment.items if l.unit_cost is not None), Decimal("0")).quantize(Decimal("0.01"))
    return ShipmentInsights(
        shipment_id=shipment.id, branch_id=shipment.branch_id, branch_name=shipment.branch.name,
        supplier_name=(shipment.supplier.name if shipment.supplier else None),
        total_cost=total,
        branch_week_spend=gasto(semana), branch_prev_week_spend=gasto(previa),
        supplier_month_spend=(gasto(mes, Shipment.supplier_id == shipment.supplier_id) if shipment.supplier_id else None),
        items_without_cost=sin_costo, items=items,
        supplier_shipments_90d=prov_total, supplier_issues_90d=prov_fallas, supplier_claim_90d=prov_reclamo,
    )



@router.delete("/shipments/{shipment_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_shipment(
    shipment_id: int,
    motivo: Optional[str] = Query(None, max_length=200),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_authorized_user),
):
    """
    Borra un cargamento cargado por error (mal la cantidad, el costo o repetido), con sus líneas y
    sus movimientos del libro: la existencia y las compras vuelven a quedar como si no se hubiera
    cargado. Mismas reglas que la merma: supervisor y admin cualquiera de sus sucursales, quien lo
    registró el suyo durante 24 horas. No se borra si después se contaron esos insumos (ver
    _chequear_sin_conteo_posterior). La auditoría guarda una copia de lo borrado y el motivo.
    """
    shipment = db.query(Shipment).filter(Shipment.id == shipment_id).first()
    if not shipment:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cargamento no encontrado.")
    efectiva = _visible_branch_filter(current_user, shipment.branch_id)
    if efectiva is not None and efectiva != shipment.branch_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No tienes acceso a los cargamentos de otra sucursal.")
    _chequear_quien_borra(current_user, shipment.received_by_user_id, shipment.created_at, "el cargamento")
    _chequear_sin_conteo_posterior(db, shipment.branch_id, [l.inventory_item_id for l in shipment.items],
                                   shipment.created_at, "este cargamento")

    snapshot = {
        "received_at": shipment.received_at.isoformat() if shipment.received_at else None,
        "received_by_user_id": shipment.received_by_user_id,
        "supplier_id": shipment.supplier_id,
        "notes": shipment.notes,
        "items": [
            {"inventory_item_id": l.inventory_item_id, "quantity": str(l.quantity),
             "unit_cost": str(l.unit_cost) if l.unit_cost is not None else None}
            for l in shipment.items
        ],
        "delete_reason": (motivo or "").strip() or None,
    }
    snapshot["invoice_number"] = shipment.invoice_number
    snapshot["incident_id"] = shipment.incident_id
    db.query(InventoryMovement).filter(
        InventoryMovement.source_type == "shipment", InventoryMovement.source_id == shipment.id,
    ).delete(synchronize_session=False)
    # Si se estaba recibiendo un cargamento agendado, vuelve a esperarse.
    db.query(ExpectedShipment).filter(ExpectedShipment.shipment_id == shipment.id).update(
        {ExpectedShipment.status: "pendiente", ExpectedShipment.shipment_id: None}, synchronize_session=False)
    log_audit_event(db, current_user.id, shipment.branch_id, "shipment.delete", "shipment", shipment.id, snapshot)
    db.delete(shipment)   # las líneas se van con él (cascade)
    db.commit()
    logger.info(f"Cargamento #{shipment_id} borrado por {current_user.name}" + (f" — {snapshot['delete_reason']}" if snapshot["delete_reason"] else ""))
    return None



@router.get("/shipments/{shipment_id}/insights", response_model=ShipmentInsights)
def shipment_insights(
    shipment_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_authorized_user),
):
    shipment = db.query(Shipment).filter(Shipment.id == shipment_id).first()
    if not shipment:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cargamento no encontrado.")
    efectiva = _visible_branch_filter(current_user, shipment.branch_id)
    if efectiva is not None and efectiva != shipment.branch_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No tienes acceso a los cargamentos de otra sucursal.")
    return _shipment_insights(db, shipment)



@router.get("/shipments", response_model=List[ShipmentResponse])
def list_shipments(
    branch_id: Optional[int] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_authorized_user),
):
    query = db.query(Shipment)

    if current_user.role == "admin" or (current_user.role == "supervisor" and current_user.branch_id is None):
        if branch_id is not None:
            query = query.filter(Shipment.branch_id == branch_id)
    else:
        query = query.filter(Shipment.branch_id == current_user.branch_id)

    shipments = query.order_by(Shipment.received_at.desc()).offset(offset).limit(limit).all()
    return [_serialize_shipment(s) for s in shipments]
