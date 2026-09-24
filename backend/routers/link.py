"""
Farmhouse Link: ventas de la caja cruzadas con el inventario (como el Retail Link de Walmart,
pero para la casa, no para los proveedores).

Etapa 1: el estado de la sincronización con Invu, sincronizar a mano y las dos consultas de
ventas que todo lo demás va a usar (por día y por plato).

Es información de gerencia: solo admin y supervisores. Un supervisor atado a una sucursal ve
la suya; el admin y el supervisor global ven todas o eligen una.
"""
import logging
from datetime import date, timedelta
from decimal import Decimal
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import case, func
from sqlalchemy.orm import Session

from config import settings
from database import get_db
from models.branch import Branch
from models.invu_sales import InvuMenuItem, InvuSale, InvuSaleLine, InvuSyncDay
from models.user import User
from schemas.link import (
    LinkBranchSyncStatus, LinkChannelSalesRow, LinkDailySalesRow, LinkItemSalesRow,
    LinkSyncBranchResult, LinkSyncDayResult, LinkSyncRequest, LinkSyncStatusResponse,
)
from services import invu_client, invu_sales_sync
from security.auth import get_current_authorized_user

logger = logging.getLogger("farmhouse.link")

router = APIRouter(prefix="/link", tags=["Farmhouse Link"])

# Tope de días por sincronización manual: la respuesta espera a Invu, dos llamadas por día.
MAX_DIAS_SYNC_MANUAL = 7
# Tope de rango en las consultas: un año alcanza para cualquier comparación que tenga sentido.
MAX_DIAS_CONSULTA = 366


def _gerencia(current_user: User = Depends(get_current_authorized_user)) -> User:
    if current_user.role not in ("admin", "supervisor"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Farmhouse Link es solo para administradores y supervisores.",
        )
    return current_user


def _es_global(user: User) -> bool:
    return user.role == "admin" or (user.role == "supervisor" and user.branch_id is None)


def _sucursal_visible(user: User, branch_id: Optional[int]) -> Optional[int]:
    """Mismo criterio que Inventario: el global elige (None = todas), el local ve la suya."""
    return branch_id if _es_global(user) else user.branch_id


def _rango(date_from: Optional[date], date_to: Optional[date], por_defecto: int = 7):
    hasta = date_to or invu_sales_sync.hoy_panama()
    desde = date_from or (hasta - timedelta(days=por_defecto - 1))
    if desde > hasta:
        raise HTTPException(status_code=422, detail="La fecha inicial es posterior a la final.")
    if (hasta - desde).days + 1 > MAX_DIAS_CONSULTA:
        raise HTTPException(status_code=422, detail=f"El rango no puede pasar de {MAX_DIAS_CONSULTA} días.")
    return desde, hasta


# ==========================================================================
# Sincronización
# ==========================================================================
@router.get("/invu/status", response_model=LinkSyncStatusResponse)
def sync_status(
    db: Session = Depends(get_db),
    current_user: User = Depends(_gerencia),
):
    """Por sucursal: si tiene credenciales, qué días hay, si cuadran con Invu y el último error."""
    configuradas = set(settings.invu_branch_credentials())
    codigos = settings.INVU_SALES_BRANCH_CODES
    visible = _sucursal_visible(current_user, None)

    consulta = db.query(Branch).filter(Branch.code.in_(codigos))
    if visible is not None:
        consulta = consulta.filter(Branch.id == visible)

    filas = []
    for branch in sorted(consulta.all(), key=lambda b: codigos.index(b.code)):
        dias = db.query(
            func.count(InvuSyncDay.id),
            func.min(InvuSyncDay.business_date),
            func.max(InvuSyncDay.business_date),
            func.max(InvuSyncDay.synced_at),
        ).filter(InvuSyncDay.branch_id == branch.id, InvuSyncDay.error.is_(None)).one()
        ultimo_error = db.query(InvuSyncDay).filter(
            InvuSyncDay.branch_id == branch.id, InvuSyncDay.error.isnot(None)
        ).order_by(InvuSyncDay.synced_at.desc()).first()

        filas.append(LinkBranchSyncStatus(
            branch_id=branch.id,
            branch_code=branch.code,
            branch_name=branch.name,
            configured=branch.code in configuradas,
            days_synced=dias[0] or 0,
            first_day=dias[1],
            last_day=dias[2],
            last_synced_at=dias[3],
            mismatched_days=db.query(func.count(InvuSyncDay.id)).filter(
                InvuSyncDay.branch_id == branch.id, InvuSyncDay.matches == False
            ).scalar() or 0,
            error_days=db.query(func.count(InvuSyncDay.id)).filter(
                InvuSyncDay.branch_id == branch.id, InvuSyncDay.error.isnot(None)
            ).scalar() or 0,
            last_error=ultimo_error.error if ultimo_error else None,
            menu_items=db.query(func.count(InvuMenuItem.id)).filter(
                InvuMenuItem.branch_id == branch.id, InvuMenuItem.active == True
            ).scalar() or 0,
        ))

    return LinkSyncStatusResponse(configured=bool(configuradas), branches=filas)


