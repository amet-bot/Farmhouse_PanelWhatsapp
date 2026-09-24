from pydantic import BaseModel, Field
from decimal import Decimal
from typing import Dict, List, Optional
from datetime import date, datetime


class LinkBranchSyncStatus(BaseModel):
    """Cómo va la sincronización de ventas de una sucursal."""
    branch_id: int
    branch_code: str
    branch_name: str
    configured: bool                        # tiene usuario de API de Invu en el servidor
    days_synced: int = 0
    first_day: Optional[date] = None
    last_day: Optional[date] = None
    last_synced_at: Optional[datetime] = None
    mismatched_days: int = 0                # días donde nuestro total no cuadra con el de Invu
    error_days: int = 0
    last_error: Optional[str] = None
    menu_items: int = 0                     # platos activos copiados de su menú


class LinkSyncStatusResponse(BaseModel):
    configured: bool                        # al menos una sucursal con credenciales
    branches: List[LinkBranchSyncStatus]


class LinkSyncRequest(BaseModel):
    """
    Sincronizar a mano. Sin fechas trae hoy y ayer; con fechas, hasta 7 días por pedido (cada
    día son dos llamadas a Invu y la respuesta espera a que terminen). El historial largo lo va
    trayendo solo el loop en segundo plano.
    """
    branch_id: Optional[int] = None
    date_from: Optional[date] = None
    date_to: Optional[date] = None
    include_menu: bool = False


class LinkSyncDayResult(BaseModel):
    business_date: date
    orders_count: int = 0
    net_total: Optional[Decimal] = None
    invu_total: Optional[Decimal] = None
    matches: Optional[bool] = None
    error: Optional[str] = None


class LinkSyncBranchResult(BaseModel):
    branch_id: int
    branch_code: str
    menu: Optional[Dict[str, int]] = None
    days: List[LinkSyncDayResult]


class LinkDailySalesRow(BaseModel):
    branch_id: int
    branch_code: str
    branch_name: str
    business_date: date
    orders_count: int
    net_total: Optional[Decimal] = None     # cerradas menos notas de crédito
    invu_total: Optional[Decimal] = None
    matches: Optional[bool] = None
    items_sold: Decimal = Field(default=Decimal("0"))


class LinkItemSalesRow(BaseModel):
    """Un plato, sumado entre las sucursales pedidas. Se agrupa por código de Invu."""
    code: Optional[str] = None
    name: str
    category: Optional[str] = None
    quantity: Decimal
    revenue: Decimal
    branches: int                           # en cuántas sucursales se vendió