@router.post("/invu/sync", response_model=List[LinkSyncBranchResult])
def sync_now(
    payload: LinkSyncRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(_gerencia),
):
    """Trae de Invu los días pedidos (o hoy y ayer), ahora mismo. Solo el admin."""
    if current_user.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Solo el administrador puede sincronizar.")

    sucursales = invu_sales_sync.sucursales_configuradas(db)
    if not sucursales:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Ninguna sucursal tiene usuario de API de Invu configurado en el servidor.",
        )
    if payload.branch_id is not None:
        sucursales = [(b, c) for b, c in sucursales if b.id == payload.branch_id]
        if not sucursales:
            raise HTTPException(status_code=404, detail="Esa sucursal no tiene usuario de API de Invu.")

    hoy = invu_sales_sync.hoy_panama()
    hasta = payload.date_to or hoy
    desde = payload.date_from or (hasta - timedelta(days=1))
    if desde > hasta:
        raise HTTPException(status_code=422, detail="La fecha inicial es posterior a la final.")
    if hasta > hoy:
        raise HTTPException(status_code=422, detail="No se pueden sincronizar días futuros.")
    if (hasta - desde).days + 1 > MAX_DIAS_SYNC_MANUAL:
        raise HTTPException(
            status_code=422,
            detail=f"Hasta {MAX_DIAS_SYNC_MANUAL} días por sincronización manual; el historial lo trae el proceso automático.",
        )

    resultados = []
    for branch, credenciales in sucursales:
        menu = None
        if payload.include_menu:
            try:
                menu = invu_sales_sync.sync_menu(db, branch, credenciales)
            except invu_client.InvuError as e:
                db.rollback()
                raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"{branch.name}: {e}")

        dias = []
        dia = hasta
        while dia >= desde:
            try:
                r = invu_sales_sync.sync_day(db, branch, credenciales, dia)
                dias.append(LinkSyncDayResult(
                    business_date=dia, orders_count=r.orders_count, net_total=r.net_total,
                    invu_total=r.invu_total, matches=r.matches,
                ))
            except invu_client.InvuError as e:
                invu_sales_sync._anotar_error(db, branch, dia, str(e))
                dias.append(LinkSyncDayResult(business_date=dia, error=str(e)))
            dia -= timedelta(days=1)

        resultados.append(LinkSyncBranchResult(branch_id=branch.id, branch_code=branch.code, menu=menu, days=dias))

    logger.info(f"Sincronización de ventas pedida por {current_user.name}: {desde} a {hasta}")
    return resultados


# ==========================================================================
# Ventas
# ==========================================================================
@router.get("/sales/daily", response_model=List[LinkDailySalesRow])
def daily_sales(
    date_from: Optional[date] = Query(None),
    date_to: Optional[date] = Query(None),
    branch_id: Optional[int] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(_gerencia),
):
    """Un renglón por sucursal y día sincronizado: órdenes, venta neta y platos vendidos."""
    desde, hasta = _rango(date_from, date_to)
    visible = _sucursal_visible(current_user, branch_id)

    dias_q = db.query(InvuSyncDay, Branch).join(Branch, Branch.id == InvuSyncDay.branch_id).filter(
        InvuSyncDay.business_date >= desde,
        InvuSyncDay.business_date <= hasta,
        InvuSyncDay.error.is_(None),
    )
    platos_q = db.query(
        InvuSaleLine.branch_id, InvuSaleLine.business_date, func.coalesce(func.sum(InvuSaleLine.quantity), 0)
    ).filter(
        InvuSaleLine.business_date >= desde,
        InvuSaleLine.business_date <= hasta,
        InvuSaleLine.counted == True,
    )
    if visible is not None:
        dias_q = dias_q.filter(InvuSyncDay.branch_id == visible)
        platos_q = platos_q.filter(InvuSaleLine.branch_id == visible)

    platos = {
        (b, d): Decimal(q)
        for b, d, q in platos_q.group_by(InvuSaleLine.branch_id, InvuSaleLine.business_date).all()
    }

    return [
        LinkDailySalesRow(
            branch_id=branch.id,
            branch_code=branch.code,
            branch_name=branch.name,
            business_date=dia.business_date,
            orders_count=dia.orders_count,
            net_total=dia.net_total,
            invu_total=dia.invu_total,
            matches=dia.matches,
            items_sold=platos.get((branch.id, dia.business_date), Decimal("0")),
        )
        for dia, branch in dias_q.order_by(InvuSyncDay.business_date.desc(), Branch.name.asc()).all()
    ]


@router.get("/sales/items", response_model=List[LinkItemSalesRow])
def item_sales(
    date_from: Optional[date] = Query(None),
    date_to: Optional[date] = Query(None),
    branch_id: Optional[int] = Query(None),
    limit: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
    current_user: User = Depends(_gerencia),
):
    """
    Platos más vendidos en el rango, por cantidad. Se agrupa por código de Invu, que es el mismo
    en todas las sucursales aunque el id interno cambie.
    """
    desde, hasta = _rango(date_from, date_to)
    visible = _sucursal_visible(current_user, branch_id)

    clave = func.coalesce(InvuSaleLine.code, InvuSaleLine.name)
    consulta = db.query(
        clave.label("clave"),
        func.max(InvuSaleLine.code).label("code"),
        func.max(InvuSaleLine.name).label("name"),
        func.max(InvuSaleLine.category).label("category"),
        func.sum(InvuSaleLine.quantity).label("quantity"),
        func.coalesce(func.sum(InvuSaleLine.total), 0).label("revenue"),
        func.count(func.distinct(InvuSaleLine.branch_id)).label("branches"),
    ).filter(
        InvuSaleLine.business_date >= desde,
        InvuSaleLine.business_date <= hasta,
        InvuSaleLine.counted == True,
    )
    if visible is not None:
        consulta = consulta.filter(InvuSaleLine.branch_id == visible)

    filas = consulta.group_by(clave).order_by(func.sum(InvuSaleLine.quantity).desc()).limit(limit).all()
    return [
        LinkItemSalesRow(
            code=f.code,
            name=f.name,
            category=f.category,
            quantity=Decimal(f.quantity).quantize(Decimal("0.001")),
            revenue=Decimal(f.revenue).quantize(Decimal("0.01")),
            branches=f.branches,
        )
        for f in filas
    ]


@router.get("/sales/channels", response_model=List[LinkChannelSalesRow])
def channel_sales(
    date_from: Optional[date] = Query(None),
    date_to: Optional[date] = Query(None),
    branch_id: Optional[int] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(_gerencia),
):
    """
    Cuánto entra por cada tipo de orden. La nota de crédito resta en el tipo de su orden: una
    devolución de Pedidos Ya baja Pedidos Ya, no el salón.
    """
    desde, hasta = _rango(date_from, date_to)
    visible = _sucursal_visible(current_user, branch_id)

    tipo = func.coalesce(InvuSale.order_type, "Sin tipo")
    signo = case((InvuSale.is_credit_note == True, -1), else_=1)
    consulta = db.query(
        tipo.label("tipo"),
        func.sum(case((InvuSale.is_credit_note == True, 0), else_=1)).label("ordenes"),
        func.coalesce(func.sum(signo * func.coalesce(InvuSale.total, 0)), 0).label("neto"),
    ).filter(InvuSale.business_date >= desde, InvuSale.business_date <= hasta)
    if visible is not None:
        consulta = consulta.filter(InvuSale.branch_id == visible)

    return [
        LinkChannelSalesRow(order_type=f.tipo, orders=int(f.ordenes or 0),
                            net_total=Decimal(f.neto).quantize(Decimal("0.01")))
        for f in consulta.group_by(tipo).order_by(func.sum(signo * func.coalesce(InvuSale.total, 0)).desc()).all()
    ]
